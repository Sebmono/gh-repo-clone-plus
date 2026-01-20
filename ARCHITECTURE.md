# Architecture & Code Structure

This document describes the architecture and code organization of the GitHub Repository Migration Utility.

## Project Structure

```
gh-repo-clone-plus/
├── migrate.py              # Main entry point and CLI
├── config.py               # Configuration management
├── requirements.txt        # Python dependencies
├── .env.example            # Example environment file
├── .gitignore              # Git ignore rules
│
├── modules/                # Core migration modules
│   ├── auth.py             # GitHub authentication
│   ├── rate_limiter.py     # API rate limiting
│   ├── state.py            # Migration state tracking
│   ├── fork.py             # Repository forking
│   ├── labels.py           # Labels migration
│   ├── releases.py         # Releases migration
│   ├── issues.py           # Issues migration
│   ├── pull_requests.py    # Pull requests migration
│   └── text_utils.py       # Text processing utilities
│
├── ARCHITECTURE.md         # Architecture and module reference
├── CHANGELOG.md            # Change history

# Runtime state (not committed)
# migration_state.json
```

## Module Descriptions

### `migrate.py` - Main Entry Point

The CLI entry point that orchestrates the migration process.

**Responsibilities:**
- Parse command-line arguments
- Initialize all components
- Execute migration steps in order
- Handle interrupts and errors
- Print progress and summary

**Key Functions:**
- `parse_arguments()` - CLI argument parsing
- `main()` - Main migration orchestration

### `config.py` - Configuration

Centralized configuration management.

**Key Settings:**

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `GITHUB_TOKEN` | str | env | GitHub Personal Access Token |
| `MIN_DELAY_SECONDS` | float | 2.0 | Delay between write operations |
| `BULK_DELAY_SECONDS` | float | 5.0 | Delay for bulk operations |
| `RATE_LIMIT_BUFFER` | int | 100 | Stop if requests drop below this |
| `SECONDARY_RATE_LIMIT_COOLDOWN` | int | 120 | Cooldown on 403 (seconds) |
| `RETRY_BACKOFF_FACTOR` | int | 60 | PyGithub retry backoff |
| `MAX_COMMENTS_PER_ITEM` | int | 10 | Max comments per issue/PR |
| `MIGRATE_ISSUES` | bool | False | Whether to migrate issues |
| `ITEM_LIMIT` | int | 1000 | Max items to fetch |

### `modules/auth.py` - Authentication

Handles GitHub API authentication.

**Class: `GitHubAuthenticator`**
- Initializes PyGithub client with token
- Configures custom retry behavior for 403 handling
- Verifies token validity and permissions

**Key Implementation:**
```python
# Custom retry configuration for secondary rate limits
retry = Retry(
    total=2,
    status_forcelist=[403, 500, 502, 503, 504],
    backoff_factor=Config.RETRY_BACKOFF_FACTOR,
    respect_retry_after_header=True,
)
self.github = Github(auth=self.auth, retry=retry)
```

### `modules/rate_limiter.py` - Rate Limiting

Manages API rate limits to prevent blocks.

**Class: `RateLimiter`**

**Key Methods:**
- `wait_for_write(is_bulk_operation)` - Wait before write operations
- `handle_secondary_rate_limit()` - Handle 403 with cooldown
- `reset_consecutive_writes()` - Reset bulk operation counter

**Features:**
- Tracks consecutive writes for bulk detection
- Implements cooldown for secondary rate limits
- Checks remaining API quota before operations

### `modules/state.py` - State Management

Tracks migration progress for resume capability.

**Class: `MigrationState`**

**State Structure:**
```json
{
  "started_at": "2026-01-14T14:12:06",
  "source_repo": {"owner": "...", "name": "..."},
  "target_repo": {"owner": "...", "name": "..."},
  "completed_steps": ["fork_created", "labels_migrated", ...],
  "label_mapping": {"old_name": "new_name"},
  "issue_mapping": {"123": 456},
  "pr_mapping": {"789": 101},
  "release_mapping": {},
  "errors": [],
  "updated_at": "2026-01-14T14:19:41"
}
```

**Key Methods:**
- `is_step_completed(step_name)` - Check if step was done
- `mark_step_completed(step_name)` - Mark step as done
- `add_issue_mapping(old, new)` - Track issue number mapping
- `add_pr_mapping(old, new)` - Track PR number mapping

### `modules/fork.py` - Repository Forking

Creates forks and handles existing forks.

**Class: `RepositoryForker`**

**Key Features:**
- Detects existing forks
- Supports personal accounts and organizations
- Auto-enables issues on forks
- Waits for fork to be ready

**Key Methods:**
- `fork_repository(source_owner, source_repo, target_owner, target_name)`
- `get_repository(owner, repo)`

### `modules/labels.py` - Labels Migration

Migrates repository labels.

**Class: `LabelMigrator`**

