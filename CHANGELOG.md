# Changelog

All notable changes to the GitHub Repository Migration Utility.

## [Unreleased]

### Changed
- **Breaking**: Repository creation now uses clone-and-push instead of GitHub fork API
  - Clones source repository with `git clone --mirror` (gets all branches and tags)
  - Creates new Internal repository in target organization with `visibility="internal"`
  - Disables GitHub Actions on target repo (workflows preserved but cannot run)
  - Pushes branches with `refs/heads/*:refs/heads/*` refspec
  - Pushes tags with `refs/tags/*:refs/tags/*` refspec
  - This avoids GitHub's limitation where forks of public repos must be public
  - For personal accounts (where Internal visibility is unavailable), creates private repos
- **Breaking**: PAT now requires `workflow` scope to push .github/workflows files
- **Breaking**: Each migration run starts fresh by default (use `--resume` to continue)
- Removed `--clear-state` flag (fresh start is now the default)
- All migration steps (Labels, Releases, Issues, PRs) verified compatible with new approach

### Added
- GitHub Actions automatically disabled on target repository (prevents workflows from running)
- Push error logging to `migration_push_errors.log` for easier debugging
- Branch name resolution for master→main renames (PR #17)

### Fixed
- Push failures caused by `git push --mirror` trying to push read-only `refs/pull/*` refs

## [2026-01-19]

### Added
- Username anonymization: `@mentions` converted to `+mentions` to prevent notifications (PR #14)
- `modules/text_utils.py` with `anonymize_mentions()` function

### Changed
- Issues no longer migrated by default; use `--include-issues` to opt-in (PR #14)
- Replaced `--skip-issues` flag with `--include-issues` flag
- Reduced `MAX_COMMENTS_PER_ITEM` from 30 to 10
- `BULK_DELAY_SECONDS` increased from 3.0 to 5.0 seconds

### Fixed
- 422 "field base invalid" errors when source PRs target `master` but target repo uses `main` (PR #17)

## [2026-01-16]

### Fixed
- PyGithub internal retry handling for 403 errors (PR #11)
- Configured `urllib3.util.retry.Retry` with custom backoff factor
- Added `RETRY_BACKOFF_FACTOR` config option (default: 60)

## [2026-01-14]

### Added
- Progress indicators during issue and PR fetching (PR #2)
- Item limit for fetching (default: 1000 most recent) via `--limit-items` (PR #3)
- Auto-enable issues on forked repositories (PR #4)
- Support for forking to organizations via `--target-owner` (PR #5)
- Detailed progress tracking for PR migration (PR #7)
- Duplicate detection by scanning target repo before migration (PR #8)
- Unique branch names for PR migration (append PR number) (PR #9)
- Early diff detection to skip PRs with no commits (PR #9)
- Development workflow documentation (PR #1)
- Secondary rate limit handling with cooldown (PR #10)

### Fixed
- `AuthenticatedUser(login=None)` error when forking to organizations (PR #6)
- PR creation failures due to branch name conflicts (PR #9)
- "No commits between branches" causing unnecessary failures (PR #9)

### Changed
- `MIN_DELAY_SECONDS` increased from 1.0 to 2.0 seconds
- Added `BULK_DELAY_SECONDS` (3.0s) for comment migration
- Added `SECONDARY_RATE_LIMIT_COOLDOWN` (120s) for 403 handling

## [2026-01-13] - Initial Release

### Added
- Core migration functionality
- Repository creation (originally using fork API, now clone-and-push)
- Labels migration with colors and descriptions
- Releases migration with assets
- Issues migration with comments
- Pull requests migration (recreate as PR or convert to issue)
- Rate limiting with configurable delays
- Resume capability via `migration_state.json`
- State tracking for error recovery
- Configuration via `.env` file

---

## Pull Request History

| PR | Title | Status | Date |
|----|-------|--------|------|
| #17 | Fix 422 errors when base branch was renamed (master→main) | Merged | 2026-01-19 |
| #14 | Anonymize @mentions and change issue migration defaults | Merged | 2026-01-19 |
| #11 | Fix PyGithub internal retry handling for 403 errors | Merged | 2026-01-16 |
| #10 | Fix secondary rate limit (403) errors during migration | Merged | 2026-01-14 |
| #9 | Improve PR migration with unique branches and early diff detection | Merged | 2026-01-14 |
| #8 | Prevent duplicate migrations by scanning target repo | Merged | 2026-01-14 |
| #7 | Add detailed progress tracking for PR migration | Merged | 2026-01-14 |
| #6 | Fix AuthenticatedUser(login=None) error | Merged | 2026-01-14 |
| #5 | Fix forking to organizations | Merged | 2026-01-14 |
| #4 | Auto-enable issues on forked repositories | Merged | 2026-01-14 |
| #3 | Add limit for fetching most recent issues and PRs | Merged | 2026-01-14 |
| #2 | Add progress indicators during issue and PR fetching | Merged | 2026-01-14 |
| #1 | Add development workflow documentation | Merged | 2026-01-14 |

---

## Configuration Changes Over Time

| Setting | Initial | Current | PR |
|---------|---------|---------|-----|
| `MIN_DELAY_SECONDS` | 1.0 | 2.0 | #10 |
| `BULK_DELAY_SECONDS` | N/A | 5.0 | #10, #14 |
| `SECONDARY_RATE_LIMIT_COOLDOWN` | N/A | 120 | #10 |
| `RETRY_BACKOFF_FACTOR` | N/A | 60 | #11 |
| `MAX_COMMENTS_PER_ITEM` | N/A | 10 | #10, #14 |
| `MIGRATE_ISSUES` | True | False | #14 |
| `ITEM_LIMIT` | N/A | 1000 | #3 |
