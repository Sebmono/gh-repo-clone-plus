import argparse
import pytest
from unittest.mock import patch, MagicMock


def make_args(**overrides):
    """Build a default args namespace, applying any overrides."""
    defaults = dict(
        source_repo='owner/repo',
        target_owner=None,
        target_name=None,
        skip_labels=False,
        skip_releases=False,
        include_issues=False,
        skip_prs=False,
        resume=False,
        limit_items=1000,
        update_repo=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestParseArguments:
    """Tests for the --update-repo CLI argument."""

    def test_update_repo_flag_defaults_to_false(self):
        with patch('sys.argv', ['migrate.py', 'owner/repo']):
            from migrate import parse_arguments
            args = parse_arguments()
            assert args.update_repo is False

    def test_update_repo_flag_set_to_true(self):
        with patch('sys.argv', ['migrate.py', 'owner/repo', '--update-repo']):
            from migrate import parse_arguments
            args = parse_arguments()
            assert args.update_repo is True

    def test_update_repo_with_target_name(self):
        with patch('sys.argv', ['migrate.py', 'owner/repo', '--update-repo', '--target-name', 'custom']):
            from migrate import parse_arguments
            args = parse_arguments()
            assert args.update_repo is True
            assert args.target_name == 'custom'

    def test_update_repo_with_limit_items(self):
        with patch('sys.argv', ['migrate.py', 'owner/repo', '--update-repo', '--limit-items', '100']):
            from migrate import parse_arguments
            args = parse_arguments()
            assert args.update_repo is True
            assert args.limit_items == 100
