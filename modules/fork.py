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
        1. Clones the source repository locally (mirror clone)
        2. Creates a new Internal repository in the target organization with Actions disabled
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

            # Clone the source repository locally (mirror clone for all refs)
            # Use a short temp path to avoid Windows 260 char path limit
            temp_dir = tempfile.mkdtemp(prefix="ghm_", dir=os.environ.get('TEMP', None))
            clone_path = os.path.join(temp_dir, "repo.git")

            try:
                print("   Cloning source repository (mirror)...")
                clone_url = f"https://github.com/{source_owner}/{source_repo}.git"

                # Mirror clone gets all refs (branches, tags, etc.)
                result = subprocess.run(
                    ["git", "clone", "-c", "core.longpaths=true", "--mirror", clone_url, clone_path],
                    capture_output=True,
                    text=True
                )
                if result.returncode != 0:
                    print(f"\n   Git clone stderr: {result.stderr}")
                    raise subprocess.CalledProcessError(
                        result.returncode, "git clone --mirror",
                        result.stdout, result.stderr
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

                # Disable GitHub Actions to prevent workflows from running
                print("   Disabling GitHub Actions (prevents workflows from running)...")
                self.rate_limiter.wait_for_write()
                try:
                    # Use the GitHub API to disable Actions
                    # PyGithub doesn't have direct support, so we use the underlying requester
                    new_repo._requester.requestJsonAndCheck(
                        "PUT",
                        f"/repos/{target_owner}/{target_repo_name}/actions/permissions",
                        input={"enabled": False}
                    )
                    print("   ✓ GitHub Actions disabled")
                except Exception as e:
                    print(f"   ⚠ Could not disable Actions: {e}")
                    print("   Note: You may want to manually disable Actions in repository settings")

                # Push all branches and tags to the new repository
                # Note: We can't use --mirror because it tries to push refs/pull/* which are read-only
                print("   Pushing to new repository...")

                # Get the token for authenticated push
                token = Config.GITHUB_TOKEN
                push_url = f"https://{token}@github.com/{target_owner}/{target_repo_name}.git"

                # Push all branches (refs/heads/*)
                print("   Pushing all branches...")
                push_branches = subprocess.run(
                    ["git", "push", push_url, "refs/heads/*:refs/heads/*", "--force"],
                    cwd=clone_path,
                    capture_output=True,
                    text=True
                )
                if push_branches.returncode != 0:
                    # Log errors to file for easier review
                    log_file = os.path.join(os.getcwd(), "migration_push_errors.log")
                    with open(log_file, "w") as f:
                        f.write("=== Branch Push Errors ===\n")
                        f.write(push_branches.stderr or "")
                        f.write("\n")
                    print(f"   ⚠ Some branches failed to push. See: {log_file}")
                else:
                    print("   ✓ Pushed all branches")

                # Push all tags (refs/tags/*)
                print("   Pushing all tags...")
                push_tags = subprocess.run(
                    ["git", "push", push_url, "refs/tags/*:refs/tags/*", "--force"],
                    cwd=clone_path,
                    capture_output=True,
                    text=True
                )
                if push_tags.returncode != 0:
                    log_file = os.path.join(os.getcwd(), "migration_push_errors.log")
                    with open(log_file, "a") as f:
                        f.write("=== Tag Push Errors ===\n")
                        f.write(push_tags.stderr or "")
                        f.write("\n")
                    print(f"   ⚠ Some tags failed to push. See: {log_file}")
                else:
                    print("   ✓ Pushed all tags")

                print("   ✓ Push complete")

            finally:
                # Clean up temp directory (use onerror handler for Windows read-only files)
                if os.path.exists(temp_dir):
                    try:
                        shutil.rmtree(temp_dir, onerror=_remove_readonly)
                        print("   ✓ Cleaned up temporary files")
                    except Exception as cleanup_error:
                        print(f"   ⚠ Warning: Could not clean up temp dir: {cleanup_error}")

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
