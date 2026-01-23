"""Repository cloning and creation functionality.

This module clones a source repository locally and re-pushes it to a new
Internal repository in the target organization. This approach avoids the
GitHub limitation where forks of public repositories must also be public.
"""

import os
import shutil
import stat
import subprocess
import tempfile
import time
from github import Github, GithubException
from modules.rate_limiter import RateLimiter
from modules.state import MigrationState
from config import Config


def _remove_readonly(func, path, excinfo):
    """Error handler for shutil.rmtree to handle read-only files on Windows.

    Git pack files are often read-only, which causes shutil.rmtree to fail.
    This handler makes the file writable and retries the deletion.
    """
    os.chmod(path, stat.S_IWRITE)
    func(path)


class RepositoryForker:
    """Handles cloning repositories and creating Internal copies."""

    def __init__(self, github_client: Github, rate_limiter: RateLimiter, state: MigrationState):
        """
        Initialize repository forker.

        Args:
            github_client: Authenticated GitHub client
            rate_limiter: Rate limiter instance
            state: Migration state manager
        """
        self.github = github_client
        self.rate_limiter = rate_limiter
        self.state = state

    def fork_repository(self, source_owner: str, source_repo: str,
                       target_owner: str = None, target_name: str = None):
        """
        Clone a repository and re-push it as an Internal repository.

        Instead of using GitHub's fork API (which forces public repos to stay public),
        this method:
        1. Clones the source repository locally (bare clone)
        2. Creates a new Internal repository in the target organization
        3. Pushes all branches and tags to the new repository

        Args:
            source_owner: Owner of the source repository
            source_repo: Name of the source repository
            target_owner: Owner for the new repository (defaults to authenticated user)
            target_name: Name for the new repository (defaults to source name)

        Returns:
            The new repository object

        Raises:
            Exception if clone/create/push fails
        """
        step_name = "fork_created"
        if self.state.is_step_completed(step_name):
            print("✓ Repository already created (skipping)")
            target = self.state.state['target_repo']
            return self.github.get_repo(f"{target['owner']}/{target['name']}")

        try:
            print(f"\n📋 Cloning repository: {source_owner}/{source_repo}")

            # Get source repository info
            source = self.github.get_repo(f"{source_owner}/{source_repo}")
            self.state.set_source_repo(source_owner, source_repo)

            # Determine target owner
            if not target_owner:
                target_owner = self.github.get_user().login

            target_repo_name = target_name or source_repo

            print(f"   Source: {source.html_url}")
            print(f"   Target: {target_owner}/{target_repo_name}")

            # Check if target repository already exists
            try:
                existing = self.github.get_repo(f"{target_owner}/{target_repo_name}")
                print(f"✓ Repository already exists at: {existing.html_url}")

                # Enable issues if not already enabled
                if not existing.has_issues:
                    print("   Enabling issues on existing repository...")
                    self.rate_limiter.wait_for_write()
                    existing.edit(has_issues=True)
                    print("   ✓ Issues enabled")

                self.state.set_target_repo(target_owner, target_repo_name)
                self.state.mark_step_completed(step_name)
                return existing
            except GithubException as e:
                if e.status != 404:
                    raise
                # Repository doesn't exist, continue with creation

            # Clone the source repository locally
            temp_dir = tempfile.mkdtemp(prefix="gh_migrate_")
            clone_path = os.path.join(temp_dir, f"{source_repo}.git")

            try:
                print("   Cloning source repository (bare clone)...")
                clone_url = f"https://github.com/{source_owner}/{source_repo}.git"
                result = subprocess.run(
                    ["git", "clone", "--bare", clone_url, clone_path],
                    capture_output=True,
                    text=True,
                    check=True
                )
                print("   ✓ Clone complete")

                # Create new Internal repository in target organization
                self.rate_limiter.wait_for_write()
                print(f"   Creating Internal repository in {target_owner}...")

                authenticated_user = self.github.get_user().login
                if target_owner and target_owner != authenticated_user:
                    # Create in organization with Internal visibility
                    org = self.github.get_organization(target_owner)
                    new_repo = org.create_repo(
                        name=target_repo_name,
                        description=source.description or "",
                        private=False,  # Internal repos are not private
                        has_issues=True,
                        has_wiki=source.has_wiki,
                        has_projects=source.has_projects,
                        visibility="internal"
                    )
                else:
                    # For personal accounts, Internal visibility is not available
                    # Fall back to private repository
                    print("   Note: Internal visibility not available for personal accounts, using private")
                    new_repo = self.github.get_user().create_repo(
                        name=target_repo_name,
                        description=source.description or "",
                        private=True,
                        has_issues=True,
                        has_wiki=source.has_wiki,
                        has_projects=source.has_projects
                    )

                print(f"   ✓ Repository created: {new_repo.html_url}")

                # Push all branches and tags to the new repository
                print("   Pushing to new repository...")

                # Get the token for authenticated push
                token = Config.GITHUB_TOKEN
                push_url = f"https://{token}@github.com/{target_owner}/{target_repo_name}.git"

                # Add the new remote
                add_remote_result = subprocess.run(
                    ["git", "remote", "add", "target", push_url],
                    cwd=clone_path,
                    capture_output=True,
                    text=True
                )
                if add_remote_result.returncode != 0:
                    raise subprocess.CalledProcessError(
                        add_remote_result.returncode,
                        "git remote add",
                        add_remote_result.stdout,
                        add_remote_result.stderr
                    )

                # Get the default branch name from the bare clone
                head_result = subprocess.run(
                    ["git", "symbolic-ref", "HEAD"],
                    cwd=clone_path,
                    capture_output=True,
                    text=True
                )
                if head_result.returncode == 0:
                    # Extract branch name from refs/heads/main -> main
                    default_branch = head_result.stdout.strip().replace("refs/heads/", "")
                else:
                    default_branch = "main"  # Fallback

                print(f"   (pushing default branch '{default_branch}' and tags...)")

                # Push the default branch
                push_branch_result = subprocess.run(
                    ["git", "push", "target", f"refs/heads/{default_branch}:refs/heads/{default_branch}"],
                    cwd=clone_path,
                    capture_output=True,
                    text=True
                )
                if push_branch_result.returncode != 0:
                    print(f"\n   Git push stderr: {push_branch_result.stderr}")
                    print(f"   Git push stdout: {push_branch_result.stdout}")
                    raise subprocess.CalledProcessError(
                        push_branch_result.returncode,
                        f"git push (branch {default_branch})",
                        push_branch_result.stdout,
                        push_branch_result.stderr
                    )
                print(f"   ✓ Pushed default branch '{default_branch}'")

                # Push all tags
                push_tags_result = subprocess.run(
                    ["git", "push", "target", "--tags"],
                    cwd=clone_path,
                    capture_output=True,
                    text=True
                )
                if push_tags_result.returncode != 0:
                    # Tags might fail due to workflow restrictions too, but warn instead of fail
                    print(f"   ⚠ Warning: Some tags could not be pushed: {push_tags_result.stderr[:200]}")
                else:
                    print("   ✓ Pushed tags")

                print("   ✓ Push complete")

            finally:
                # Clean up temp directory (use onerror handler for Windows read-only files)
                # Use try/except to not mask any earlier errors
                if os.path.exists(temp_dir):
                    try:
                        shutil.rmtree(temp_dir, onerror=_remove_readonly)
                        print("   ✓ Cleaned up temporary files")
                    except Exception as cleanup_error:
                        print(f"   ⚠ Warning: Could not clean up temp dir: {cleanup_error}")
                        # Don't re-raise - we don't want cleanup errors to mask push errors

            # Refresh repo object to get updated state
            new_repo = self.github.get_repo(f"{target_owner}/{target_repo_name}")

            self.state.set_target_repo(new_repo.owner.login, new_repo.name)
            self.state.mark_step_completed(step_name)

            print(f"✓ Repository created successfully: {new_repo.html_url}")
            return new_repo

        except subprocess.CalledProcessError as e:
            error_msg = f"Git command failed: {e.stderr or e.stdout or str(e)}"
            self.state.add_error(error_msg)
            raise Exception(error_msg)
        except GithubException as e:
            error_msg = f"Failed to create repository: {str(e)}"
            self.state.add_error(error_msg)
            raise Exception(error_msg)

    def get_repository(self, owner: str, repo: str):
        """
        Get a repository object.

        Args:
            owner: Repository owner
            repo: Repository name

        Returns:
            Repository object
        """
        return self.github.get_repo(f"{owner}/{repo}")
