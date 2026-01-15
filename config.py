"""Configuration management for the GitHub migration utility."""

import os
import re
from typing import Tuple
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Config:
    """Configuration settings for the migration utility."""

    # GitHub API settings
    GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
    BASE_API_URL = 'https://api.github.com'

    # Rate limiting settings
    MIN_DELAY_SECONDS = 2.0  # Minimum delay between API write operations
    BULK_DELAY_SECONDS = 5.0  # Delay when doing bulk operations (many writes in succession)
    RATE_LIMIT_BUFFER = 100  # Stop if remaining requests drop below this

    # Secondary rate limit (abuse detection) settings
    SECONDARY_RATE_LIMIT_COOLDOWN = 120  # Seconds to wait when 403 is encountered
    RETRY_BACKOFF_FACTOR = 60  # Backoff factor for PyGithub retries (results in 60s, 120s, 240s waits)
    MAX_COMMENTS_PER_ITEM = 30  # Max comments to migrate per issue/PR (None = unlimited)

    # Migration settings
    COPY_ALL_BRANCHES = False  # Default to copying only the default branch
    MIGRATE_LABELS = True
    MIGRATE_RELEASES = True
    MIGRATE_ISSUES = True
    MIGRATE_PULL_REQUESTS = True

    # Limit for issues and PRs (None = fetch all, number = fetch most recent N)
    ITEM_LIMIT = 1000  # Default: fetch most recent 1000 issues/PRs

    # State file for resume capability
    STATE_FILE = 'migration_state.json'

    # Optional overrides from environment
    TARGET_OWNER = os.getenv('TARGET_OWNER')
    TARGET_REPO = os.getenv('TARGET_REPO')

    @staticmethod
    def validate():
        """Validate required configuration."""
        if not Config.GITHUB_TOKEN:
            raise ValueError(
                "GITHUB_TOKEN is required. Please set it in your .env file.\n"
                "Get a token from: https://github.com/settings/tokens\n"
                "Required scopes: repo, read:org, read:user"
            )

    @staticmethod
    def parse_github_url(url: str) -> Tuple[str, str]:
        """
        Parse a GitHub URL to extract owner and repository name.

        Supports formats:
        - https://github.com/owner/repo
        - https://github.com/owner/repo.git
        - git@github.com:owner/repo.git
        - owner/repo

        Args:
            url: GitHub URL or owner/repo string

        Returns:
            Tuple of (owner, repo_name)

        Raises:
            ValueError: If URL format is invalid
        """
        # Remove trailing slashes and .git extension
        url = url.rstrip('/').rstrip('.git')

        # Pattern for HTTPS URLs
        https_pattern = r'https?://github\.com/([^/]+)/([^/]+)'
        # Pattern for SSH URLs
        ssh_pattern = r'git@github\.com:([^/]+)/([^/]+)'
        # Pattern for owner/repo format
        simple_pattern = r'^([^/]+)/([^/]+)$'

        for pattern in [https_pattern, ssh_pattern, simple_pattern]:
            match = re.match(pattern, url)
            if match:
                owner, repo = match.groups()
                # Remove .git if it's still there
                repo = repo.replace('.git', '')
                return owner, repo

        raise ValueError(
            f"Invalid GitHub URL format: {url}\n"
            "Supported formats:\n"
            "  - https://github.com/owner/repo\n"
            "  - git@github.com:owner/repo.git\n"
            "  - owner/repo"
        )
