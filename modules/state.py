"""State management for migration resume capability."""

import json
import os
from typing import Dict, Any, List
from datetime import datetime
from config import Config


class MigrationState:
    """Manages migration state for resume capability."""

    def __init__(self, state_file: str = None):
        """
        Initialize migration state manager.

        Args:
            state_file: Path to state file (defaults to Config.STATE_FILE)
        """
        self.state_file = state_file or Config.STATE_FILE
        self.state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        """Load state from file if it exists."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"⚠ Warning: Could not load state file: {str(e)}")
                return self._new_state()
        return self._new_state()

    def _new_state(self) -> Dict[str, Any]:
        """Create a new state structure."""
        return {
            'started_at': datetime.now().isoformat(),
            'source_repo': None,
            'target_repo': None,
            'completed_steps': [],
            'label_mapping': {},  # old_name -> new_name
            'issue_mapping': {},  # old_number -> new_number
            'pr_mapping': {},     # old_number -> new_number
            'release_mapping': {},  # old_tag -> new_tag
            'errors': []
        }

    def save(self):
        """Save current state to file."""
        try:
            self.state['updated_at'] = datetime.now().isoformat()
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            print(f"⚠ Warning: Could not save state file: {str(e)}")

    def set_source_repo(self, owner: str, repo: str):
        """Set source repository information."""
        self.state['source_repo'] = {'owner': owner, 'name': repo}
        self.save()

    def set_target_repo(self, owner: str, repo: str):
        """Set target repository information."""
        self.state['target_repo'] = {'owner': owner, 'name': repo}
        self.save()

    def mark_step_completed(self, step: str):
        """Mark a migration step as completed."""
        if step not in self.state['completed_steps']:
            self.state['completed_steps'].append(step)
            self.save()

    def is_step_completed(self, step: str) -> bool:
        """Check if a migration step was completed."""
        return step in self.state['completed_steps']

    def add_label_mapping(self, old_name: str, new_name: str):
        """Record a label mapping."""
        self.state['label_mapping'][old_name] = new_name
        self.save()

    def add_issue_mapping(self, old_number: int, new_number: int):
        """Record an issue number mapping."""
        self.state['issue_mapping'][str(old_number)] = new_number
        self.save()

    def add_pr_mapping(self, old_number: int, new_number: int):
        """Record a PR number mapping."""
        self.state['pr_mapping'][str(old_number)] = new_number
        self.save()

    def add_release_mapping(self, old_tag: str, new_tag: str):
        """Record a release mapping."""
        self.state['release_mapping'][old_tag] = new_tag
        self.save()

    def add_error(self, error: str):
        """Record an error that occurred during migration."""
        self.state['errors'].append({
            'timestamp': datetime.now().isoformat(),
            'error': error
        })
        self.save()

    def get_migrated_issues(self) -> List[int]:
        """Get list of issue numbers that have been migrated."""
        return [int(k) for k in self.state['issue_mapping'].keys()]

    def get_migrated_prs(self) -> List[int]:
        """Get list of PR numbers that have been migrated."""
        return [int(k) for k in self.state['pr_mapping'].keys()]

    def clear(self):
        """Clear the state file."""
        if os.path.exists(self.state_file):
            os.remove(self.state_file)
        self.state = self._new_state()

    def print_summary(self):
        """Print a summary of the migration state."""
        print("\n" + "=" * 60)
        print("MIGRATION STATE SUMMARY")
        print("=" * 60)

        if self.state.get('source_repo'):
            src = self.state['source_repo']
            print(f"Source: {src['owner']}/{src['name']}")

        if self.state.get('target_repo'):
            tgt = self.state['target_repo']
            print(f"Target: {tgt['owner']}/{tgt['name']}")

        print(f"\nCompleted steps: {len(self.state['completed_steps'])}")
        for step in self.state['completed_steps']:
            print(f"  ✓ {step}")

        print(f"\nMigrated items:")
        print(f"  - Labels: {len(self.state['label_mapping'])}")
        print(f"  - Issues: {len(self.state['issue_mapping'])}")
        print(f"  - Pull Requests: {len(self.state['pr_mapping'])}")
        print(f"  - Releases: {len(self.state['release_mapping'])}")

        if self.state['errors']:
            print(f"\n⚠ Errors encountered: {len(self.state['errors'])}")
            for err in self.state['errors'][-5:]:  # Show last 5 errors
                print(f"  - {err['error']}")

        print("=" * 60 + "\n")
