#!/usr/bin/env python3
"""
GitHub Repository Migration Utility

This script clones a GitHub repository and creates an Internal copy in a target
organization, then migrates all metadata including:
- Labels
- Releases (with assets)
- Issues (with comments)
- Pull Requests (recreated or converted to issues)

The clone-and-push approach (instead of forking) allows the target repository to
be created with Internal visibility, avoiding GitHub's limitation where forks of
public repositories must also be public.

Usage:
    python migrate.py <source_repo_url> [options]

Examples:
    python migrate.py https://github.com/owner/repo
    python migrate.py owner/repo
    python migrate.py https://github.com/owner/repo --target-name my-copy
"""

import sys
import argparse
from config import Config
from modules.auth import GitHubAuthenticator
from modules.rate_limiter import RateLimiter
from modules.state import MigrationState
from modules.fork import RepositoryForker
from modules.labels import LabelMigrator
from modules.releases import ReleaseMigrator
from modules.issues import IssueMigrator
from modules.pull_requests import PullRequestMigrator


def print_banner():
    """Print welcome banner."""
    print("\n" + "=" * 70)
    print("  GitHub Repository Migration Utility")
    print("  Migrate repos with all metadata (Issues, PRs, Releases, Labels)")
    print("=" * 70 + "\n")


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Clone a GitHub repository to a target org as an Internal repo and migrate all metadata.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python migrate.py https://github.com/octocat/Hello-World
  python migrate.py octocat/Hello-World
  python migrate.py octocat/Hello-World --target-name my-hello-world
  python migrate.py octocat/Hello-World --target-owner myorg
  python migrate.py octocat/Hello-World --include-issues  # Issues not migrated by default
  python migrate.py octocat/Hello-World --skip-prs
  python migrate.py octocat/Hello-World --limit-items 500
  python migrate.py octocat/Hello-World --limit-items 0  # No limit, fetch all

For first-time setup:
  1. Copy .env.example to .env
  2. Add your GitHub Personal Access Token to .env
  3. Run: python migrate.py <repo_url>

