"""Issues migration functionality."""

from github import GithubException
from modules.rate_limiter import RateLimiter
from modules.state import MigrationState
from modules.text_utils import anonymize_mentions
from config import Config
from tqdm import tqdm


class IssueMigrator:
    """Handles migrating repository issues."""

    def __init__(self, rate_limiter: RateLimiter, state: MigrationState):
        """
        Initialize issue migrator.

        Args:
            rate_limiter: Rate limiter instance
            state: Migration state manager
        """
        self.rate_limiter = rate_limiter
        self.state = state

    def migrate_issues(self, source_repo, target_repo, limit=None):
        """
        Migrate issues from source to target repository.

        Args:
            source_repo: Source repository object
            target_repo: Target repository object
            limit: Maximum number of issues to migrate (None = all, N = most recent N)

        Returns:
            Number of issues migrated
        """
        step_name = "issues_migrated"
        if self.state.is_step_completed(step_name):
            print("✓ Issues already migrated (skipping)")
            return len(self.state.state['issue_mapping'])

        try:
            print("\n🐛 Migrating issues...")

            # Get issues from source (excluding pull requests)
            if limit:
                print(f"   Fetching most recent {limit} issues from source repository...")
            else:
                print("   Fetching all issues from source repository...")

            all_issues = []
            fetch_count = 0
            for issue in source_repo.get_issues(state='all', sort='updated', direction='desc'):
                if not issue.pull_request:  # Exclude pull requests
                    all_issues.append(issue)
                    fetch_count += 1
                    if fetch_count % 100 == 0:
                        print(f"   Fetched {fetch_count} issues so far...")
                    # Stop if we've reached the limit
                    if limit and fetch_count >= limit:
                        break

            if limit and fetch_count >= limit:
                print(f"   Reached limit of {limit} issues")
            print(f"   Found {len(all_issues)} issues to migrate")

            if not all_issues:
                print("   No issues to migrate")
                self.state.mark_step_completed(step_name)
                return 0

            # Get already migrated issues from state file
            migrated_issues = set(self.state.get_migrated_issues())

            # Also check target repo for previously migrated issues (prevents duplicates)
            print("   Checking for previously migrated issues in target repo...")
            already_migrated = self._find_already_migrated_issues(target_repo, source_repo)
            migrated_issues.update(already_migrated)
            if already_migrated:
                print(f"   Found {len(already_migrated)} previously migrated issues (will skip)")

            # Sort issues by number to maintain order
            all_issues.sort(key=lambda x: x.number)

            migrated_count = 0

            # Migrate each issue
            for issue in tqdm(all_issues, desc="   Migrating issues"):
                try:
                    # Skip if already migrated
                    if issue.number in migrated_issues:
                        continue

                    # Prepare issue body with attribution
                    body = self._format_issue_body(issue, source_repo)

                    # Prepare labels
                    label_names = [label.name for label in issue.labels]

                    # Create issue in target with 403 handling
                    self.rate_limiter.wait_for_write()
                    try:
                        new_issue = target_repo.create_issue(
                            title=issue.title,
                            body=body,
                            labels=label_names
                        )
                    except GithubException as e:
                        if e.status == 403:
                            self.rate_limiter.handle_secondary_rate_limit()
                            new_issue = target_repo.create_issue(
                                title=issue.title,
                                body=body,
                                labels=label_names
                            )
                        else:
                            raise

                    # Migrate comments
                    if issue.comments > 0:
                        self._migrate_comments(issue, new_issue)

                    # Close issue if original was closed
                    if issue.state == 'closed':
                        self.rate_limiter.wait_for_write()
                        new_issue.edit(state='closed')

                    self.state.add_issue_mapping(issue.number, new_issue.number)
                    migrated_count += 1

                except GithubException as e:
                    error_msg = f"Failed to migrate issue #{issue.number}: {str(e)}"
                    print(f"\n   ⚠ {error_msg}")
                    self.state.add_error(error_msg)
                    continue
                except Exception as e:
                    error_msg = f"Unexpected error migrating issue #{issue.number}: {str(e)}"
                    print(f"\n   ⚠ {error_msg}")
                    self.state.add_error(error_msg)
                    continue

            self.state.mark_step_completed(step_name)

            print(f"✓ Issues migrated: {migrated_count}")
            return migrated_count

        except Exception as e:
            error_msg = f"Issue migration failed: {str(e)}"
            self.state.add_error(error_msg)
            raise Exception(error_msg)

    def _find_already_migrated_issues(self, target_repo, source_repo) -> set:
        """
        Find issues that were already migrated to the target repo.

        Scans target repo issues for ones containing the source repo URL pattern,
        indicating they were previously migrated.

        Args:
            target_repo: Target repository object
            source_repo: Source repository object

        Returns:
            Set of original issue numbers that were already migrated
        """
        import re
        already_migrated = set()
        source_url_pattern = f"{source_repo.html_url}/issues/"

        try:
            # Scan existing issues in target repo
            for issue in target_repo.get_issues(state='all'):
                if issue.body and source_url_pattern in issue.body:
                    # Extract original issue number from body
                    match = re.search(rf'{re.escape(source_url_pattern)}(\d+)', issue.body)
                    if match:
                        original_number = int(match.group(1))
                        already_migrated.add(original_number)
        except Exception as e:
            print(f"\n   ⚠ Warning: Could not fully scan target repo for existing issues: {str(e)}")

        return already_migrated

    def _format_issue_body(self, issue, source_repo) -> str:
        """
        Format issue body with original metadata.

        Args:
            issue: Source issue object
            source_repo: Source repository object

        Returns:
            Formatted issue body with attribution (mentions anonymized)
        """
        # Create header with original metadata (use + instead of @ to avoid notifications)
        header = f"> **Original Issue:** {source_repo.html_url}/issues/{issue.number}\n"
        header += f"> **Opened by:** +{issue.user.login}\n"
        header += f"> **Created at:** {issue.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"

        if issue.closed_at:
            header += f"> **Closed at:** {issue.closed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"

        if issue.assignees:
            assignees = ", ".join([f"+{a.login}" for a in issue.assignees])
            header += f"> **Original assignees:** {assignees}\n"

        header += "\n---\n\n"

        # Add original body with mentions anonymized
        body = issue.body or "*No description provided.*"
        body = anonymize_mentions(body)

        return header + body

    def _migrate_comments(self, source_issue, target_issue):
        """
        Migrate comments from source to target issue.

        Args:
            source_issue: Source issue object
            target_issue: Target issue object
        """
        try:
            comments = list(source_issue.get_comments())

            # Apply comment limit if configured
            max_comments = Config.MAX_COMMENTS_PER_ITEM
            if max_comments and len(comments) > max_comments:
                comments = comments[:max_comments]

            for comment in comments:
                try:
                    # Format comment with original author (use + to avoid notifications)
                    comment_body = f"**Comment by +{comment.user.login}** "
                    comment_body += f"*({comment.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')})*:\n\n"
                    comment_body += anonymize_mentions(comment.body) or "*No content*"

                    # Create comment in target with 403 handling
                    self.rate_limiter.wait_for_write(is_bulk_operation=True)
                    try:
                        target_issue.create_comment(comment_body)
                    except GithubException as e:
                        if e.status == 403:
                            # Secondary rate limit hit - enter cooldown and retry
                            self.rate_limiter.handle_secondary_rate_limit()
                            target_issue.create_comment(comment_body)
                        else:
                            raise

                except GithubException as e:
                    error_msg = f"Failed to migrate comment on issue #{source_issue.number}: {str(e)}"
                    print(f"\n      ⚠ {error_msg}")
                    self.state.add_error(error_msg)
                    continue

            # Reset consecutive writes counter after bulk operation
            self.rate_limiter.reset_consecutive_writes()

        except Exception as e:
            error_msg = f"Failed to migrate comments for issue #{source_issue.number}: {str(e)}"
            print(f"\n   ⚠ {error_msg}")
            self.state.add_error(error_msg)
