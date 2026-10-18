import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
import official_unlock as official


class Retention(unittest.TestCase):
    def test_prune_keeps_current_backup_and_removes_old_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state = pathlib.Path(directory).resolve()
            current = state / 'official-backup-current.app'
            old = state / 'official-backup-old.app'
            retired = state / 'retired-patched-1.app'
            rollback = state / 'rollback-retired-1.app'
            report = state / 'adaptation-report-1.json'
            manifest = state / 'previous-version-manifest-1.json'
            for path in (current, old, retired, rollback):
                path.mkdir()
            report.write_text('{}')
            manifest.write_text('{}')
            with patch.object(official, 'STATE', state):
                official.prune_old_state(current)
            self.assertTrue(current.is_dir())
            for path in (old, retired, rollback, report, manifest):
                self.assertFalse(path.exists(), path)

    def test_prune_skips_symlinks(self):
        with tempfile.TemporaryDirectory() as directory, \
                tempfile.TemporaryDirectory() as outside_directory:
            state = pathlib.Path(directory).resolve()
            outside = pathlib.Path(outside_directory).resolve()
            link = state / 'official-backup-link.app'
            link.symlink_to(outside, target_is_directory=True)
            with patch.object(official, 'STATE', state):
                official.prune_old_state(None)
            self.assertTrue(outside.is_dir())
            self.assertTrue(link.is_symlink())


if __name__ == '__main__':
    unittest.main()
