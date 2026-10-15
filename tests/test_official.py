import pathlib
import plistlib
import sys
import tempfile
import unittest
import json
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]/'src'))
import official_unlock as official


class OfficialSafety(unittest.TestCase):
    def test_rollback_refuses_running_application(self):
        with patch.object(official, 'running', return_value=True), patch.object(official, 'clone') as clone:
            with self.assertRaisesRegex(ValueError, '退出 Claude'):
                official.rollback_update()
            clone.assert_not_called()

    def test_rollback_rejects_foreign_or_symlink_prior_app(self):
        for prior in ['/tmp/retired-patched-1.app', str(official.STATE / 'retired-patched-link.app')]:
            with patch.object(official, 'running', return_value=False), \
                 patch.object(official, 'status', return_value={'enabled': True, 'supported': True, 'rollbackAvailable': True}), \
                 patch.object(official, 'manifest', return_value={'retired': prior}), \
                 patch.object(pathlib.Path, 'is_symlink', return_value=True), \
                 patch.object(official, 'clone') as clone:
                with self.assertRaisesRegex(ValueError, '路径异常'):
                    official.rollback_update()
                clone.assert_not_called()

    def test_rollback_swap_failure_retains_current_app(self):
        with tempfile.TemporaryDirectory() as directory:
            state = pathlib.Path(directory).resolve()
            target = state / 'Claude.app'; target.mkdir()
            (target / 'marker').write_text('current')
            prior = state / 'retired-patched-1.app'; prior.mkdir()
            record = {'retired': str(prior), 'installedFiles': {official.ASAR: 'new'},
                      'upgradeFromFiles': {official.ASAR: 'old'}, 'patchRevision': 2, 'upgradeFromRevision': 1}
            real_rename = pathlib.Path.rename
            def rename(path, destination):
                if path.name.startswith('rollback-prepared-'):
                    raise OSError('simulated rollback swap failure')
                return real_rename(path, destination)
            with patch.object(official, 'STATE', state), patch.object(official, 'TARGET', target), \
                 patch.object(official, 'manifest', return_value=record), \
                 patch.object(official, 'running', return_value=False), \
                 patch.object(official, 'status', return_value={'enabled': True, 'supported': True, 'rollbackAvailable': True}), \
                 patch.object(official, 'verify_patched'), \
                 patch.object(official, 'clone', side_effect=lambda source, destination: destination.mkdir()), \
                 patch.object(pathlib.Path, 'rename', rename):
                with self.assertRaisesRegex(OSError, 'simulated'):
                    official.rollback_update()
            self.assertEqual((target / 'marker').read_text(), 'current')
            self.assertEqual(json.loads((state / 'manifest.json').read_text()), record)

    def test_update_refuses_running_application(self):
        with patch.object(official, 'running', return_value=True), patch.object(official, 'clone') as clone:
            with self.assertRaisesRegex(ValueError, '退出 Claude'):
                official.update()
            clone.assert_not_called()

    def test_update_refuses_unverified_application(self):
        with patch.object(official, 'running', return_value=False), \
             patch.object(official, 'status', return_value={'enabled': True, 'supported': False}), \
             patch.object(official, 'clone') as clone:
            with self.assertRaisesRegex(ValueError, '已校验'):
                official.update()
            clone.assert_not_called()

    def test_update_interruption_before_swap_recognizes_old_install(self):
        record = {'phase': 'upgrade-prepared', 'installedFiles': {official.ASAR: 'new'},
                  'patchRevision': 2, 'upgradeFromFiles': {official.ASAR: 'old'}, 'upgradeFromRevision': 1}
        with patch.object(pathlib.Path, 'read_bytes', return_value=b'old'), \
             patch.object(official.core, 'sha', return_value='old'):
            old = official.effective_record(pathlib.Path('Synthetic.app'), record)
        self.assertEqual(old['installedFiles'][official.ASAR], 'old')
        self.assertEqual(old['patchRevision'], 1)
        self.assertEqual(record['installedFiles'][official.ASAR], 'new')

    def test_update_swap_failure_preserves_previous_app_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            state = pathlib.Path(directory).resolve()
            target = state / 'Claude.app'
            target.mkdir()
            (target / 'marker').write_text('previous')
            backup = state / 'official-backup-1.app'
            backup.mkdir()
            record = {'backup': str(backup), 'installedFiles': {official.ASAR: 'old'}, 'phase': 'enabled'}
            real_rename = pathlib.Path.rename
            def rename(path, destination):
                if path.name.startswith('upgrade-prepared-'):
                    raise OSError('simulated swap failure')
                return real_rename(path, destination)
            with patch.object(official, 'STATE', state), patch.object(official, 'TARGET', target), \
                 patch.object(official, 'manifest', return_value=record), \
                 patch.object(official, 'running', return_value=False), \
                 patch.object(official, 'status', return_value={'enabled': True, 'supported': True}), \
                 patch.object(official.core, 'verify_source'), patch.object(official, 'verify_patched'), \
                 patch.object(official.platform, 'machine', return_value='arm64'), \
                 patch.object(official.platform, 'mac_ver', return_value=('27.0.1', (), '')), \
                 patch.object(official, 'clone', side_effect=lambda source, destination: destination.mkdir()), \
                 patch.object(official, 'build', return_value={official.ASAR: 'new'}), \
                 patch.object(pathlib.Path, 'rename', rename):
                with self.assertRaisesRegex(OSError, 'simulated'):
                    official.update()
            self.assertEqual((target / 'marker').read_text(), 'previous')
            self.assertEqual(json.loads((state / 'manifest.json').read_text()), record)

    def test_interrupted_install_exposes_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            state = pathlib.Path(directory).resolve()
            with patch.object(official, 'STATE', state), \
                 patch.object(official, 'TARGET', state / 'Missing.app'), \
                 patch.object(official, 'manifest', return_value={'backup': str(state / 'official-backup-1.app')}), \
                 patch.object(official, 'running', return_value=False), \
                 patch.object(official.core, 'verify_source'):
                self.assertTrue(official.status()['recoveryRequired'])
                with self.assertRaisesRegex(ValueError, '先关闭开关'):
                    official.enable()

    def test_running_app_refuses_both_mutations(self):
        with patch.object(official, 'running', return_value=True), \
             patch.object(official, 'status', return_value={'enabled': False}), \
             patch.object(official, 'clone') as clone:
            for operation in [official.enable, official.disable]:
                with self.assertRaisesRegex(ValueError, '退出 Claude'):
                    operation()
            clone.assert_not_called()

    def test_unknown_version_refuses_enable(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory).resolve() / 'Claude.app'
            (target / 'Contents').mkdir(parents=True)
            (target / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleShortVersionString': '99.0'}))
            with patch.object(official, 'TARGET', target), \
                 patch.object(official, 'manifest', return_value=None), \
                 patch.object(official, 'running', return_value=False), \
                 patch.object(official, 'clone') as clone:
                self.assertFalse(official.status()['supported'])
                with self.assertRaisesRegex(ValueError, 'Unknown version'):
                    official.enable()
                clone.assert_not_called()

    def test_damaged_backup_refuses_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            state = pathlib.Path(directory).resolve()
            backup = state / 'official-backup-1.app'
            (backup / 'Contents').mkdir(parents=True)
            (backup / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleShortVersionString': '99.0'}))
            with patch.object(official, 'STATE', state), \
                 patch.object(official, 'manifest', return_value={'backup': str(backup)}), \
                 patch.object(official, 'running', return_value=False), \
                 patch.object(official, 'clone') as clone:
                with self.assertRaisesRegex(ValueError, 'Unknown version'):
                    official.disable()
                clone.assert_not_called()

    def test_foreign_backup_path_refuses_restore(self):
        with patch.object(official, 'manifest', return_value={'backup': '/tmp/official-backup-1.app'}), \
             patch.object(official, 'running', return_value=False), \
             patch.object(official, 'clone') as clone:
            with self.assertRaisesRegex(ValueError, '备份路径异常'):
                official.disable()
            clone.assert_not_called()

    def test_changed_install_refuses_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            state = pathlib.Path(directory).resolve()
            backup = state / 'official-backup-1.app'
            target = state / 'Claude.app'
            (target / 'Contents/Resources').mkdir(parents=True)
            (target / official.ASAR).write_bytes(b'changed application')
            (target / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleShortVersionString': official.core.VERSION}))
            record = {'backup': str(backup), 'installedFiles': {official.ASAR: 'expected'}}
            with patch.object(official, 'STATE', state), patch.object(official, 'TARGET', target), \
                 patch.object(official, 'manifest', return_value=record), \
                 patch.object(official, 'running', return_value=False), \
                 patch.object(official.core, 'verify_source'), \
                 patch.object(official, 'clone') as clone:
                with self.assertRaisesRegex(ValueError, '已安装文件发生变化'):
                    official.disable()
                self.assertEqual((target / official.ASAR).read_bytes(), b'changed application')
                clone.assert_not_called()

    def test_swap_failure_restores_current_application(self):
        with tempfile.TemporaryDirectory() as directory:
            state = pathlib.Path(directory).resolve()
            target = state / 'Claude.app'
            target.mkdir()
            (target / 'marker').write_text('patched app remains recoverable')
            backup = state / 'official-backup-1.app'
            backup.mkdir()
            record = {'backup': str(backup)}
            real_rename = pathlib.Path.rename
            def rename(path, destination):
                if path.name.startswith('restore-prepared-'):
                    raise OSError('simulated swap failure')
                return real_rename(path, destination)
            with patch.object(official, 'STATE', state), patch.object(official, 'TARGET', target), \
                 patch.object(official, 'manifest', return_value=record), \
                 patch.object(official, 'running', return_value=False), \
                 patch.object(official.core, 'verify_source'), \
                 patch.object(official, 'verify_patched'), \
                 patch.object(official.core, 'sha', return_value='patched'), \
                 patch.object(pathlib.Path, 'read_bytes', return_value=b'patched'), \
                 patch.object(official.plistlib, 'loads', return_value={'CFBundleShortVersionString': official.core.VERSION}), \
                 patch.object(official, 'clone', side_effect=lambda source, destination: destination.mkdir()), \
                 patch.object(pathlib.Path, 'rename', rename):
                with self.assertRaisesRegex(OSError, 'simulated'):
                    official.disable()
            self.assertEqual((target / 'marker').read_text(), 'patched app remains recoverable')


if __name__ == '__main__':
    unittest.main()
