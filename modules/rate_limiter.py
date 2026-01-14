"""Rate limiting utilities for GitHub API calls."""

import time
from datetime import datetime
from github import Github
from config import Config


class RateLimiter:
    """Manages rate limiting for GitHub API calls."""

    def __init__(self, github_client: Github):
        """
        Initialize rate limiter.

        Args:
            github_client: Authenticated GitHub client
        """
        self.github = github_client
        self.last_request_time = 0
        self.min_delay = Config.MIN_DELAY_SECONDS

    def wait_if_needed(self):
        """
        Wait if necessary to respect rate limits.

        Ensures minimum delay between requests and checks GitHub rate limit status.
        """
        # Ensure minimum delay between requests
        current_time = time.time()
        time_since_last = current_time - self.last_request_time

        if time_since_last < self.min_delay:
            sleep_time = self.min_delay - time_since_last
            time.sleep(sleep_time)

        self.last_request_time = time.time()

    def check_rate_limit(self):
        """
        Check current rate limit status and wait if necessary.

        If remaining requests are low, waits until rate limit resets.
        """
        try:
            rate_limit = self.github.get_rate_limit()
            core = rate_limit.core

            if core.remaining < Config.RATE_LIMIT_BUFFER:
                reset_time = core.reset
                wait_seconds = (reset_time - datetime.now()).total_seconds()

                if wait_seconds > 0:
                    print(f"\n⚠ Rate limit low ({core.remaining} remaining). "
                          f"Waiting {int(wait_seconds)} seconds until reset...")
                    time.sleep(wait_seconds + 5)  # Add 5 second buffer
                    print("✓ Rate limit reset. Continuing...")

        except Exception as e:
            print(f"⚠ Warning: Could not check rate limit: {str(e)}")

    def wait_for_write(self):
        """Wait with delay appropriate for write operations."""
        self.check_rate_limit()
        self.wait_if_needed()
