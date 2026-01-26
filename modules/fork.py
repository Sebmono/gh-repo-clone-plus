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
            # Use a short temp path to avoid Windows 260 char path limit
            temp_dir = tempfile.mkdtemp(prefix="ghm_", dir=os.environ.get('TEMP', None))
            clone_path = os.path.join(temp_dir, "repo")

            try:
                print("   Cloning source repository...")
                clone_url = f"https://github.com/{source_owner}/{source_repo}.git"

                # Clone with all branches (--no-single-branch) so tag commits exist
                # Enable long paths for Windows
                result = subprocess.run(
                    ["git", "clone", "-c", "core.longpaths=true", "--no-single-branch", clone_url, clone_path],
                    capture_output=True,
                    text=True
                )
                if result.returncode != 0:
                    print(f"\n   Git clone stderr: {result.stderr}")
                    raise subprocess.CalledProcessError(
                        result.returncode, "git clone",
                        result.stdout, result.stderr
                    )
                print("   ✓ Clone complete")

                # Get the current branch name (default branch)
                branch_result = subprocess.run(
                    ["git", "branch", "--show-current"],
                    cwd=clone_path,
                    capture_output=True,
                    text=True
                )
                default_branch = branch_result.stdout.strip() or "main"

                # Get list of all remote tracking branches
                branches_result = subprocess.run(
                    ["git", "branch", "-r"],
                    cwd=clone_path,
                    capture_output=True,
                    text=True
                )
                all_branches = []
                if branches_result.returncode == 0:
                    all_branches = [b.strip().replace("origin/", "") for b in branches_result.stdout.strip().split("\n") if b.strip() and "HEAD" not in b]

                # Remove .github folder from ALL branches to avoid workflow/ruleset issues
                # This is necessary because GitHub rejects pushes containing workflow files
                # unless the PAT has the 'workflow' scope
                print(f"   Removing .github folder from all branches ({len(all_branches)} branches)...")
                branches_with_github = 0
                branches_cleaned = 0

                # Process default branch first (already checked out)
                github_dir = os.path.join(clone_path, ".github")
                if os.path.exists(github_dir):
                    branches_with_github += 1
                    shutil.rmtree(github_dir, onerror=_remove_readonly)
                    subprocess.run(["git", "add", "-A"], cwd=clone_path, capture_output=True, text=True)
                    subprocess.run(
                        ["git", "commit", "-m", "Remove .github folder for migration"],
                        cwd=clone_path, capture_output=True, text=True
                    )
                    branches_cleaned += 1

                # Process other branches
                other_branches = [b for b in all_branches if b != default_branch]
                for i, branch in enumerate(other_branches):
                    # Show progress every 100 branches
                    if (i + 1) % 100 == 0:
                        print(f"   Progress: checked {i + 1}/{len(other_branches)} branches...")

                    # Create local branch from remote tracking branch
                    subprocess.run(
                        ["git", "checkout", "-b", branch, f"origin/{branch}"],
                        cwd=clone_path,
                        capture_output=True,
                        text=True
                    )

                    # Check if .github exists on this branch
                    github_dir = os.path.join(clone_path, ".github")
                    if os.path.exists(github_dir):
                        branches_with_github += 1
                        shutil.rmtree(github_dir, onerror=_remove_readonly)
                        subprocess.run(["git", "add", "-A"], cwd=clone_path, capture_output=True, text=True)
                        subprocess.run(
                            ["git", "commit", "-m", "Remove .github folder for migration"],
                            cwd=clone_path, capture_output=True, text=True
                        )
                        branches_cleaned += 1

                # Return to default branch
                subprocess.run(
                    ["git", "checkout", default_branch],
                    cwd=clone_path,
                    capture_output=True,
                    text=True
                )

                print(f"   ✓ Removed .github from {branches_cleaned} branches (found in {branches_with_github} branches)")

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

                # Set the remote URL for pushing
                subprocess.run(
                    ["git", "remote", "set-url", "origin", push_url],
                    cwd=clone_path,
                    capture_output=True,
                    text=True,
                    check=True
                )

                print(f"   Pushing default branch '{default_branch}' and tags...")

                # Push default branch
                push_branch = subprocess.run(
                    ["git", "push", "-u", "origin", default_branch],
                    cwd=clone_path,
                    capture_output=True,
                    text=True
                )
                if push_branch.returncode != 0:
                    print(f"\n   Git push stderr: {push_branch.stderr}")
                    print(f"   Git push stdout: {push_branch.stdout}")
                    raise subprocess.CalledProcessError(
                        push_branch.returncode, f"git push {default_branch}",
                        push_branch.stdout, push_branch.stderr
                    )
                print(f"   ✓ Pushed default branch '{default_branch}'")

                # Push all other branches (so tag commits exist)
                # We already created local branches when removing .github, so push those
                if other_branches:
                    print(f"   Pushing {len(other_branches)} additional branches (for tag commits)...")
                    pushed_count = 0
                    failed_branches = []

                    # Batch branches to avoid command line length limits and improve reliability
                    batch_size = 50
                    total_batches = (len(other_branches) + batch_size - 1) // batch_size

                    for batch_num in range(total_batches):
                        start_idx = batch_num * batch_size
                        end_idx = min(start_idx + batch_size, len(other_branches))
                        batch = other_branches[start_idx:end_idx]

                        # Push local branches (we created them when removing .github)
                        cmd = ["git", "push", "origin"] + batch
                        result = subprocess.run(
                            cmd,
                            cwd=clone_path,
                            capture_output=True,
                            text=True
                        )

                        if result.returncode == 0:
                            pushed_count += len(batch)
                        else:
                            # Log the error for debugging
                            if result.stderr:
                                # Only log first failure in detail to avoid spam
                                if not failed_branches:
                                    print(f"   ⚠ Batch {batch_num + 1} failed: {result.stderr[:200]}")
                            failed_branches.extend(batch)

                        # Show progress every 10 batches
                        if (batch_num + 1) % 10 == 0 or batch_num == total_batches - 1:
                            print(f"   Progress: {min(end_idx, len(other_branches))}/{len(other_branches)} branches pushed...")

                        # Small delay between batches to avoid rate limiting
                        if batch_num < total_batches - 1:
                            time.sleep(0.5)

                    print(f"   ✓ Pushed {pushed_count}/{len(other_branches)} branches")
                    if failed_branches and len(failed_branches) <= 10:
                        print(f"   ⚠ Failed branches: {', '.join(failed_branches)}")

                # Push tags
                push_tags = subprocess.run(
                    ["git", "push", "origin", "--tags"],
                    cwd=clone_path,
                    capture_output=True,
                    text=True
                )
                if push_tags.returncode == 0:
                    print("   ✓ Pushed tags")
                else:
                    print(f"   ⚠ Some tags could not be pushed")

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
