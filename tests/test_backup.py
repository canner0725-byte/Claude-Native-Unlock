import contextlib
import json
import pathlib
import plistlib
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
import official_unlock as official


class BackupSafety(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.state = pathlib.Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.target = self.make_app('Claude.app', official.core.ADAPTER_37337['version'], b'patched')
        self.backup = self.make_app('official-backup-1.app', official.core.ADAPTER_37337['version'], b'original')
        self.record = {'version': official.core.ADAPTER_37337['version'], 'patchRevision': 8,
                       'phase': 'enabled', 'backup': str(self.backup),
                       # 安装记录保存的官方原版指纹：恢复以此为准，不查登记表。
                       'original': {'version': official.core.ADAPTER_37337['version'],
                                    'asarHash': official.core.sha(b'original'),
                                    'files': {}, 'sourceFiles': {}},
                       'installedFiles': {official.ASAR: official.core.sha(b'patched')}}
        for module, name, value in [(official, 'STATE', self.state), (official, 'TARGET', self.target),
                                    (official, 'running', lambda: False),
                                    (official, 'manifest', lambda: self.record),
                                    (official, 'verify_patched', lambda *args: None),
                                    (official, 'prepare_identity', lambda: None),
                                    (official.core, 'verify_source', lambda *args: None),
                                    (official.core, 'verify_official_signature', lambda *args: None)]:
            self.stack.enter_context(patch.object(module, name, value))

    def make_app(self, name, version, data):
        app = self.state / name
        (app / 'Contents/Resources').mkdir(parents=True)
        (app / official.ASAR).write_bytes(data)
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleShortVersionString': version}))
        return app

    def test_other_supported_version_cannot_restore_over_current_version(self):
        old = self.make_app('official-backup-old.app', official.core.VERSION, b'old official')
        self.record['backup'] = str(old)
        with patch.object(official, 'clone', side_effect=shutil.copytree) as clone:
            with self.assertRaisesRegex(ValueError, '备份版本'):
                official.disable()
            clone.assert_not_called()
        self.assertEqual((self.target / official.ASAR).read_bytes(), b'patched')

    def test_other_supported_version_cannot_supply_patch_update(self):
        old = self.make_app('official-backup-old.app', official.core.VERSION, b'old official')
        self.record.update(backup=str(old), patchRevision=7)
        current = {'enabled': True, 'supported': True, 'version': self.record['version']}
        with patch.object(official, 'status', return_value=current), \
             patch.object(official, 'host_machine', return_value='arm64'), \
             patch.object(official, 'host_mac_version', return_value='27.0.1'), \
             patch.object(official.core, 'source_adapter', return_value={
                 'version': self.record['version'], 'asarHash': 'x'}), \
             patch.object(official, 'clone', side_effect=shutil.copytree) as clone, \
             patch.object(official, 'build', return_value=self.record['installedFiles']):
            with self.assertRaisesRegex(ValueError, '备份版本'):
                official.update()
            clone.assert_not_called()
        self.assertEqual((self.target / official.ASAR).read_bytes(), b'patched')

    def test_redirected_backup_is_rejected_before_copy(self):
        link = self.state / 'official-backup-link.app'
        link.symlink_to(self.backup, target_is_directory=True)
        self.record['backup'] = str(link)
        with patch.object(official, 'clone', side_effect=shutil.copytree) as clone:
            with self.assertRaisesRegex(ValueError, '备份路径异常'):
                official.disable()
            clone.assert_not_called()

    def test_damaged_backup_does_not_hide_valid_installed_patch(self):
        with patch.object(official.core, 'verify_official_signature',
                          side_effect=ValueError('damaged backup')):
            result = official.status()
        self.assertTrue(result['enabled'])
        self.assertTrue(result['supported'])
        self.assertFalse(result['backupVerified'])
        self.assertIn('备份', result['message'])
        self.assertNotIn('damaged backup', json.dumps(result))

    def test_healthy_backup_is_checked_during_status(self):
        with patch.object(official.core, 'verify_official_signature') as verify:
            result = official.status()
        self.assertTrue(result['backupVerified'])
        verify.assert_called_once_with(self.backup)

    def test_same_version_restore_keeps_backup_and_restores_original(self):
        with patch.object(official, 'clone', side_effect=shutil.copytree), \
             patch.object(official, 'status', return_value={'enabled': False, 'supported': True}):
            result = official.disable()
        self.assertEqual((self.target / official.ASAR).read_bytes(), b'original')
        self.assertEqual((self.backup / official.ASAR).read_bytes(), b'original')
        self.assertFalse(result['enabled'])
        self.assertTrue(result['supported'])
        retired = pathlib.Path(self.record['retired'])
        self.assertEqual((retired / official.ASAR).read_bytes(), b'patched')

    def test_missing_target_with_wrong_version_backup_is_not_recoverable(self):
        self.record['version'] = official.core.VERSION
        with patch.object(official, 'TARGET', self.state / 'missing.app'):
            result = official.status()
        self.assertFalse(result['supported'])
        self.assertFalse(result.get('recoveryRequired', False))

    def test_legacy_record_without_version_uses_original_supported_version(self):
        self.record.pop('version')
        result = official.status()
        self.assertFalse(result['backupVerified'])

    def test_backup_from_another_build_is_refused(self):
        """安装的是 arm64 构建，却拿到了 universal 构建的备份：拒绝。"""
        version = official.core.ADAPTER_42304['version']
        self.record['version'] = version
        self.record['original'] = {
            'version': version,
            'asarHash': official.core.ADAPTER_42304_ARM64['asarHash'],
            'files': {}, 'sourceFiles': {}}
        (self.backup / 'Contents/Info.plist').write_bytes(
            plistlib.dumps({'CFBundleShortVersionString': version}))
        (self.backup / official.ASAR).write_bytes(b'universal build asar')
        with self.assertRaisesRegex(ValueError, '官方构建'):
            official.verify_backup(self.record, version)

    def test_matching_build_backup_is_accepted(self):
        version = official.core.ADAPTER_42304['version']
        self.record['version'] = version
        self.record['original'] = {
            'version': version,
            'asarHash': official.core.sha(b'official original here'),
            'files': {}, 'sourceFiles': {}}
        (self.backup / 'Contents/Info.plist').write_bytes(
            plistlib.dumps({'CFBundleShortVersionString': version}))
        (self.backup / official.ASAR).write_bytes(b'official original here')
        self.assertEqual(official.verify_backup(self.record, version), self.backup)

    def test_backup_is_recoverable_without_the_registry(self):
        """那个版本已经从登记表淘汰，只要记录里的原始指纹还在就能恢复。"""
        self.record['version'] = '9.9.9-retired'
        self.record['original'] = {
            'version': '9.9.9-retired',
            'asarHash': official.core.sha(b'retired official build'),
            'files': {}, 'sourceFiles': {}}
        (self.backup / 'Contents/Info.plist').write_bytes(
            plistlib.dumps({'CFBundleShortVersionString': '9.9.9-retired'}))
        (self.backup / official.ASAR).write_bytes(b'retired official build')
        self.assertEqual(official.verify_backup(self.record), self.backup)


if __name__ == '__main__':
    unittest.main()