Note: Unlike traditional forking, this creates an Internal repository in the
target organization, avoiding the GitHub limitation where forks of public
repositories must be public.
        """
    )

    parser.add_argument(
        'source_repo',
        help='Source repository URL or owner/repo format'
    )

    parser.add_argument(
        '--target-owner',
        help='Target organization for Internal repo (defaults to authenticated user)'
    )

    parser.add_argument(
        '--target-name',
        help='Target repository name (defaults to source repo name)'
    )

    parser.add_argument(
        '--skip-labels',
        action='store_true',
        help='Skip migrating labels'
    )

    parser.add_argument(
        '--skip-releases',
        action='store_true',
        help='Skip migrating releases'
    )

    parser.add_argument(
        '--include-issues',
        action='store_true',
        help='Include issues in migration (not migrated by default)'
    )

    parser.add_argument(
        '--skip-prs',
        action='store_true',
        help='Skip migrating pull requests'
    )

    parser.add_argument(
        '--resume',
        action='store_true',
        help='Resume from last saved state'
    )

    parser.add_argument(
        '--clear-state',
        action='store_true',
        help='Clear saved state and start fresh'
    )

    parser.add_argument(
        '--limit-items',
        type=int,
        default=1000,
        help='Limit number of issues/PRs to migrate (most recent N items). Use 0 for no limit. Default: 1000'
    )

    return parser.parse_args()


def main():
    """Main migration function."""
    print_banner()

    # Parse arguments
    args = parse_arguments()

    try:
        # Validate configuration
        Config.validate()

        # Initialize state
        state = MigrationState()

        if args.clear_state:
            print("🧹 Clearing saved state...")
            state.clear()
            print("✓ State cleared\n")

        # Always parse source repository URL first
        try:
            source_owner, source_repo = Config.parse_github_url(args.source_repo)
        except ValueError as e:
            print(f"❌ Error: {str(e)}")
            sys.exit(1)

        # Check if existing state is for a different repository
        existing_source = state.state.get('source_repo')
        if existing_source:
            existing_repo = f"{existing_source['owner']}/{existing_source['name']}"
            requested_repo = f"{source_owner}/{source_repo}"
            if existing_repo.lower() != requested_repo.lower():
                if args.resume:
                    print(f"❌ Error: Cannot resume - state file is for '{existing_repo}', not '{requested_repo}'")
                    print("   Use --clear-state to start a new migration")
                    sys.exit(1)
                else:
                    print(f"ℹ Clearing state from previous migration ({existing_repo})...")
                    state.clear()

        if args.resume and state.state.get('source_repo'):
            print("📋 Resuming from saved state...")
            state.print_summary()
            resume = input("\nContinue with this migration? (yes/no): ").strip().lower()
            if resume != 'yes':
                print("Migration cancelled.")
                return
        else:
            print(f"📦 Source Repository: {source_owner}/{source_repo}")

            # Determine target
            target_owner = args.target_owner or Config.TARGET_OWNER
            target_name = args.target_name or Config.TARGET_REPO or source_repo

            if target_owner:
                print(f"🎯 Target: {target_owner}/{target_name}\n")
            else:
                print(f"🎯 Target name: {target_name} (owner: your authenticated user)\n")

        # Authenticate
        print("🔐 Authenticating with GitHub...")
        authenticator = GitHubAuthenticator()
        github_client = authenticator.get_client()

        # Verify token
        authenticator.verify_token()
        print()

        # Initialize components
        rate_limiter = RateLimiter(github_client)
        forker = RepositoryForker(github_client, rate_limiter, state)
        label_migrator = LabelMigrator(rate_limiter, state)
        release_migrator = ReleaseMigrator(rate_limiter, state)
        issue_migrator = IssueMigrator(rate_limiter, state)
        pr_migrator = PullRequestMigrator(rate_limiter, state)

        # Get or create target repository
        if state.state.get('target_repo'):
            # Resume: Get existing target repo
            target = state.state['target_repo']
            target_repo = forker.get_repository(target['owner'], target['name'])
            source = state.state['source_repo']
            source_repo = forker.get_repository(source['owner'], source['name'])

            # Ensure issues are enabled on target repo (in case they were disabled)
            if not target_repo.has_issues:
                print("   Enabling issues on target repository...")
                rate_limiter.wait_for_write()
                target_repo.edit(has_issues=True)
                print("   ✓ Issues enabled")
        else:
            # New migration: Clone and create Internal repository
            source_owner, source_repo_name = Config.parse_github_url(args.source_repo)
            target_owner = args.target_owner or Config.TARGET_OWNER
            target_name = args.target_name or Config.TARGET_REPO

            target_repo = forker.fork_repository(
                source_owner,
                source_repo_name,
                target_owner,
                target_name
            )

            source_repo = forker.get_repository(source_owner, source_repo_name)

        # Migrate metadata
        print("\n" + "=" * 70)
        print("  Starting Metadata Migration")
        print("=" * 70)

        # Migrate labels
        if not args.skip_labels and Config.MIGRATE_LABELS:
            label_migrator.migrate_labels(source_repo, target_repo)

        # Migrate releases
        if not args.skip_releases and Config.MIGRATE_RELEASES:
            release_migrator.migrate_releases(source_repo, target_repo)

        # Determine item limit (0 means no limit)
        item_limit = args.limit_items if args.limit_items > 0 else None

        # Migrate issues (only if explicitly requested with --include-issues)
        if args.include_issues:
            issue_migrator.migrate_issues(source_repo, target_repo, limit=item_limit)

        # Migrate pull requests
        if not args.skip_prs and Config.MIGRATE_PULL_REQUESTS:
            pr_migrator.migrate_pull_requests(source_repo, target_repo, limit=item_limit)

        # Print final summary
        print("\n" + "=" * 70)
        print("  Migration Complete!")
        print("=" * 70)

        state.print_summary()

        print(f"✓ View your migrated repository at:")
        print(f"  {target_repo.html_url}\n")

        # Close connection
        authenticator.close()

    except KeyboardInterrupt:
        print("\n\n⚠ Migration interrupted by user")
        print("Run with --resume to continue from where you left off")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        if 'state' in locals():
            print("\nRun with --resume to continue from where you left off")
        sys.exit(1)


if __name__ == "__main__":
    main()
