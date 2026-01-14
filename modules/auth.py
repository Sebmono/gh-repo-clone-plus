"""Authentication handling for GitHub API."""

from github import Github, Auth
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
        self.github = Github(auth=self.auth)

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
