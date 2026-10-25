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

    def test_standalone_module_shares_state_with_backend(self):
        """单模块命令行运行时，`__main__` 和被 import 的模块必须是同一份。

        不是同一个对象时，configure() 改的是 import 拿到的那份副本，
        真正执行 main() 的这份还指向仓库里的 work/：读不到安装记录，
        已打补丁的应用会被误报成「尚未适配」，仓库里还会多出空目录。
        """
        with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
            root = Path(temp).resolve()/'Application Support/Claude Native Unlock'
            for module, name in [(core,'ROOT'), (core,'WORK'), (official,'STATE'), (features,'STATE')]:
                stack.enter_context(patch.object(module, name, getattr(module,name)))
            # 模拟 `python3 src/official_unlock.py`：模块以 __main__ 身份运行，
            # 此时 sys.modules 里还没有 'official_unlock' 这一项。
            fake_main = type(sys)('__main__')
            fake_main.__dict__.update(official.__dict__)
            stack.enter_context(patch.dict(sys.modules, {'__main__': fake_main}))
            stack.enter_context(patch.dict(sys.modules))
            del sys.modules['official_unlock']
            resolved = backend.configure_module('official_unlock', root)
            self.assertIs(resolved, fake_main)
            # 真正会跑 main() 的那一份也拿到了新的状态目录
            self.assertEqual(fake_main.STATE, root/'work/official-install')

    def test_redirected_state_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root/'real').mkdir()
            (root/'redirect').symlink_to(root/'real', target_is_directory=True)
            with self.assertRaisesRegex(ValueError, '符号链接'):
                backend.configure(root/'redirect')
            self.assertEqual(list((root/'real').iterdir()), [])

    def test_unknown_desktop_without_a_readable_asar_is_refused(self):
        """没有 app.asar 就读不到功能表，结构校验不成立，仍然拒绝。

        未登记版本本身不再是拒绝理由（用户 2026-10-25 明确要默认照常
        解锁），但结构读不出来时同样不能放行。
        """
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp)/'Claude.app'
            (app/'Contents').mkdir(parents=True)
            (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleShortVersionString':'99.0'}))
            with self.assertRaises(ValueError):
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


class MasterSwitch(unittest.TestCase):
    """界面只有一个总开关：后端要按顺序做完每一项，并如实报告跳过和失败。"""

    def setUp(self):
        self.calls = []

    def record(self, name, result=None, error=None):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if error is not None:
                raise ValueError(error)
            return result if result is not None else {}
        return call

    def test_enable_runs_official_first_then_every_feature_then_cli(self):
        report = {'features': [{'id': identifier, 'available': True}
                               for identifier in features.FEATURES]}
        with patch.object(official, 'enable', self.record('official')), \
             patch.object(official, 'status', return_value={'supported': True}), \
             patch.object(features, 'report', return_value=report), \
             patch.object(features, 'change', self.record('change')), \
             patch.object(cli, 'enable', self.record('cli')):
            result = backend.switch_all(True)
        self.assertEqual([name for name, _, _ in self.calls],
                         ['official'] + ['change'] * len(features.FEATURES) + ['cli'])
        self.assertEqual([args[0] for name, args, _ in self.calls if name == 'change'],
                         list(features.FEATURES))
        self.assertTrue(result['on'])
        self.assertNotIn('error', result)
        self.assertTrue(all(step['state'] == 'done' for step in result['steps']))

    def test_enable_stops_before_config_when_the_patch_fails(self):
        with patch.object(official, 'enable', self.record('official', error='指纹不符')), \
             patch.object(features, 'change', self.record('change')), \
             patch.object(cli, 'enable', self.record('cli')):
            result = backend.switch_all(True)
        self.assertNotIn('change', [name for name, _, _ in self.calls])
        self.assertFalse(result['on'])
        self.assertIn('指纹不符', result['error'])
        self.assertEqual(result['steps'][0]['state'], 'failed')

    def test_missing_setting_is_skipped_and_named(self):
        report = {'features': [{'id': identifier, 'available': identifier != 'browser'}
                               for identifier in features.FEATURES]}
        with patch.object(official, 'enable', self.record('official')), \
             patch.object(official, 'status', return_value={'supported': True}), \
             patch.object(features, 'report', return_value=report), \
             patch.object(features, 'change', self.record('change')), \
             patch.object(cli, 'enable', self.record('cli')):
            result = backend.switch_all(True)
        skipped = [step for step in result['steps'] if step['state'] == 'skipped']
        self.assertEqual([step['id'] for step in skipped], ['browser'])
        self.assertNotIn('browser', [args[0] for name, args, _ in self.calls if name == 'change'])
        self.assertNotIn('error', result)

    def test_restore_only_touches_items_with_a_recovery_record(self):
        report = {'features': [{'id': identifier, 'canRestore': identifier == 'browser'}
                               for identifier in features.FEATURES]}
        with patch.object(features, 'report', return_value=report), \
             patch.object(features, 'change', self.record('change')), \
             patch.object(cli, 'restore', self.record('cli')), \
             patch.object(official, 'disable', self.record('official')):
            result = backend.switch_all(False)
        self.assertEqual([(name, args[0] if args else None) for name, args, _ in self.calls],
                         [('change', 'browser'), ('cli', 'official'), ('official', None)])
        self.assertFalse(result['on'])
        self.assertNotIn('error', result)

    def test_restore_removes_configuration_before_the_patch(self):
        """配置写入要求正式应用仍然校验通过，所以卸补丁必须排在最后。"""
        report = {'features': [{'id': identifier, 'canRestore': True}
                               for identifier in features.FEATURES]}
        with patch.object(features, 'report', return_value=report), \
             patch.object(features, 'change', self.record('change')), \
             patch.object(cli, 'restore', self.record('cli')), \
             patch.object(official, 'disable', self.record('official')):
            backend.switch_all(False)
        self.assertEqual(self.calls[-1][0], 'official')

    def test_a_failing_item_does_not_hide_the_others(self):
        with patch.object(features, 'report', return_value={'features': []}), \
             patch.object(features, 'change', self.record('change')), \
             patch.object(cli, 'restore', self.record('cli', error='CLI 未适配')), \
             patch.object(official, 'disable', self.record('official')):
            result = backend.switch_all(False)
        states = {step['id']: step['state'] for step in result['steps']}
        self.assertEqual(states['code-auto-dream'], 'failed')
        self.assertEqual(states['official'], 'done')
        self.assertIn('Code 自动整理', result['error'])


class StorageRootMigration(unittest.TestCase):
    """0.3.16 改产品名后，旧状态目录要整体迁移过来，且绝不丢备份。"""

    def make_home(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        home = Path(temp.name).resolve()
        support = home / 'Library/Application Support'
        legacy = support / backend.LEGACY_ROOT_NAME
        (legacy / 'work/official-install').mkdir(parents=True)
        backup = legacy / 'work/official-install/official-backup-1.app'
        backup.mkdir()
        manifest = legacy / 'work/official-install/manifest.json'
        manifest.write_text(json.dumps({'backup': str(backup), 'phase': 'enabled'}))
        return home, support, legacy

    def test_legacy_directory_is_renamed_and_absolute_paths_follow(self):
        home, support, legacy = self.make_home()
        with patch.object(backend.Path, 'home', return_value=home):
            root = backend.storage_root()
        self.assertEqual(root, support / backend.ROOT_NAME)
        self.assertTrue(root.is_dir())
        self.assertFalse(legacy.exists())
        self.assertTrue((root / 'work/official-install/official-backup-1.app').is_dir())
        moved = json.loads((root / 'work/official-install/manifest.json').read_text())
        self.assertEqual(moved['backup'],
                         str(root / 'work/official-install/official-backup-1.app'))

    def test_migration_is_not_repeated_once_the_new_directory_exists(self):
        home, support, legacy = self.make_home()
        with patch.object(backend.Path, 'home', return_value=home):
            first = backend.storage_root()
            (legacy / 'stray').mkdir(parents=True)
            second = backend.storage_root()
        self.assertEqual(first, second)
        self.assertTrue((legacy / 'stray').is_dir())

    def test_failed_migration_falls_back_to_the_legacy_directory(self):
        home, support, legacy = self.make_home()
        with patch.object(backend.Path, 'home', return_value=home), \
             patch.object(backend, 'rewrite_legacy_paths',
                          side_effect=OSError('simulated failure')):
            root = backend.storage_root()
        self.assertEqual(root, legacy)
        self.assertTrue((legacy / 'work/official-install/official-backup-1.app').is_dir())

    def test_redirected_legacy_directory_is_left_alone(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        home = Path(temp.name).resolve()
        support = home / 'Library/Application Support'
        target = support / 'elsewhere'
        target.mkdir(parents=True)
        (support / backend.LEGACY_ROOT_NAME).symlink_to(target, target_is_directory=True)
        with patch.object(backend.Path, 'home', return_value=home):
            root = backend.storage_root()
        self.assertEqual(root, support / backend.ROOT_NAME)
        self.assertFalse(root.exists())
        self.assertTrue((support / backend.LEGACY_ROOT_NAME).is_symlink())

    def test_empty_shell_created_by_the_interface_does_not_block_migration(self):
        """界面启动时会先建好目录，真正的状态还在旧目录里，必须照样搬过来。"""
        home, support, legacy = self.make_home()
        shell = support / backend.ROOT_NAME
        (shell / 'work').mkdir(parents=True)
        (shell / 'last-operation.json').write_text('{}')
        with patch.object(backend.Path, 'home', return_value=home):
            root = backend.storage_root()
        self.assertEqual(root, shell)
        self.assertTrue((root / 'work/official-install/official-backup-1.app').is_dir())
        self.assertFalse(legacy.exists())

    def test_new_directory_with_real_state_is_never_overwritten(self):
        home, support, legacy = self.make_home()
        current = support / backend.ROOT_NAME
        (current / 'work/official-install').mkdir(parents=True)
        (current / 'work/official-install/manifest.json').write_text('{"phase": "enabled"}')
        with patch.object(backend.Path, 'home', return_value=home):
            root = backend.storage_root()
        self.assertEqual(root, current)
        self.assertTrue((current / 'work/official-install/manifest.json').exists())
        self.assertTrue((legacy / 'work/official-install/official-backup-1.app').is_dir())

    def test_unknown_content_in_the_new_directory_is_left_alone(self):
        home, support, legacy = self.make_home()
        current = support / backend.ROOT_NAME
        current.mkdir(parents=True)
        (current / 'something-else').write_text('keep me')
        with patch.object(backend.Path, 'home', return_value=home):
            root = backend.storage_root()
        self.assertEqual(root, current)
        self.assertEqual((current / 'something-else').read_text(), 'keep me')
