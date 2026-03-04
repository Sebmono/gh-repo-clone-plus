"""Pull requests migration functionality."""

from github import GithubException
from modules.rate_limiter import RateLimiter
from modules.state import MigrationState
from modules.text_utils import anonymize_mentions
from config import Config
from tqdm import tqdm

# GitHub API body length limit
MAX_BODY_LENGTH = Config.MAX_BODY_LENGTH


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

    def _resolve_branch_name(self, target_repo, branch_name: str) -> str:
        """
        Resolve the actual branch name in target repo, handling renames like master→main.

        GitHub may redirect branch requests (e.g., master→main) but PR creation
        requires the actual branch name, not the redirected one.

        Args:
            target_repo: Target repository object
            branch_name: Original branch name from source PR

        Returns:
            Actual branch name that exists in target repo
        """
        # Common branch renames to check
        branch_mappings = {
            'master': 'main',
            'main': 'master',
        }

        # First check if the branch exists directly
        try:
            branch = target_repo.get_branch(branch_name)
            # The branch API may redirect - check if the actual name differs
            if hasattr(branch, 'name') and branch.name != branch_name:
                return branch.name
            return branch_name
        except GithubException:
            pass

        # Try the mapped alternative
        if branch_name in branch_mappings:
            alt_name = branch_mappings[branch_name]
            try:
                target_repo.get_branch(alt_name)
                return alt_name
            except GithubException:
                pass

        # Also check target repo's default branch
        try:
            default_branch = target_repo.default_branch
            if branch_name in ('master', 'main') and default_branch:
                return default_branch
        except:
            pass

        return branch_name  # Return original if no mapping found

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
            print(f"\n      → PR #{pr.number}: Checking branches...", end='', flush=True)

            # Check if base branch exists in target (handle master→main renames)
            original_base = pr.base.ref
            base_branch = self._resolve_branch_name(target_repo, original_base)

            try:
                target_repo.get_branch(base_branch)
                if base_branch != original_base:
                    print(f" base OK ({original_base}→{base_branch})", end='', flush=True)
                else:
                    print(f" base OK", end='', flush=True)
            except GithubException:
                print(f"\n      ℹ Base branch '{original_base}' not found (converting to issue)")
                return False

            # Use unique branch name to avoid conflicts with other PRs using same branch
            original_head_branch = pr.head.ref
            head_branch = f"{original_head_branch}-migrated-pr-{pr.number}"
            head_sha = pr.head.sha
            created_placeholder = False

            # Try to create unique head branch for this PR
            try:
                # First check if our unique branch already exists
                target_repo.get_branch(head_branch)
                print(f", head OK (reusing)", end='', flush=True)
            except GithubException:
                # Branch doesn't exist, try to create it
                print(f", creating head branch...", end='', flush=True)
                branch_created = self._create_placeholder_branch(
                    target_repo, head_branch, head_sha, base_branch
                )
                if branch_created:
                    print(f" created", end='', flush=True)
                    created_placeholder = True
                else:
                    print(f"\n      ℹ Cannot create branch (converting to issue)")
                    return False

            # Early detection: Check if there are actual commits between branches
            print(f", checking diff...", end='', flush=True)
            try:
                comparison = target_repo.compare(base_branch, head_branch)
                if comparison.total_commits == 0:
                    print(f"\n      ℹ No commits between branches (original commits not in fork, converting to issue)")
                    # Clean up the branch we just created if it was a placeholder
                    if created_placeholder:
                        try:
                            ref = target_repo.get_git_ref(f"heads/{head_branch}")
                            ref.delete()
                        except:
                            pass  # Best effort cleanup
                    return False
                print(f" {comparison.total_commits} commits", end='', flush=True)
            except GithubException as e:
                # If compare fails, try to proceed anyway
                print(f" (compare failed, trying anyway)", end='', flush=True)

            # Prepare PR body with attribution
            body = self._format_pr_body(pr, source_repo)

            # Prepare labels
            label_names = [label.name for label in pr.labels]

            # Create PR in target
            print(f", creating PR...", end='', flush=True)
            self.rate_limiter.wait_for_write()
            new_pr = target_repo.create_pull(
                title=pr.title,
                body=body,
                head=head_branch,
                base=base_branch
            )
            print(f" created", end='', flush=True)

            # Add labels
            if label_names:
                print(f", adding labels...", end='', flush=True)
                self.rate_limiter.wait_for_write()
                new_pr.set_labels(*label_names)
                print(f" done", end='', flush=True)

            # Migrate comments
            total_comments = pr.comments + pr.review_comments
            if total_comments > 0:
                print(f", migrating {total_comments} comments...", end='', flush=True)
                self._migrate_pr_comments(pr, new_pr)
                print(f" done", end='', flush=True)

            # Close PR if original was closed or merged
            if pr.state == 'closed':
                print(f", closing...", end='', flush=True)
                self.rate_limiter.wait_for_write()
                new_pr.edit(state='closed')
                print(f" done", end='', flush=True)

            print(f" ✓")
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
            print(f"\n      → PR #{pr.number}: Creating as issue...", end='', flush=True)

            # Format body as PR-turned-issue
            body = self._format_pr_as_issue_body(pr, source_repo)

            # Prepare labels
            label_names = [label.name for label in pr.labels]

            # Create issue in target with 403 handling
            self.rate_limiter.wait_for_write()
            try:
                new_issue = target_repo.create_issue(
                    title=f"[PR] {pr.title}",
                    body=body,
                    labels=label_names
                )
            except GithubException as e:
                if e.status == 403:
                    # Secondary rate limit hit - enter cooldown and retry
                    self.rate_limiter.handle_secondary_rate_limit()
                    new_issue = target_repo.create_issue(
                        title=f"[PR] {pr.title}",
                        body=body,
                        labels=label_names
                    )
                else:
                    raise
            print(f" created", end='', flush=True)

            # Migrate comments
            if pr.comments > 0:
                print(f", migrating {pr.comments} comments...", end='', flush=True)
                self._migrate_pr_comments_to_issue(pr, new_issue)
                print(f" done", end='', flush=True)

            # Close issue if original PR was closed
            if pr.state == 'closed':
                print(f", closing...", end='', flush=True)
                self.rate_limiter.wait_for_write()
                try:
                    new_issue.edit(state='closed')
                except GithubException as e:
                    if e.status == 403:
                        self.rate_limiter.handle_secondary_rate_limit()
                        new_issue.edit(state='closed')
                    else:
                        raise
                print(f" done", end='', flush=True)

            print(f" ✓")

        except GithubException as e:
            error_msg = f"Failed to create PR #{pr.number} as issue: {str(e)}"
            print(f"\n   ⚠ {error_msg}")
            self.state.add_error(error_msg)

    def _truncate_body(self, full_body: str, original_url: str) -> str:
        """
        Truncate body to fit GitHub's maximum length, preserving a link to the original.

        Args:
            full_body: The full body text
            original_url: URL to the original PR for reference

        Returns:
            Body truncated to MAX_BODY_LENGTH if needed
        """
        if len(full_body) <= MAX_BODY_LENGTH:
            return full_body

        truncation_notice = (
            f"\n\n---\n\n"
            f"> **Note:** This body was truncated from {len(full_body):,} characters to fit "
            f"GitHub's {MAX_BODY_LENGTH:,} character limit.\n"
            f"> **Full content:** {original_url}"
        )
        max_content = MAX_BODY_LENGTH - len(truncation_notice)
        return full_body[:max_content] + truncation_notice

    def _format_pr_body(self, pr, source_repo) -> str:
        """
        Format PR body with original metadata.

        Args:
            pr: Source pull request object
            source_repo: Source repository object

        Returns:
            Formatted PR body with attribution (mentions anonymized)
        """
        # Create header with original metadata (use + instead of @ to avoid notifications)
        header = f"> **Original PR:** {source_repo.html_url}/pull/{pr.number}\n"
        header += f"> **Opened by:** +{pr.user.login}\n"
        header += f"> **Created at:** {pr.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"

        if pr.merged:
            header += f"> **Merged at:** {pr.merged_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            if pr.merged_by:
                header += f"> **Merged by:** +{pr.merged_by.login}\n"
        elif pr.closed_at:
            header += f"> **Closed at:** {pr.closed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"

        header += f"> **Base branch:** `{pr.base.ref}` → **Head branch:** `{pr.head.ref}`\n"

        if pr.assignees:
            assignees = ", ".join([f"+{a.login}" for a in pr.assignees])
            header += f"> **Original assignees:** {assignees}\n"

        header += "\n---\n\n"

        # Add original body with mentions anonymized
        body = pr.body or "*No description provided.*"
        body = anonymize_mentions(body)

        original_url = f"{source_repo.html_url}/pull/{pr.number}"
        return self._truncate_body(header + body, original_url)

    def _format_pr_as_issue_body(self, pr, source_repo) -> str:
        """
        Format PR as issue body (when PR can't be recreated).

        Args:
            pr: Source pull request object
            source_repo: Source repository object

        Returns:
            Formatted issue body (mentions anonymized, truncated if needed)
        """
        # Use + instead of @ to avoid notifications
        header = f"> **ℹ This was originally a Pull Request (not recreated)**\n"
        header += f"> **Original PR:** {source_repo.html_url}/pull/{pr.number}\n"
        header += f"> **Opened by:** +{pr.user.login}\n"
        header += f"> **Created at:** {pr.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"

        if pr.merged:
            header += f"> **Merged at:** {pr.merged_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        elif pr.closed_at:
            header += f"> **Closed at:** {pr.closed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"

        header += f"> **Base branch:** `{pr.base.ref}` → **Head branch:** `{pr.head.ref}`\n"
        header += "\n---\n\n"

        body = pr.body or "*No description provided.*"
        body = anonymize_mentions(body)

        original_url = f"{source_repo.html_url}/pull/{pr.number}"
        return self._truncate_body(header + body, original_url)

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

            # Apply comment limit if configured
            max_comments = Config.MAX_COMMENTS_PER_ITEM
            total_comments = len(comments)
            if max_comments and total_comments > max_comments:
                print(f" (limiting to {max_comments} of {total_comments})", end='', flush=True)
                comments = comments[:max_comments]

            migrated_count = 0
            for comment in comments:
                try:
                    # Use + instead of @ to avoid notifications
                    comment_body = f"**Comment by +{comment.user.login}** "
                    comment_body += f"*({comment.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')})*:\n\n"
                    comment_body += anonymize_mentions(comment.body) or "*No content*"

                    self.rate_limiter.wait_for_write(is_bulk_operation=True)
                    target_pr.create_issue_comment(comment_body)
                    migrated_count += 1

                except GithubException as e:
                    if e.status == 403:
                        # Secondary rate limit hit - enter cooldown and retry
                        self.rate_limiter.handle_secondary_rate_limit()
                        try:
                            target_pr.create_issue_comment(comment_body)
                            migrated_count += 1
                        except GithubException:
                            error_msg = f"Failed to migrate comment on PR #{source_pr.number} after cooldown: {str(e)}"
                            self.state.add_error(error_msg)
                    else:
                        error_msg = f"Failed to migrate comment on PR #{source_pr.number}: {str(e)}"
                        self.state.add_error(error_msg)
                    continue

            # Note: Review comments (inline code comments) are harder to migrate
            # as they reference specific lines that may not exist in target
            # We'll migrate them as general comments instead
            review_comments = list(source_pr.get_review_comments())

            # Apply limit to review comments too
            if max_comments:
                remaining_limit = max(0, max_comments - migrated_count)
                if len(review_comments) > remaining_limit:
                    review_comments = review_comments[:remaining_limit]

            for comment in review_comments:
                try:
                    # Use + instead of @ to avoid notifications
                    comment_body = f"**Review comment by +{comment.user.login}** "
                    comment_body += f"on `{comment.path}:{comment.position}` "
                    comment_body += f"*({comment.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')})*:\n\n"
                    comment_body += anonymize_mentions(comment.body) or "*No content*"

                    self.rate_limiter.wait_for_write(is_bulk_operation=True)
                    target_pr.create_issue_comment(comment_body)

                except GithubException as e:
                    if e.status == 403:
                        # Secondary rate limit hit - enter cooldown and retry
                        self.rate_limiter.handle_secondary_rate_limit()
                        try:
                            target_pr.create_issue_comment(comment_body)
                        except GithubException:
                            error_msg = f"Failed to migrate review comment on PR #{source_pr.number} after cooldown: {str(e)}"
                            self.state.add_error(error_msg)
                    else:
                        error_msg = f"Failed to migrate review comment on PR #{source_pr.number}: {str(e)}"
                        self.state.add_error(error_msg)
                    continue

            # Reset consecutive writes counter after bulk operation
            self.rate_limiter.reset_consecutive_writes()

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

            # Apply comment limit if configured
            max_comments = Config.MAX_COMMENTS_PER_ITEM
            if max_comments and len(comments) > max_comments:
                print(f" (limiting to {max_comments} of {len(comments)})", end='', flush=True)
                comments = comments[:max_comments]

            for comment in comments:
                try:
                    # Use + instead of @ to avoid notifications
                    comment_body = f"**Comment by +{comment.user.login}** "
                    comment_body += f"*({comment.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')})*:\n\n"
                    comment_body += anonymize_mentions(comment.body) or "*No content*"

                    self.rate_limiter.wait_for_write(is_bulk_operation=True)
                    target_issue.create_comment(comment_body)

                except GithubException as e:
                    if e.status == 403:
                        # Secondary rate limit hit - enter cooldown and retry
                        self.rate_limiter.handle_secondary_rate_limit()
                        try:
                            target_issue.create_comment(comment_body)
                        except GithubException:
                            error_msg = f"Failed to migrate comment after cooldown: {str(e)}"
                            self.state.add_error(error_msg)
                    else:
                        error_msg = f"Failed to migrate comment: {str(e)}"
                        self.state.add_error(error_msg)
                    continue

            # Reset consecutive writes counter after bulk operation
            self.rate_limiter.reset_consecutive_writes()

        except Exception as e:
            error_msg = f"Failed to migrate PR comments to issue: {str(e)}"
            self.state.add_error(error_msg)
