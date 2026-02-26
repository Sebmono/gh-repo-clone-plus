# gh-repo-clone-plus
A utility for copying public repositories, with their issues, pull requests and releases, into an organization you control.

---

# GitHub Repository Migration Utility

A Python utility to clone GitHub repositories and create Internal copies in a target organization, then migrate all metadata including Issues, Pull Requests, Releases, and Labels.

## Key Feature: Internal Repository Creation

Unlike GitHub's fork API (which forces forks of public repos to be public), this utility:
1. Clones the source repository locally
2. Creates a new **Internal** repository in your target organization
3. Pushes all branches and tags to the new repository
4. Migrates all metadata (Issues, PRs, Releases, Labels)

This allows organizations to maintain Internal copies of public repositories.

## Features

- **Internal Repository Creation**: Creates Internal repos (not public forks)
- **Labels Migration**: Copies all labels with their colors and descriptions
- **Releases Migration**: Migrates releases including tags and assets
- **Issues Migration**: Transfers all issues with comments, labels, and metadata
- **Pull Requests Migration**: Recreates PRs or converts them to issues with full metadata
- **Rate Limiting**: Built-in delays and rate limit handling to prevent API blocks
- **Resume Capability**: Use `--resume` to continue interrupted migrations
- **Fresh Start Default**: Each run starts fresh unless `--resume` is specified
- **Item Limiting**: Control how many releases/issues/PRs to migrate with `--limit-items`

## What Gets Migrated

✅ **Migrated:**
- Repository code, commit history, all branches and tags
- All labels with colors and descriptions
- All releases with their assets
- All issues with comments and labels
- All pull requests (recreated or converted to issues)
- Original author attribution (noted in descriptions)
- Open/closed status

