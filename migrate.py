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
  python migrate.py octocat/Hello-World --target-owner myorg
  python migrate.py octocat/Hello-World --target-name my-copy
  python migrate.py octocat/Hello-World --include-issues
  python migrate.py octocat/Hello-World --limit-items 10
  python migrate.py octocat/Hello-World --resume  # Continue interrupted migration
  python migrate.py octocat/Hello-World --update-repo  # Update existing target repo

For first-time setup:
  1. Copy .env.example to .env
  2. Add your GitHub Personal Access Token to .env
  3. Run: python migrate.py <repo_url> --target-owner <your-org>

Note: Each run starts fresh by default. Use --resume to continue an interrupted
migration. Creates an Internal repository in the target organization.
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
        help='Resume from last saved state (default starts fresh)'
    )

    parser.add_argument(
        '--update-repo',
        action='store_true',
        help='Update an existing target repo: sync all code and migrate new metadata only'
    )

    parser.add_argument(
        '--limit-items',
        type=int,
        default=1000,
        help='Limit number of releases/issues/PRs to migrate (most recent N). Use 0 for no limit. Default: 1000'
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

        # Parse source repository URL
        try:
            source_owner, source_repo = Config.parse_github_url(args.source_repo)
        except ValueError as e:
            print(f"❌ Error: {str(e)}")
            sys.exit(1)

        # Initialize state
        state = MigrationState()

        # Handle resume vs fresh start
        if args.resume:
            # Resume mode: check if state exists and matches requested repo
            existing_source = state.state.get('source_repo')
            if existing_source:
                existing_repo = f"{existing_source['owner']}/{existing_source['name']}"
                requested_repo = f"{source_owner}/{source_repo}"
                if existing_repo.lower() != requested_repo.lower():
                    print(f"❌ Error: Cannot resume - state file is for '{existing_repo}', not '{requested_repo}'")
                    sys.exit(1)
                print("📋 Resuming from saved state...")
                state.print_summary()
                resume = input("\nContinue with this migration? (yes/no): ").strip().lower()
                if resume != 'yes':
                    print("Migration cancelled.")
                    return
            else:
                print("ℹ No saved state found, starting fresh migration")
                print(f"📦 Source Repository: {source_owner}/{source_repo}")
        else:
            # Default: clear any existing state and start fresh
            if state.state.get('source_repo'):
                state.clear()
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

        # Determine item limit (0 means no limit)
        item_limit = args.limit_items if args.limit_items > 0 else None

        # Migrate releases
        if not args.skip_releases and Config.MIGRATE_RELEASES:
            release_migrator.migrate_releases(source_repo, target_repo, limit=item_limit)

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
