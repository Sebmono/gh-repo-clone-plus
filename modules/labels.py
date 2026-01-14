"""Labels migration functionality."""

from github import GithubException
from modules.rate_limiter import RateLimiter
from modules.state import MigrationState
from tqdm import tqdm


class LabelMigrator:
    """Handles migrating repository labels."""

    def __init__(self, rate_limiter: RateLimiter, state: MigrationState):
        """
        Initialize label migrator.

        Args:
            rate_limiter: Rate limiter instance
            state: Migration state manager
        """
        self.rate_limiter = rate_limiter
        self.state = state

    def migrate_labels(self, source_repo, target_repo):
        """
        Migrate all labels from source to target repository.

        Args:
            source_repo: Source repository object
            target_repo: Target repository object

        Returns:
            Number of labels migrated
        """
        step_name = "labels_migrated"
        if self.state.is_step_completed(step_name):
            print("✓ Labels already migrated (skipping)")
            return len(self.state.state['label_mapping'])

        try:
            print("\n🏷️  Migrating labels...")

            # Get all labels from source
            source_labels = list(source_repo.get_labels())
            print(f"   Found {len(source_labels)} labels in source repository")

            if not source_labels:
                print("   No labels to migrate")
                self.state.mark_step_completed(step_name)
                return 0

            # Get existing labels in target
            existing_labels = {label.name: label for label in target_repo.get_labels()}

            migrated_count = 0
            skipped_count = 0

            # Migrate each label
            for label in tqdm(source_labels, desc="   Migrating labels"):
                try:
                    if label.name in existing_labels:
                        # Label already exists, update it
                        self.rate_limiter.wait_for_write()
                        existing_labels[label.name].edit(
                            name=label.name,
                            color=label.color,
                            description=label.description or ""
                        )
                        skipped_count += 1
                    else:
                        # Create new label
                        self.rate_limiter.wait_for_write()
                        target_repo.create_label(
                            name=label.name,
                            color=label.color,
                            description=label.description or ""
                        )
                        migrated_count += 1

                    self.state.add_label_mapping(label.name, label.name)

                except GithubException as e:
                    error_msg = f"Failed to migrate label '{label.name}': {str(e)}"
                    print(f"\n   ⚠ {error_msg}")
                    self.state.add_error(error_msg)
                    continue

            self.state.mark_step_completed(step_name)

            print(f"✓ Labels migrated: {migrated_count} created, {skipped_count} updated")
            return migrated_count + skipped_count

        except Exception as e:
            error_msg = f"Label migration failed: {str(e)}"
            self.state.add_error(error_msg)
            raise Exception(error_msg)