**Migrated Data:**
- Label name
- Color (hex code)
- Description

### `modules/releases.py` - Releases Migration

Migrates releases and assets.

**Class: `ReleaseMigrator`**

**Migrated Data:**
- Release tag
- Release name
- Release body
- Draft/prerelease status
- Release assets (binary files)

### `modules/issues.py` - Issues Migration

Migrates issues with comments.

**Class: `IssueMigrator`**

**Key Features:**
- Excludes pull requests from issue list
- Detects already-migrated issues by URL pattern
- Migrates comments (limited to `MAX_COMMENTS_PER_ITEM`)
- Anonymizes @mentions to prevent notifications
- Preserves open/closed status

**Key Methods:**
- `migrate_issues(source_repo, target_repo, limit)`
- `_find_already_migrated_issues(target_repo, source_repo)`
- `_format_issue_body(issue, source_repo)`
- `_migrate_comments(source_issue, target_issue)`

### `modules/pull_requests.py` - Pull Requests Migration

The most complex module - handles PR migration.

**Class: `PullRequestMigrator`**

**Migration Strategy:**
1. Try to recreate as actual PR
2. If that fails, convert to issue

**Key Features:**
- Unique branch names (append PR number) to avoid conflicts
- Early diff detection to skip PRs with no commits
- Branch name resolution for master→main renames
- Detects already-migrated PRs by URL pattern
- Migrates both issue comments and review comments
- Anonymizes @mentions

**Key Methods:**
- `migrate_pull_requests(source_repo, target_repo, limit)`
- `_recreate_as_pr(pr, source_repo, target_repo)`
- `_create_pr_as_issue(pr, source_repo, target_repo)`
- `_resolve_branch_name(target_repo, branch_name)`
- `_create_placeholder_branch(target_repo, branch_name, commit_sha, base_branch)`

**Branch Resolution Logic:**
```python
def _resolve_branch_name(self, target_repo, branch_name):
    # Handle master→main renames
    branch_mappings = {'master': 'main', 'main': 'master'}

    # Try original name first
    # If redirected, use actual name
    # Fall back to default branch
```

### `modules/text_utils.py` - Text Utilities

Text processing functions.

**Key Functions:**
- `anonymize_mentions(text)` - Convert `@user` to `+user`

**Pattern Used:**
```python
pattern = r'@([a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?)'
return re.sub(pattern, r'+\1', text)
```

## Data Flow

```
┌─────────────┐
│  migrate.py │
└──────┬──────┘
       │
       ▼
┌─────────────────┐     ┌──────────────┐
│ GitHubAuthenticator │◄───│   config.py   │
└────────┬────────┘     └──────────────┘
         │
         ▼
┌─────────────────┐
│   RateLimiter   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌──────────────┐
│ MigrationState  │◄───►│ state.json   │
└────────┬────────┘     └──────────────┘
         │
         ▼
┌────────────────────────────────────────────┐
│              Migration Steps               │
├────────────┬───────────┬──────────┬────────┤
│ RepositoryForker │ LabelMigrator │ ReleaseMigrator │
├────────────┴───────────┴──────────┴────────┤
│ IssueMigrator │ PullRequestMigrator │
└────────────────────────────────────────────┘
```

## Error Handling

### Rate Limit Handling

1. **Primary Rate Limit**: Check `rate_limit.core.remaining` before operations
2. **Secondary Rate Limit (403)**:
   - PyGithub's internal retry with backoff
   - Application-level cooldown (120s)
   - Retry operation after cooldown

### Resume Capability

- State saved to `migration_state.json` after each step
- `--resume` flag loads state and continues
- `--clear-state` flag starts fresh

### Error Recovery

- Individual item failures logged but don't stop migration
- Errors stored in state file for review
- Safe to re-run (detects existing items)

## CLI Arguments

| Argument | Description |
|----------|-------------|
| `source_repo` | Source repository (URL or owner/repo) |
| `--target-owner` | Target owner (default: authenticated user) |
| `--target-name` | Target repo name (default: source name) |
| `--include-issues` | Include issues in migration |
| `--skip-labels` | Skip labels migration |
| `--skip-releases` | Skip releases migration |
| `--skip-prs` | Skip pull requests migration |
| `--limit-items N` | Limit to N most recent items (0 = no limit) |
| `--resume` | Resume from saved state |
| `--clear-state` | Clear state and start fresh |

## Testing Recommendations

### Quick Test
```bash
python migrate.py octocat/Hello-World --limit-items 10 --clear-state
```

### Full Test with Issues
```bash
python migrate.py octocat/Hello-World --include-issues --limit-items 50
```

### Large Repository
```bash
python migrate.py rails/rails --limit-items 100
# Note: Large repos may hit rate limits; use --resume if interrupted
```

## Future Considerations

- Wiki migration
- GitHub Actions workflows
- Project boards
- Discussions
- Parallel processing for faster migration
