"""Releases migration functionality."""

import requests
from github import GithubException
from modules.rate_limiter import RateLimiter
from modules.state import MigrationState
from tqdm import tqdm


class ReleaseMigrator:
    """Handles migrating repository releases."""

    def __init__(self, rate_limiter: RateLimiter, state: MigrationState):
        """
        Initialize release migrator.

        Args:
            rate_limiter: Rate limiter instance
            state: Migration state manager
        """
        self.rate_limiter = rate_limiter
        self.state = state

    def migrate_releases(self, source_repo, target_repo, limit=None):
        """
        Migrate releases from source to target repository.

        Args:
            source_repo: Source repository object
            target_repo: Target repository object
            limit: Maximum number of releases to migrate (None = all)

        Returns:
            Number of releases migrated
        """
        step_name = "releases_migrated"
        if self.state.is_step_completed(step_name):
            print("✓ Releases already migrated (skipping)")
            return len(self.state.state['release_mapping'])

        try:
            print("\n📦 Migrating releases...")

            # Get releases from source (with optional limit)
            all_releases = list(source_repo.get_releases())
            if limit and limit > 0:
                source_releases = all_releases[:limit]
                print(f"   Found {len(all_releases)} releases, migrating {len(source_releases)} (limited)")
            else:
                source_releases = all_releases
                print(f"   Found {len(source_releases)} releases in source repository")

            if not source_releases:
                print("   No releases to migrate")
                self.state.mark_step_completed(step_name)
                return 0

            # Get existing releases in target
            existing_releases = {rel.tag_name: rel for rel in target_repo.get_releases()}

            migrated_count = 0

            # Migrate each release
            for release in tqdm(source_releases, desc="   Migrating releases"):
                try:
                    # Skip if already exists
                    if release.tag_name in existing_releases:
                        print(f"\n   ⚠ Release {release.tag_name} already exists (skipping)")
                        self.state.add_release_mapping(release.tag_name, release.tag_name)
                        continue

                    # Verify the tag exists in target repo
                    try:
                        target_repo.get_git_ref(f"tags/{release.tag_name}")
                    except GithubException:
                        # Tag doesn't exist in target, try to create it
                        try:
                            # Get the commit SHA that the tag points to
                            source_tag = source_repo.get_git_ref(f"tags/{release.tag_name}")
                            tag_sha = source_tag.object.sha

                            # Check if commit exists in target
                            try:
                                target_repo.get_commit(tag_sha)
                                # Commit exists, create the tag reference
                                self.rate_limiter.wait_for_write()
                                target_repo.create_git_ref(
                                    ref=f"refs/tags/{release.tag_name}",
                                    sha=tag_sha
                                )
                            except GithubException:
                                print(f"\n   ⚠ Cannot create release {release.tag_name}: "
                                      f"commit {tag_sha} not found in target (skipping)")
                                continue

                        except Exception as e:
                            print(f"\n   ⚠ Failed to create tag {release.tag_name}: {str(e)} (skipping)")
                            continue

                    # Create the release
                    self.rate_limiter.wait_for_write()

                    # Prepare release notes with attribution
                    body = f"*Originally released by @{release.author.login} on {release.created_at.strftime('%Y-%m-%d')}*\n\n"
                    if release.body:
                        body += release.body
                    else:
                        body += "No release notes provided."

                    new_release = target_repo.create_git_release(
                        tag=release.tag_name,
                        name=release.title or release.tag_name,
                        message=body,
                        draft=release.draft,
                        prerelease=release.prerelease
                    )

                    # Migrate release assets
                    if release.get_assets().totalCount > 0:
                        self._migrate_assets(release, new_release, target_repo)

                    self.state.add_release_mapping(release.tag_name, release.tag_name)
                    migrated_count += 1

                except GithubException as e:
                    error_msg = f"Failed to migrate release '{release.tag_name}': {str(e)}"
                    print(f"\n   ⚠ {error_msg}")
                    self.state.add_error(error_msg)
                    continue
                except Exception as e:
                    error_msg = f"Unexpected error migrating release '{release.tag_name}': {str(e)}"
                    print(f"\n   ⚠ {error_msg}")
                    self.state.add_error(error_msg)
                    continue

            self.state.mark_step_completed(step_name)

            print(f"✓ Releases migrated: {migrated_count}")
            return migrated_count

        except Exception as e:
            error_msg = f"Release migration failed: {str(e)}"
            self.state.add_error(error_msg)
            raise Exception(error_msg)

    def _migrate_assets(self, source_release, target_release, target_repo):
        """
        Migrate release assets from source to target release.

        Args:
            source_release: Source release object
            target_release: Target release object
            target_repo: Target repository object
        """
        assets = list(source_release.get_assets())
        print(f"\n      Migrating {len(assets)} assets for {source_release.tag_name}...")

        for asset in assets:
            try:
                # Download asset
                print(f"      - Downloading {asset.name}...", end='', flush=True)
                response = requests.get(asset.browser_download_url)
                response.raise_for_status()
                print(" Done")

                # Upload to target release
                print(f"      - Uploading {asset.name}...", end='', flush=True)
                self.rate_limiter.wait_for_write()
                target_release.upload_asset(
                    path=None,
                    label=asset.label or asset.name,
                    content_type=asset.content_type,
                    name=asset.name,
                    file_like=response.content
                )
                print(" Done")

            except Exception as e:
                print(f"\n      ⚠ Failed to migrate asset '{asset.name}': {str(e)}")
                self.state.add_error(f"Failed to migrate asset '{asset.name}' for release '{source_release.tag_name}': {str(e)}")
                continue
