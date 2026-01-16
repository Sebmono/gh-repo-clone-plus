"""Authentication handling for GitHub API."""

from github import Github, Auth
from urllib3.util.retry import Retry
from config import Config


class GitHubAuthenticator:
    """Handles GitHub API authentication."""

    def __init__(self, token: str = None):
        """
        Initialize GitHub authenticator.

        Args:
            token: GitHub Personal Access Token (defaults to Config.GITHUB_TOKEN)
        """
        self.token = token or Config.GITHUB_TOKEN
        if not self.token:
            raise ValueError("GitHub token is required")

        self.auth = Auth.Token(self.token)

        # Configure custom retry behavior for secondary rate limits (403)
        # Use longer backoff to handle GitHub's abuse detection
        # With backoff_factor=60: waits are 0s, 60s, 120s, 240s for retries 0, 1, 2, 3
        retry = Retry(
            total=2,  # Max 2 retries (to avoid "too many 403" errors)
            status_forcelist=[403, 500, 502, 503, 504],
            backoff_factor=Config.RETRY_BACKOFF_FACTOR,
            respect_retry_after_header=True,
        )

        self.github = Github(auth=self.auth, retry=retry)

    def get_client(self) -> Github:
        """Get authenticated GitHub client."""
        return self.github

    def get_user(self):
        """Get authenticated user information."""
        return self.github.get_user()

    def verify_token(self) -> bool:
        """
        Verify that the token is valid and has required permissions.

        Returns:
            True if token is valid, raises exception otherwise
        """
        try:
            user = self.get_user()
            print(f"✓ Authenticated as: {user.login}")

            # Check rate limit
            rate_limit = self.github.get_rate_limit()
            print(f"✓ API Rate Limit: {rate_limit.core.remaining}/{rate_limit.core.limit} remaining")

            return True
        except Exception as e:
            raise ValueError(f"Token verification failed: {str(e)}")

    def close(self):
        """Close the GitHub connection."""
        self.github.close()
