"""Text processing utilities for migration."""

import re


def anonymize_mentions(text: str) -> str:
    """
    Anonymize GitHub @mentions to prevent notifications.

    Replaces @username with +username so the text is still readable
    but doesn't trigger GitHub notifications to the mentioned users.

    Args:
        text: Text that may contain @mentions

    Returns:
        Text with @mentions replaced by +mentions
    """
    if not text:
        return text

    # Pattern to match @username mentions
    # GitHub usernames can contain alphanumeric characters and hyphens
    # but cannot start with a hyphen
    pattern = r'@([a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?)'

    return re.sub(pattern, r'+\1', text)