⚠️ **Limitations:**
- Original timestamps cannot be preserved (noted in descriptions)
- Issue/PR numbers may change (old → new mapping tracked)
- Original author attribution appears in descriptions (all items created by your account)
- Cross-repo references (#123) will still point to original repo
- No upstream link (this is not a fork, it's an independent copy)

## Prerequisites

1. **Python 3.8 or higher** (you have 3.10.6 ✓)
2. **GitHub Personal Access Token** with these scopes:
   - `repo` (Full control of private repositories)
   - `workflow` (Update GitHub Action workflows) - required to push .github/workflows files
   - `read:org` (Read org and team membership)
   - `read:user` (Read user profile data)

**Note:** GitHub Actions are automatically disabled on the target repository, so workflows will be preserved but cannot run.

## Installation & Setup

### Step 1: Install Python Dependencies

Open Command Prompt or PowerShell in this directory and run:

```bash
pip install -r requirements.txt
```

This installs:
- `PyGithub` - GitHub API library
- `requests` - HTTP library
- `python-dotenv` - Environment variable management
- `tqdm` - Progress bars

### Step 2: Configure Your GitHub Token

1. **Create a `.env` file** by copying the example:
   ```bash
   copy .env.example .env
   ```

2. **Edit the `.env` file** (use Notepad or any text editor):
   - Open: `.env`
   - Replace `your_token_here` with your actual GitHub Personal Access Token
   - Save the file

   Example `.env` file:
   ```
   GITHUB_TOKEN=ghp_1234567890abcdefghijklmnopqrstuvwxyz
   ```

3. **Optional settings** in `.env`:
   ```
   # Specify target owner (defaults to your authenticated user)
   TARGET_OWNER=your-username-or-org

   # Specify target repo name (defaults to source repo name)
   TARGET_REPO=my-custom-repo-name
   ```

### Step 3: Verify Setup

Test your configuration:

```bash
python migrate.py --help
```

You should see usage instructions without any errors.

## Usage

### Basic Usage

Clone and migrate a repository to your organization:

```bash
python migrate.py https://github.com/owner/repo
```

The `source_repo` argument also accepts:

- `owner/repo`
- a trailing `.git` suffix (for example, `https://github.com/owner/repo.git`)

Or use the shorthand format:

```bash
python migrate.py owner/repo
```

### Command Line Options

| Option | Description |
|--------|-------------|
| `source_repo` | Source repository (required). URL or `owner/repo` format |
| `--target-owner` | Target organization for the Internal repo (defaults to authenticated user) |
| `--target-name` | Custom name for target repo (defaults to source repo name) |
| `--include-issues` | Include issues in migration (not migrated by default) |
| `--skip-labels` | Skip migrating labels |
| `--skip-releases` | Skip migrating releases |
| `--skip-prs` | Skip migrating pull requests |
| `--limit-items N` | Limit releases/issues/PRs to most recent N items (default: 1000, use 0 for no limit) |
| `--resume` | Resume from saved state (default behavior starts fresh each run) |

### Examples

**Basic migration to your organization:**
```bash
python migrate.py owner/repo --target-owner my-organization
```

**Limit to 10 most recent items (good for testing):**
```bash
python migrate.py owner/repo --target-owner myorg --limit-items 10
```

**Include issues (opt-in):**
```bash
python migrate.py owner/repo --include-issues
```

**Custom target name:**
```bash
python migrate.py owner/repo --target-name my-copy
```

**Resume an interrupted migration:**
```bash
python migrate.py owner/repo --resume
```

**Migrate only labels:**
```bash
python migrate.py owner/repo --skip-prs --skip-releases
```

## Migration Process

The utility performs these steps in order:

1. **Authentication**: Verifies your GitHub token
2. **Repository Creation**: Clones source repo and creates Internal copy in target org
3. **Labels Migration**: Copies all labels
4. **Releases Migration**: Migrates releases and assets
5. **Issues Migration**: Transfers issues with comments (only when included)
6. **Pull Requests Migration**: Recreates or converts PRs

After pushing branches and tags, the target repository default branch is set to match the source repository.

Each step is tracked in `migration_state.json` for resume capability.

## Migrated Text Format

### Mention anonymization (`@user` → `+user`)

When migrating issue/PR bodies and comments, the utility replaces GitHub `@username` mentions with `+username`.

The migrated content stays readable, and the original users do not receive GitHub notifications from the migrated text.

### Comment limit

The utility migrates up to 10 comments per issue or pull request.

## Understanding the Output

During migration, you'll see:

```
✓ - Completed successfully
⚠ - Warning (item skipped or partial success)
ℹ - Information message
❌ - Error
```

Progress bars show the current operation status.

## Project continuity

- Architecture overview: See [ARCHITECTURE.md](./ARCHITECTURE.md)
- Change history: See [CHANGELOG.md](./CHANGELOG.md)

## Troubleshooting

### "GITHUB_TOKEN is required" Error

**Solution**: Make sure you've created a `.env` file with your token:
```bash
copy .env.example .env
# Then edit .env and add your token
```

### Pull requests fail with 422 "field base invalid"

This can happen when the source PR targets a base branch that was renamed in the target repo (for example, `master` → `main`).

**What the utility does**: When migrating pull requests, the utility resolves the actual base branch name in the target repo and uses that name for PR creation.

**What you see in output**: If a remap occurs, the PR migration log prints `base OK (master→main)`.

### "Rate limit exceeded" Error

**Solution**: The script has built-in rate limiting, but if you hit limits:
- Wait for the rate limit to reset (shown in error message)
- Run with `--resume` to continue

### "Module not found" Error

**Solution**: Install dependencies:
```bash
pip install -r requirements.txt
```

### Repository Already Exists

The script will detect existing target repositories and use them instead of creating duplicates.

### Migration Interrupted

**Solution**: Use the `--resume` flag to continue from where it left off:
```bash
python migrate.py owner/repo --resume
```

Note: Without `--resume`, each run starts fresh. The `--resume` flag is required to continue an interrupted migration.

## Rate Limiting

- The utility automatically handles GitHub API rate limits
- Includes a minimum delay between write operations (`Config.MIN_DELAY_SECONDS`, default: 2.0 seconds)
- Uses an additional delay during bulk write operations (`Config.BULK_DELAY_SECONDS`, default: 5.0 seconds)
- Checks rate limit status before operations
- Waits when GitHub returns a secondary rate limit response (HTTP 403)

### Secondary rate limits (HTTP 403)

GitHub may return HTTP 403 responses when abuse detection / secondary rate limiting triggers.

The utility handles this in two ways:

1. It applies a cooldown (`Config.SECONDARY_RATE_LIMIT_COOLDOWN`, default: 120 seconds).
2. It configures PyGithub's internal (urllib3) retry behavior to back off between retries.

To tune PyGithub's retry backoff, set `Config.RETRY_BACKOFF_FACTOR` (default: 60). The `urllib3.util.retry.Retry` backoff behavior uses this factor.

Note: The authenticator configures PyGithub with `Retry(total=2, status_forcelist=[403, 500, 502, 503, 504], respect_retry_after_header=True, backoff_factor=Config.RETRY_BACKOFF_FACTOR)`.

**Limits:**
- Authenticated: 5,000 requests/hour
- Secondary limit: 900 points/minute

**Tips:**
- Large repositories may take significant time
- The script can run for hours for repos with many issues/PRs
- Use `--resume` to continue if interrupted

## Files Created During Migration

- `migration_state.json` - Tracks migration progress for resume capability
- `.env` - Your configuration (never commit this file!)

## Notes for Product Managers

- **Time Estimate**: Migration time depends on repository size. A repo with 100 issues/PRs may take 10-15 minutes
- **Cost**: Free (uses GitHub's free API tier)
- **Safety**: Read-only on source repo, only writes to the new Internal repository
- **Reversibility**: Original repo is never modified
- **Multiple Runs**: Safe to run multiple times (detects existing items)
- **Visibility**: Target repository is created as "Internal" (visible to org members only)

## Security Notes

⚠️ **Important:**
- Never commit your `.env` file to git
- Never share your GitHub Personal Access Token
- The `.gitignore` file already excludes `.env` and `migration_state.json`
- Keep your token secure like a password

## Support & Issues

If you encounter issues:

1. Check the error message carefully
2. Try with `--resume` if interrupted
3. Verify your token has correct permissions
4. Check GitHub API status: https://www.githubstatus.com/

## Technical Details

For technical users interested in the implementation:

- **Architecture**: Modular design with separate components for each migration type
- **API Library**: Uses PyGithub for GitHub API interactions
- **State Management**: JSON-based state tracking for resume capability
- **Error Handling**: Comprehensive error catching with detailed logging
- **Rate Limiting**: Proactive rate limit checking with automatic backoff


## License

This utility copies repositories into an organization you control.
