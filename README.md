# gh-repo-clone-plus
A utility for copying public repositories, with their issues, pull requests and releases, into an organization you control.

---

# GitHub Repository Migration Utility

A Python utility to fork GitHub repositories and migrate all metadata including Issues, Pull Requests, Releases, and Labels.

## Features

- **Fork Creation**: Automatically forks a repository using the GitHub API
- **Labels Migration**: Copies all labels with their colors and descriptions
- **Releases Migration**: Migrates releases including tags and assets
- **Issues Migration**: Transfers all issues with comments, labels, and metadata
- **Pull Requests Migration**: Recreates PRs or converts them to issues with full metadata
- **Rate Limiting**: Built-in delays and rate limit handling to prevent API blocks
- **Resume Capability**: Can resume interrupted migrations from where they left off
- **State Tracking**: Saves progress to allow for error recovery

## What Gets Migrated

✅ **Migrated:**
- Repository fork (code, commit history, branches)
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

## Prerequisites

1. **Python 3.8 or higher** (you have 3.10.6 ✓)
2. **GitHub Personal Access Token** with these scopes:
   - `repo` (Full control of private repositories)
   - `read:org` (Read org and team membership)
   - `read:user` (Read user profile data)

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
   TARGET_REPO=my-custom-fork-name
   ```

### Step 3: Verify Setup

Test your configuration:

```bash
python migrate.py --help
```

You should see usage instructions without any errors.

## Usage

### Basic Usage

Fork and migrate a repository:

```bash
python migrate.py https://github.com/owner/repo
```

Or use the shorthand format:

```bash
python migrate.py owner/repo
```

### Advanced Options

**Specify target repository name:**
```bash
python migrate.py owner/repo --target-name my-fork
```

**Specify target owner (for organizations):**
```bash
python migrate.py owner/repo --target-owner my-organization
```

**Include issues (opt-in):**
```bash
python migrate.py owner/repo --include-issues
```

**Skip certain migrations:**
```bash
python migrate.py owner/repo --skip-prs
python migrate.py owner/repo --skip-releases
```

**Resume interrupted migration:**
```bash
python migrate.py owner/repo --resume
```

**Clear saved state and start fresh:**
```bash
python migrate.py owner/repo --clear-state
```

### Complete Examples

1. **Fork a public repository:**
   ```bash
   python migrate.py https://github.com/octocat/Hello-World
   ```

2. **Fork with a custom name:**
   ```bash
   python migrate.py octocat/Hello-World --target-name my-hello-world
   ```

3. **Fork to an organization:**
   ```bash
   python migrate.py octocat/Hello-World --target-owner mycompany
   ```

4. **Migrate only labels (issues opt-in):**
   ```bash
   python migrate.py octocat/Hello-World --skip-prs --skip-releases
   ```

5. **Migrate pull requests, labels, releases, and issues:**
   ```bash
   python migrate.py octocat/Hello-World --include-issues
   ```

## Migration Process

The utility performs these steps in order:

1. **Authentication**: Verifies your GitHub token
2. **Fork Creation**: Creates the fork using GitHub API
3. **Labels Migration**: Copies all labels
4. **Releases Migration**: Migrates releases and assets
5. **Issues Migration**: Transfers issues with comments (only when included)
6. **Pull Requests Migration**: Recreates or converts PRs

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

## Troubleshooting

### "GITHUB_TOKEN is required" Error

**Solution**: Make sure you've created a `.env` file with your token:
```bash
copy .env.example .env
# Then edit .env and add your token
```

### "Rate limit exceeded" Error

**Solution**: The script has built-in rate limiting, but if you hit limits:
- Wait for the rate limit to reset (shown in error message)
- Run with `--resume` to continue

### "Module not found" Error

**Solution**: Install dependencies:
```bash
pip install -r requirements.txt
```

### Fork Already Exists

The script will detect existing forks and use them instead of creating duplicates.

### Migration Interrupted

**Solution**: Use the `--resume` flag:
```bash
python migrate.py owner/repo --resume
```

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
- **Safety**: Read-only on source repo, only writes to the fork
- **Reversibility**: Original repo is never modified
- **Multiple Runs**: Safe to run multiple times (detects existing items)

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
