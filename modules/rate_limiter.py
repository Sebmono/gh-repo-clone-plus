"""Rate limiting utilities for GitHub API calls."""

import time
from datetime import datetime
from github import Github, GithubException
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
        self.bulk_delay = Config.BULK_DELAY_SECONDS
        self.consecutive_writes = 0
        self.in_cooldown = False
        self.cooldown_until = 0

    def wait_if_needed(self, use_bulk_delay=False):
        """
        Wait if necessary to respect rate limits.

        Ensures minimum delay between requests and checks GitHub rate limit status.

        Args:
            use_bulk_delay: If True, use longer delay for bulk operations
        """
        # Check if we're in cooldown from a secondary rate limit
        if self.in_cooldown:
            current_time = time.time()
            if current_time < self.cooldown_until:
                wait_time = self.cooldown_until - current_time
                print(f"\n   ⏳ In cooldown for {int(wait_time)} more seconds...")
                time.sleep(wait_time)
            self.in_cooldown = False

        # Determine delay based on operation type
        delay = self.bulk_delay if use_bulk_delay else self.min_delay

        # Ensure minimum delay between requests
        current_time = time.time()
        time_since_last = current_time - self.last_request_time

        if time_since_last < delay:
            sleep_time = delay - time_since_last
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

    def wait_for_write(self, is_bulk_operation=False):
        """
        Wait with delay appropriate for write operations.

        Args:
            is_bulk_operation: If True, use longer delay for bulk operations
        """
        self.check_rate_limit()
        self.consecutive_writes += 1

        # Use bulk delay if doing many consecutive writes
        use_bulk = is_bulk_operation or self.consecutive_writes > 10
        self.wait_if_needed(use_bulk_delay=use_bulk)

    def reset_consecutive_writes(self):
        """Reset the consecutive writes counter after completing a batch."""
        self.consecutive_writes = 0

    def handle_secondary_rate_limit(self):
        """
        Handle a secondary rate limit (403) by entering cooldown.

        Call this when you catch a 403 error from the GitHub API.
        """
        self.in_cooldown = True
        self.cooldown_until = time.time() + Config.SECONDARY_RATE_LIMIT_COOLDOWN
        print(f"\n   ⏳ Secondary rate limit hit. Cooling down for {Config.SECONDARY_RATE_LIMIT_COOLDOWN} seconds...")
        time.sleep(Config.SECONDARY_RATE_LIMIT_COOLDOWN)
        self.in_cooldown = False
        self.consecutive_writes = 0
        print("   ✓ Cooldown complete. Resuming...")

    def safe_api_call(self, func, *args, **kwargs):
        """
        Execute an API call with secondary rate limit handling.

        Args:
            func: The function to call
            *args: Arguments to pass to the function
            **kwargs: Keyword arguments to pass to the function

        Returns:
            The result of the function call

        Raises:
            GithubException: If the call fails after retry
        """
        try:
            return func(*args, **kwargs)
        except GithubException as e:
            if e.status == 403 and 'rate limit' in str(e).lower():
                self.handle_secondary_rate_limit()
                # Retry once after cooldown
                return func(*args, **kwargs)
            raise
