import contextlib
import importlib.util
import json
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
import backend
import native_unlock as core
import official_unlock as official
import native_features as features
import native_cli_unlock as cli

spec = importlib.util.spec_from_file_location('migrate_state', ROOT/'scripts/migrate_state.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class PortableRuntime(unittest.TestCase):
    def test_state_is_independent_of_app_and_source_directory(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
            root = Path(temp).resolve()/'Application Support/Claude Native Unlock'
            for module, name in [(core,'ROOT'), (core,'WORK'), (official,'STATE'), (features,'STATE')]:
                stack.enter_context(patch.object(module, name, getattr(module,name)))
            backend.configure(root)
            self.assertEqual(official.STATE, root/'work/official-install')
            self.assertEqual(features.STATE, root/'work/native-features')
            self.assertTrue(core.WORK.is_dir())
            self.assertFalse(str(core.WORK).startswith(str(ROOT)))

    def test_redirected_state_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root/'real').mkdir()
            (root/'redirect').symlink_to(root/'real', target_is_directory=True)
            with self.assertRaisesRegex(ValueError, '符号链接'):
                backend.configure(root/'redirect')
            self.assertEqual(list((root/'real').iterdir()), [])

    def test_unknown_desktop_cannot_receive_an_adapter(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp)/'Claude.app'
            (app/'Contents').mkdir(parents=True)
            (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleShortVersionString':'99.0'}))
            with self.assertRaisesRegex(ValueError,'Unknown version'):
                core.source_adapter(app)


class CombinedStatus(unittest.TestCase):
    def test_reports_all_three_sections_in_one_call(self):
        with patch.object(official, 'status', return_value={
                'supported': True, 'version': '2.t', 'running': False}), \
             patch.object(cli, 'status', return_value={'enabled': True}), \
             patch.object(features, 'report', return_value={'features': []}) as report:
            result = backend.status_all()
        self.assertEqual(result['official']['version'], '2.t')
        self.assertTrue(result['cli']['enabled'])
        self.assertEqual(result['features']['features'], [])
        report.assert_called_once()

    def test_one_failing_section_does_not_hide_the_others(self):
        with patch.object(official, 'status', side_effect=ValueError('synthetic failure')), \
             patch.object(cli, 'status', return_value={'enabled': False}), \
             patch.object(features, 'report', return_value={'features': []}):
            result = backend.status_all()
        self.assertIn('error', result['official'])
        self.assertNotIn('synthetic failure', json.dumps(result))
        self.assertFalse(result['cli']['enabled'])
        self.assertIn('features', result)


class RecoveryMigration(unittest.TestCase):
    def test_configuration_journal_is_copied_without_source_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root/'legacy'
            journal = source/'work/native-features/test-browser.json'
            journal.parent.mkdir(parents=True)
            original = b'{"configId":"test","feature":"browser","beforePresent":false}\n'
            journal.write_bytes(original)
            destination = root/'app-state'
            result = migration.migrate(source,destination)
            self.assertEqual(result['migratedRecords'],1)
            self.assertEqual(journal.read_bytes(),original)
            self.assertEqual(json.loads((destination/'work/native-features/test-browser.json').read_text()),json.loads(original))
            with self.assertRaisesRegex(ValueError,'不覆盖'):
                migration.migrate(source,destination)

    def test_foreign_backup_refused_before_destination_created(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root/'legacy'
            journal = source/'work/official-install/manifest.json'
            journal.parent.mkdir(parents=True)
            journal.write_text(json.dumps({'backup':str(root/'unrelated.app')}))
            destination = root/'app-state'
            with self.assertRaisesRegex(ValueError,'备份路径'):
                migration.migrate(source,destination)
            self.assertFalse(destination.exists())

    def test_bad_signature_refused_before_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root/'legacy'
            journal = source/'work/official-install/manifest.json'
            journal.parent.mkdir(parents=True)
            journal.write_text(json.dumps({'backup':str(journal.parent/'official-backup-1.app')}))
            destination = root/'app-state'
            with patch.object(core,'verify_source',side_effect=ValueError('signature mismatch')):
                with self.assertRaisesRegex(ValueError,'signature mismatch'):
                    migration.migrate(source,destination)
            self.assertFalse(destination.exists())


if __name__ == '__main__':
    unittest.main()
