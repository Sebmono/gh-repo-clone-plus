"""Pull requests migration functionality."""

from github import GithubException
from modules.rate_limiter import RateLimiter
from modules.state import MigrationState
from tqdm import tqdm


class PullRequestMigrator:
    """Handles migrating repository pull requests."""

    def __init__(self, rate_limiter: RateLimiter, state: MigrationState):
        """
        Initialize pull request migrator.

        Args:
            rate_limiter: Rate limiter instance
            state: Migration state manager
        """
        self.rate_limiter = rate_limiter
        self.state = state

    def migrate_pull_requests(self, source_repo, target_repo, limit=None):
        """
        Migrate pull requests from source to target repository.

        Note: This attempts to recreate PRs as actual pull requests.
        For PRs with missing branches, creates placeholder branches.

        Args:
            source_repo: Source repository object
            target_repo: Target repository object
            limit: Maximum number of PRs to migrate (None = all, N = most recent N)

        Returns:
            Number of pull requests migrated
        """
        step_name = "prs_migrated"
        if self.state.is_step_completed(step_name):
            print("✓ Pull requests already migrated (skipping)")
            return len(self.state.state['pr_mapping'])

        try:
            print("\n🔀 Migrating pull requests...")

            # Get pull requests from source
            if limit:
                print(f"   Fetching most recent {limit} pull requests from source repository...")
            else:
                print("   Fetching all pull requests from source repository...")

            all_prs = []
            fetch_count = 0
            for pr in source_repo.get_pulls(state='all', sort='updated', direction='desc'):
                all_prs.append(pr)
                fetch_count += 1
                if fetch_count % 100 == 0:
                    print(f"   Fetched {fetch_count} pull requests so far...")
                # Stop if we've reached the limit
                if limit and fetch_count >= limit:
                    break

            if limit and fetch_count >= limit:
                print(f"   Reached limit of {limit} pull requests")
            print(f"   Found {len(all_prs)} pull requests to migrate")

            if not all_prs:
                print("   No pull requests to migrate")
                self.state.mark_step_completed(step_name)
                return 0

            # Get already migrated PRs from state file
            migrated_prs = set(self.state.get_migrated_prs())

            # Also check target repo for previously migrated PRs (prevents duplicates)
            print("   Checking for previously migrated PRs in target repo...")
            already_migrated = self._find_already_migrated_prs(target_repo, source_repo)
            migrated_prs.update(already_migrated)
            if already_migrated:
                print(f"   Found {len(already_migrated)} previously migrated PRs (will skip)")

            # Sort PRs by number to maintain order
            all_prs.sort(key=lambda x: x.number)

            migrated_count = 0
            converted_count = 0

            # Migrate each PR
            for pr in tqdm(all_prs, desc="   Migrating pull requests"):
                try:
                    # Skip if already migrated
                    if pr.number in migrated_prs:
                        continue

                    # Try to recreate as actual PR
                    success = self._recreate_as_pr(pr, source_repo, target_repo)

                    if success:
                        migrated_count += 1
                    else:
                        # Fallback: Create as issue if PR recreation fails
                        self._create_pr_as_issue(pr, source_repo, target_repo)
                        converted_count += 1

                    self.state.add_pr_mapping(pr.number, pr.number)  # Note: number mapping is best-effort

                except Exception as e:
                    error_msg = f"Unexpected error migrating PR #{pr.number}: {str(e)}"
                    print(f"\n   ⚠ {error_msg}")
                    self.state.add_error(error_msg)
                    continue

            self.state.mark_step_completed(step_name)

            if converted_count > 0:
                print(f"✓ Pull requests migrated: {migrated_count} as PRs, {converted_count} as Issues")
            else:
                print(f"✓ Pull requests migrated: {migrated_count}")

            return migrated_count + converted_count

        except Exception as e:
            error_msg = f"Pull request migration failed: {str(e)}"
            self.state.add_error(error_msg)
            raise Exception(error_msg)

    def _find_already_migrated_prs(self, target_repo, source_repo) -> set:
        """
        Find PRs that were already migrated to the target repo.

        Scans target repo PRs and issues for ones containing the source repo PR URL pattern,
        indicating they were previously migrated (PRs may be recreated as actual PRs or as issues).

        Args:
            target_repo: Target repository object
            source_repo: Source repository object

        Returns:
            Set of original PR numbers that were already migrated
        """
        import re
        already_migrated = set()
        source_pr_pattern = f"{source_repo.html_url}/pull/"

        try:
            # Check existing PRs in target repo
            for pr in target_repo.get_pulls(state='all'):
                if pr.body and source_pr_pattern in pr.body:
                    match = re.search(rf'{re.escape(source_pr_pattern)}(\d+)', pr.body)
                    if match:
                        original_number = int(match.group(1))
                        already_migrated.add(original_number)

            # Also check issues (PRs may have been converted to issues)
            for issue in target_repo.get_issues(state='all'):
                if issue.pull_request:  # Skip actual PRs, already checked above
                    continue
                if issue.body and source_pr_pattern in issue.body:
                    match = re.search(rf'{re.escape(source_pr_pattern)}(\d+)', issue.body)
                    if match:
                        original_number = int(match.group(1))
                        already_migrated.add(original_number)

        except Exception as e:
            print(f"\n   ⚠ Warning: Could not fully scan target repo for existing PRs: {str(e)}")

        return already_migrated

    def _recreate_as_pr(self, pr, source_repo, target_repo) -> bool:
        """
        Attempt to recreate a PR as an actual pull request in the target repo.

        Args:
            pr: Source pull request object
            source_repo: Source repository object
            target_repo: Target repository object

        Returns:
            True if successful, False if should fallback to issue
        """
        try:
            # Check if base branch exists in target
            base_branch = pr.base.ref
            try:
                target_repo.get_branch(base_branch)
            except GithubException:
                print(f"\n      ⚠ Base branch '{base_branch}' not found for PR #{pr.number} (will create as issue)")
                return False

            # Check if head branch exists
            head_branch = pr.head.ref
            head_sha = pr.head.sha

            # Try to create/verify head branch exists in target
            try:
                target_repo.get_branch(head_branch)
                branch_exists = True
            except GithubException:
                # Branch doesn't exist, try to create placeholder
                branch_exists = self._create_placeholder_branch(
                    target_repo, head_branch, head_sha, base_branch
                )

            if not branch_exists:
                print(f"\n      ⚠ Cannot create branch '{head_branch}' for PR #{pr.number} (will create as issue)")
                return False

            # Prepare PR body with attribution
            body = self._format_pr_body(pr, source_repo)

            # Prepare labels
            label_names = [label.name for label in pr.labels]

            # Create PR in target
            self.rate_limiter.wait_for_write()
            new_pr = target_repo.create_pull(
                title=pr.title,
                body=body,
                head=head_branch,
                base=base_branch
            )

            # Add labels
            if label_names:
                self.rate_limiter.wait_for_write()
                new_pr.set_labels(*label_names)

            # Migrate comments
            if pr.comments > 0 or pr.review_comments > 0:
                self._migrate_pr_comments(pr, new_pr)

            # Close PR if original was closed or merged
            if pr.state == 'closed':
                self.rate_limiter.wait_for_write()
                new_pr.edit(state='closed')

            return True

        except GithubException as e:
            # If PR creation fails, return False to fallback to issue
            print(f"\n      ⚠ Failed to create PR #{pr.number} as pull request: {str(e)} (will create as issue)")
            return False

    def _create_placeholder_branch(self, target_repo, branch_name: str, commit_sha: str, base_branch: str) -> bool:
        """
        Create a placeholder branch for a PR that doesn't have its source branch.

        Args:
            target_repo: Target repository object
            branch_name: Name of the branch to create
            commit_sha: SHA of the commit to point the branch to
            base_branch: Name of the base branch to create from if commit doesn't exist

        Returns:
            True if branch was created, False otherwise
        """
        try:
            # First, check if the commit exists in target repo
            try:
                target_repo.get_commit(commit_sha)
                # Commit exists, create branch pointing to it
                self.rate_limiter.wait_for_write()
                target_repo.create_git_ref(
                    ref=f"refs/heads/{branch_name}",
                    sha=commit_sha
                )
                return True
            except GithubException:
                # Commit doesn't exist, create branch from base branch
                print(f"\n      ℹ Creating placeholder branch '{branch_name}' from '{base_branch}'")
                base = target_repo.get_branch(base_branch)
                self.rate_limiter.wait_for_write()
                target_repo.create_git_ref(
                    ref=f"refs/heads/{branch_name}",
                    sha=base.commit.sha
                )
                return True

        except GithubException as e:
            print(f"\n      ⚠ Failed to create placeholder branch '{branch_name}': {str(e)}")
            return False

    def _create_pr_as_issue(self, pr, source_repo, target_repo):
        """
        Create a PR as an issue in the target repo (fallback method).

        Args:
            pr: Source pull request object
            source_repo: Source repository object
            target_repo: Target repository object
        """
        try:
            # Format body as PR-turned-issue
            body = self._format_pr_as_issue_body(pr, source_repo)

            # Prepare labels
            label_names = [label.name for label in pr.labels]

            # Create issue in target
            self.rate_limiter.wait_for_write()
            new_issue = target_repo.create_issue(
                title=f"[PR] {pr.title}",
                body=body,
                labels=label_names
            )

            # Migrate comments
            if pr.comments > 0:
                self._migrate_pr_comments_to_issue(pr, new_issue)

            # Close issue if original PR was closed
            if pr.state == 'closed':
                self.rate_limiter.wait_for_write()
                new_issue.edit(state='closed')

        except GithubException as e:
            error_msg = f"Failed to create PR #{pr.number} as issue: {str(e)}"
            print(f"\n   ⚠ {error_msg}")
            self.state.add_error(error_msg)

    def _format_pr_body(self, pr, source_repo) -> str:
        """
        Format PR body with original metadata.

        Args:
            pr: Source pull request object
            source_repo: Source repository object

        Returns:
            Formatted PR body with attribution
        """
        # Create header with original metadata
        header = f"> **Original PR:** {source_repo.html_url}/pull/{pr.number}\n"
        header += f"> **Opened by:** @{pr.user.login}\n"
        header += f"> **Created at:** {pr.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"

        if pr.merged:
            header += f"> **Merged at:** {pr.merged_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            if pr.merged_by:
                header += f"> **Merged by:** @{pr.merged_by.login}\n"
        elif pr.closed_at:
            header += f"> **Closed at:** {pr.closed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"

        header += f"> **Base branch:** `{pr.base.ref}` → **Head branch:** `{pr.head.ref}`\n"

        if pr.assignees:
            assignees = ", ".join([f"@{a.login}" for a in pr.assignees])
            header += f"> **Original assignees:** {assignees}\n"

        header += "\n---\n\n"

        # Add original body
        body = pr.body or "*No description provided.*"

        return header + body

    def _format_pr_as_issue_body(self, pr, source_repo) -> str:
        """
        Format PR as issue body (when PR can't be recreated).

        Args:
            pr: Source pull request object
            source_repo: Source repository object

        Returns:
            Formatted issue body
        """
        header = f"> **⚠ This was originally a Pull Request (not recreated)**\n"
        header += f"> **Original PR:** {source_repo.html_url}/pull/{pr.number}\n"
        header += f"> **Opened by:** @{pr.user.login}\n"
        header += f"> **Created at:** {pr.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"

        if pr.merged:
            header += f"> **Merged at:** {pr.merged_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        elif pr.closed_at:
            header += f"> **Closed at:** {pr.closed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"

        header += f"> **Base branch:** `{pr.base.ref}` → **Head branch:** `{pr.head.ref}`\n"
        header += "\n---\n\n"

        body = pr.body or "*No description provided.*"

        return header + body

    def _migrate_pr_comments(self, source_pr, target_pr):
        """
        Migrate comments from source to target PR.

        Args:
            source_pr: Source pull request object
            target_pr: Target pull request object
        """
        try:
            # Migrate issue comments (general PR comments)
            comments = list(source_pr.get_issue_comments())

            for comment in comments:
                try:
                    comment_body = f"**Comment by @{comment.user.login}** "
                    comment_body += f"*({comment.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')})*:\n\n"
                    comment_body += comment.body or "*No content*"

                    self.rate_limiter.wait_for_write()
                    target_pr.create_issue_comment(comment_body)

                except GithubException as e:
                    error_msg = f"Failed to migrate comment on PR #{source_pr.number}: {str(e)}"
                    self.state.add_error(error_msg)
                    continue

            # Note: Review comments (inline code comments) are harder to migrate
            # as they reference specific lines that may not exist in target
            # We'll migrate them as general comments instead
            review_comments = list(source_pr.get_review_comments())

            for comment in review_comments:
                try:
                    comment_body = f"**Review comment by @{comment.user.login}** "
                    comment_body += f"on `{comment.path}:{comment.position}` "
                    comment_body += f"*({comment.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')})*:\n\n"
                    comment_body += comment.body or "*No content*"

                    self.rate_limiter.wait_for_write()
                    target_pr.create_issue_comment(comment_body)

                except GithubException as e:
                    error_msg = f"Failed to migrate review comment on PR #{source_pr.number}: {str(e)}"
                    self.state.add_error(error_msg)
                    continue

        except Exception as e:
            error_msg = f"Failed to migrate comments for PR #{source_pr.number}: {str(e)}"
            self.state.add_error(error_msg)

    def _migrate_pr_comments_to_issue(self, source_pr, target_issue):
        """
        Migrate PR comments to an issue.

        Args:
            source_pr: Source pull request object
            target_issue: Target issue object
        """
        try:
            comments = list(source_pr.get_issue_comments())

            for comment in comments:
                try:
                    comment_body = f"**Comment by @{comment.user.login}** "
                    comment_body += f"*({comment.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')})*:\n\n"
                    comment_body += comment.body or "*No content*"

                    self.rate_limiter.wait_for_write()
                    target_issue.create_comment(comment_body)

                except GithubException as e:
                    error_msg = f"Failed to migrate comment: {str(e)}"
                    self.state.add_error(error_msg)
                    continue

        except Exception as e:
            error_msg = f"Failed to migrate PR comments to issue: {str(e)}"
            self.state.add_error(error_msg)
