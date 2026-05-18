import argparse
import os
import sys
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.fork import RepositoryForker


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


class TestUpdateRepository:
    """Tests for RepositoryForker.update_repository()."""

    def _make_forker(self):
        github_client = MagicMock()
        rate_limiter = MagicMock()
        state = MagicMock()
        return RepositoryForker(github_client, rate_limiter, state)

    def test_target_not_found_raises_error(self):
        """Should raise ValueError when target repo does not exist."""
        from github import GithubException
        forker = self._make_forker()
        forker.github.get_repo.side_effect = GithubException(404, {"message": "Not Found"}, None)

        with pytest.raises(ValueError, match="No repo in the target destination exists with that name"):
            forker.update_repository('source-owner', 'myrepo', 'target-owner', None)

    def test_target_found_by_source_name(self):
        """When no target_name given, should look up source name in target org."""
        forker = self._make_forker()
        mock_repo = MagicMock()
        mock_repo.html_url = 'https://github.com/target-owner/myrepo'
        mock_repo.has_issues = True
        forker.github.get_repo.return_value = mock_repo

        with patch('modules.fork.subprocess') as mock_sub:
            mock_sub.run.return_value = MagicMock(returncode=0, stderr='', stdout='')
            with patch('modules.fork.tempfile.mkdtemp', return_value='/tmp/ghm_test'):
                with patch('modules.fork.shutil.rmtree'):
                    with patch('modules.fork.os.path.exists', return_value=True):
                        result = forker.update_repository('source-owner', 'myrepo', 'target-owner', None)

        forker.github.get_repo.assert_any_call('target-owner/myrepo')
        assert result == mock_repo

    def test_target_found_by_target_name(self):
        """When --target-name given, should look up that name instead of source name."""
        forker = self._make_forker()
        mock_repo = MagicMock()
        mock_repo.html_url = 'https://github.com/target-owner/custom-name'
        mock_repo.has_issues = True
        forker.github.get_repo.return_value = mock_repo

        with patch('modules.fork.subprocess') as mock_sub:
            mock_sub.run.return_value = MagicMock(returncode=0, stderr='', stdout='')
            with patch('modules.fork.tempfile.mkdtemp', return_value='/tmp/ghm_test'):
                with patch('modules.fork.shutil.rmtree'):
                    with patch('modules.fork.os.path.exists', return_value=True):
                        result = forker.update_repository('source-owner', 'myrepo', 'target-owner', 'custom-name')

        forker.github.get_repo.assert_any_call('target-owner/custom-name')
        assert result == mock_repo

    def test_enables_issues_if_disabled(self):
        """Should enable issues on target repo if they are disabled."""
        forker = self._make_forker()
        mock_repo = MagicMock()
        mock_repo.html_url = 'https://github.com/target-owner/myrepo'
        mock_repo.has_issues = False
        forker.github.get_repo.return_value = mock_repo

        with patch('modules.fork.subprocess') as mock_sub:
            mock_sub.run.return_value = MagicMock(returncode=0, stderr='', stdout='')
            with patch('modules.fork.tempfile.mkdtemp', return_value='/tmp/ghm_test'):
                with patch('modules.fork.shutil.rmtree'):
                    with patch('modules.fork.os.path.exists', return_value=True):
                        forker.update_repository('source-owner', 'myrepo', 'target-owner', None)

        mock_repo.edit.assert_called_with(has_issues=True)

    def test_mirror_clone_and_force_push(self):
        """Should mirror clone source and force push branches+tags to target."""
        forker = self._make_forker()
        mock_repo = MagicMock()
        mock_repo.html_url = 'https://github.com/target-owner/myrepo'
        mock_repo.has_issues = True
        forker.github.get_repo.return_value = mock_repo

        with patch('modules.fork.subprocess') as mock_sub:
            mock_sub.run.return_value = MagicMock(returncode=0, stderr='', stdout='')
            with patch('modules.fork.tempfile.mkdtemp', return_value='/tmp/ghm_test'):
                with patch('modules.fork.shutil.rmtree'):
                    with patch('modules.fork.os.path.exists', return_value=True):
                        with patch('modules.fork.Config') as mock_config:
                            mock_config.GITHUB_TOKEN = 'fake-token'
                            forker.update_repository('source-owner', 'myrepo', 'target-owner', None)

        calls = mock_sub.run.call_args_list
        clone_cmd = calls[0][0][0]
        assert '--mirror' in clone_cmd
        # The clone URL is the second-to-last arg (last arg is the clone path)
        assert 'source-owner/myrepo' in clone_cmd[-2]

        push_branches_cmd = calls[1][0][0]
        assert 'refs/heads/*:refs/heads/*' in push_branches_cmd
        assert '--force' in push_branches_cmd

        push_tags_cmd = calls[2][0][0]
        assert 'refs/tags/*:refs/tags/*' in push_tags_cmd
        assert '--force' in push_tags_cmd
