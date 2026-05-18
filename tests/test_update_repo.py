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


class TestUpdateRepoMainFlow:
    """Tests for the --update-repo flow in main()."""

    @patch('migrate.PullRequestMigrator')
    @patch('migrate.IssueMigrator')
    @patch('migrate.ReleaseMigrator')
    @patch('migrate.LabelMigrator')
    @patch('migrate.RepositoryForker')
    @patch('migrate.RateLimiter')
    @patch('migrate.GitHubAuthenticator')
    @patch('migrate.MigrationState')
    @patch('migrate.Config')
    def test_update_repo_calls_update_repository(
        self, mock_config, mock_state_cls, mock_auth_cls,
        mock_rl_cls, mock_forker_cls, mock_label_cls,
        mock_release_cls, mock_issue_cls, mock_pr_cls
    ):
        """--update-repo should call forker.update_repository instead of fork_repository."""
        mock_config.validate.return_value = None
        mock_config.parse_github_url.return_value = ('blinklabs-io', 'gouroboros')
        mock_config.TARGET_OWNER = 'Sandgarden-Demo'
        mock_config.TARGET_REPO = None
        mock_config.MIGRATE_LABELS = True
        mock_config.MIGRATE_RELEASES = True
        mock_config.MIGRATE_PULL_REQUESTS = True

        mock_state = MagicMock()
        mock_state.state = {'source_repo': None, 'target_repo': None, 'completed_steps': []}
        mock_state_cls.return_value = mock_state

        mock_auth = MagicMock()
        mock_auth_cls.return_value = mock_auth

        mock_forker = MagicMock()
        mock_target = MagicMock()
        mock_target.html_url = 'https://github.com/Sandgarden-Demo/gouroboros'
        mock_forker.update_repository.return_value = mock_target
        mock_source = MagicMock()
        mock_forker.get_repository.return_value = mock_source
        mock_forker_cls.return_value = mock_forker

        args = make_args(update_repo=True, source_repo='blinklabs-io/gouroboros')

        with patch('migrate.parse_arguments', return_value=args):
            from migrate import main
            main()

        mock_forker.update_repository.assert_called_once_with(
            'blinklabs-io', 'gouroboros', 'Sandgarden-Demo', None
        )
        mock_forker.fork_repository.assert_not_called()

    @patch('migrate.PullRequestMigrator')
    @patch('migrate.IssueMigrator')
    @patch('migrate.ReleaseMigrator')
    @patch('migrate.LabelMigrator')
    @patch('migrate.RepositoryForker')
    @patch('migrate.RateLimiter')
    @patch('migrate.GitHubAuthenticator')
    @patch('migrate.MigrationState')
    @patch('migrate.Config')
    def test_update_repo_with_target_name(
        self, mock_config, mock_state_cls, mock_auth_cls,
        mock_rl_cls, mock_forker_cls, mock_label_cls,
        mock_release_cls, mock_issue_cls, mock_pr_cls
    ):
        """--update-repo with --target-name should pass target_name to update_repository."""
        mock_config.validate.return_value = None
        mock_config.parse_github_url.return_value = ('blinklabs-io', 'gouroboros')
        mock_config.TARGET_OWNER = 'Sandgarden-Demo'
        mock_config.TARGET_REPO = None
        mock_config.MIGRATE_LABELS = True
        mock_config.MIGRATE_RELEASES = True
        mock_config.MIGRATE_PULL_REQUESTS = True

        mock_state = MagicMock()
        mock_state.state = {'source_repo': None, 'target_repo': None, 'completed_steps': []}
        mock_state_cls.return_value = mock_state

        mock_auth = MagicMock()
        mock_auth_cls.return_value = mock_auth

        mock_forker = MagicMock()
        mock_target = MagicMock()
        mock_target.html_url = 'https://github.com/Sandgarden-Demo/my-copy'
        mock_forker.update_repository.return_value = mock_target
        mock_source = MagicMock()
        mock_forker.get_repository.return_value = mock_source
        mock_forker_cls.return_value = mock_forker

        args = make_args(update_repo=True, target_name='my-copy', source_repo='blinklabs-io/gouroboros')

        with patch('migrate.parse_arguments', return_value=args):
            from migrate import main
            main()

        mock_forker.update_repository.assert_called_once_with(
            'blinklabs-io', 'gouroboros', 'Sandgarden-Demo', 'my-copy'
        )

    @patch('migrate.PullRequestMigrator')
    @patch('migrate.IssueMigrator')
    @patch('migrate.ReleaseMigrator')
    @patch('migrate.LabelMigrator')
    @patch('migrate.RepositoryForker')
    @patch('migrate.RateLimiter')
    @patch('migrate.GitHubAuthenticator')
    @patch('migrate.MigrationState')
    @patch('migrate.Config')
    def test_update_repo_clears_completed_steps(
        self, mock_config, mock_state_cls, mock_auth_cls,
        mock_rl_cls, mock_forker_cls, mock_label_cls,
        mock_release_cls, mock_issue_cls, mock_pr_cls
    ):
        """--update-repo should clear completed_steps so metadata migrators re-run."""
        mock_config.validate.return_value = None
        mock_config.parse_github_url.return_value = ('owner', 'repo')
        mock_config.TARGET_OWNER = 'target-org'
        mock_config.TARGET_REPO = None
        mock_config.MIGRATE_LABELS = True
        mock_config.MIGRATE_RELEASES = True
        mock_config.MIGRATE_PULL_REQUESTS = True

        mock_state = MagicMock()
        mock_state.state = {
            'source_repo': None,
            'target_repo': None,
            'completed_steps': ['labels_migrated', 'releases_migrated'],
        }
        mock_state_cls.return_value = mock_state

        mock_forker = MagicMock()
        mock_target = MagicMock()
        mock_target.html_url = 'https://github.com/target-org/repo'
        mock_forker.update_repository.return_value = mock_target
        mock_forker.get_repository.return_value = MagicMock()
        mock_forker_cls.return_value = mock_forker

        mock_auth = MagicMock()
        mock_auth_cls.return_value = mock_auth

        args = make_args(update_repo=True)

        with patch('migrate.parse_arguments', return_value=args):
            from migrate import main
            main()

        assert mock_state.state['completed_steps'] == []

    @patch('migrate.PullRequestMigrator')
    @patch('migrate.IssueMigrator')
    @patch('migrate.ReleaseMigrator')
    @patch('migrate.LabelMigrator')
    @patch('migrate.RepositoryForker')
    @patch('migrate.RateLimiter')
    @patch('migrate.GitHubAuthenticator')
    @patch('migrate.MigrationState')
    @patch('migrate.Config')
    def test_update_repo_respects_skip_flags(
        self, mock_config, mock_state_cls, mock_auth_cls,
        mock_rl_cls, mock_forker_cls, mock_label_cls,
        mock_release_cls, mock_issue_cls, mock_pr_cls
    ):
        """--update-repo should respect --skip-labels, --skip-releases, --skip-prs."""
        mock_config.validate.return_value = None
        mock_config.parse_github_url.return_value = ('owner', 'repo')
        mock_config.TARGET_OWNER = 'target-org'
        mock_config.TARGET_REPO = None
        mock_config.MIGRATE_LABELS = True
        mock_config.MIGRATE_RELEASES = True
        mock_config.MIGRATE_PULL_REQUESTS = True

        mock_state = MagicMock()
        mock_state.state = {'source_repo': None, 'target_repo': None, 'completed_steps': []}
        mock_state_cls.return_value = mock_state

        mock_forker = MagicMock()
        mock_target = MagicMock()
        mock_target.html_url = 'https://github.com/target-org/repo'
        mock_forker.update_repository.return_value = mock_target
        mock_forker.get_repository.return_value = MagicMock()
        mock_forker_cls.return_value = mock_forker

        mock_auth = MagicMock()
        mock_auth_cls.return_value = mock_auth

        mock_label_migrator = MagicMock()
        mock_label_cls.return_value = mock_label_migrator
        mock_release_migrator = MagicMock()
        mock_release_cls.return_value = mock_release_migrator
        mock_pr_migrator = MagicMock()
        mock_pr_cls.return_value = mock_pr_migrator

        args = make_args(update_repo=True, skip_labels=True, skip_releases=True, skip_prs=True)

        with patch('migrate.parse_arguments', return_value=args):
            from migrate import main
            main()

        mock_label_migrator.migrate_labels.assert_not_called()
        mock_release_migrator.migrate_releases.assert_not_called()
        mock_pr_migrator.migrate_pull_requests.assert_not_called()
