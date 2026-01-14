"""Fork creation functionality."""

import time
from github import Github, GithubException
from modules.rate_limiter import RateLimiter
from modules.state import MigrationState
from config import Config


class RepositoryForker:
    """Handles forking repositories."""

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
        Fork a repository.

        Args:
            source_owner: Owner of the source repository
            source_repo: Name of the source repository
            target_owner: Owner for the forked repository (defaults to authenticated user)
            target_name: Name for the forked repository (defaults to source name)

        Returns:
            The forked repository object

        Raises:
            Exception if fork fails
        """
        step_name = "fork_created"
        if self.state.is_step_completed(step_name):
            print("✓ Fork already created (skipping)")
            target = self.state.state['target_repo']
            return self.github.get_repo(f"{target['owner']}/{target['name']}")

        try:
            print(f"\n📋 Forking repository: {source_owner}/{source_repo}")

            # Get source repository
            source = self.github.get_repo(f"{source_owner}/{source_repo}")
            self.state.set_source_repo(source_owner, source_repo)

            # Determine target owner
            if not target_owner:
                target_owner = self.github.get_user().login

            # Verify access to target owner (especially for organizations)
            authenticated_user = self.github.get_user().login
            if target_owner != authenticated_user:
                # Check if it's an organization and user has access
                try:
                    org = self.github.get_organization(target_owner)
                    # Verify user is a member
                    if not org.has_in_members(self.github.get_user()):
                        print(f"   ⚠ Warning: You may not have permission to create repos in '{target_owner}'")
                except GithubException:
                    print(f"   ⚠ Warning: Could not verify access to '{target_owner}'")

            print(f"   Source: {source.html_url}")
            print(f"   Target owner: {target_owner}")

            # Check if fork already exists
            try:
                existing_fork_name = target_name or source_repo
                existing = self.github.get_repo(f"{target_owner}/{existing_fork_name}")
                if existing.fork and existing.parent and existing.parent.full_name == source.full_name:
                    print(f"✓ Fork already exists at: {existing.html_url}")

                    # Enable issues if not already enabled
                    if not existing.has_issues:
                        print("   Enabling issues on existing fork...")
                        self.rate_limiter.wait_for_write()
                        existing.edit(has_issues=True)
                        print("   ✓ Issues enabled")

                    self.state.set_target_repo(target_owner, existing_fork_name)
                    self.state.mark_step_completed(step_name)
                    return existing
            except GithubException:
                pass  # Fork doesn't exist, continue

            # Create the fork
            self.rate_limiter.wait_for_write()
            print("   Creating fork...")

            # Determine if forking to organization or personal account
            authenticated_user = self.github.get_user().login
            if target_owner and target_owner != authenticated_user:
                # Fork to organization
                try:
                    org = self.github.get_organization(target_owner)
                    print(f"   Forking to organization: {target_owner}")
                    forked_repo = org.create_fork(source)
                except GithubException as e:
                    raise Exception(f"Failed to fork to organization '{target_owner}'. "
                                    f"Ensure you have permission to create repos in this org. Error: {str(e)}")
            else:
                # Fork to personal account
                forked_repo = self.github.get_user().create_fork(source)

            # Wait for fork to be ready
            print("   Waiting for fork to be ready...", end='', flush=True)
            max_wait = 60  # Maximum 60 seconds
            waited = 0
            while waited < max_wait:
                try:
                    time.sleep(2)
                    waited += 2
                    print(".", end='', flush=True)
                    # Try to access the fork
                    forked_repo = self.github.get_repo(forked_repo.full_name)
                    if forked_repo.size > 0:  # Fork is ready when it has content
                        break
                except:
                    continue

            print(" Done!")

            # Rename if target_name is specified
            if target_name and target_name != forked_repo.name:
                print(f"   Renaming fork to: {target_name}")
                self.rate_limiter.wait_for_write()
                forked_repo.edit(name=target_name)
                forked_repo = self.github.get_repo(f"{target_owner}/{target_name}")

            # Enable issues if not already enabled
            if not forked_repo.has_issues:
                print("   Enabling issues on forked repository...")
                self.rate_limiter.wait_for_write()
                forked_repo.edit(has_issues=True)
                print("   ✓ Issues enabled")

            self.state.set_target_repo(forked_repo.owner.login, forked_repo.name)
            self.state.mark_step_completed(step_name)

            print(f"✓ Fork created successfully: {forked_repo.html_url}")
            return forked_repo

        except GithubException as e:
            error_msg = f"Failed to fork repository: {str(e)}"
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
