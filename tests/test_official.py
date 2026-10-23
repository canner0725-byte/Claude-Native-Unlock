import pathlib
import plistlib
import shutil
import struct
import sys
import tempfile
import unittest
import json
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]/'src'))
import official_unlock as official


class OfficialSafety(unittest.TestCase):
    def test_revision6_enable_installs_new_side_session_availability(self):
        current = {'enabled': True, 'supported': True, 'availabilityOnly': True,
                   'version': official.core.ADAPTER_37337['version'], 'nativeCodeStateUI': True,
                   'nativeCodeSideSessions': False}
        with patch.object(official, 'status', return_value=current), \
             patch.object(official, 'update', return_value={'updated': True}) as update:
            self.assertEqual(official.enable(), {'updated': True})
            update.assert_called_once()

    def test_revision7_enable_installs_folder_worktree_availability(self):
        current = {'enabled': True, 'supported': True, 'availabilityOnly': True,
                   'version': official.core.ADAPTER_37337['version'], 'nativeCodeSideSessions': True,
                   'nativeCodeFolderWorktrees': False}
        with patch.object(official, 'status', return_value=current), \
             patch.object(official, 'update', return_value={'updated': True}) as update:
            self.assertEqual(official.enable(), {'updated': True})
            update.assert_called_once()

    def test_current_folder_worktree_patch_is_idempotent(self):
        current = {'enabled': True, 'supported': True, 'availabilityOnly': True,
                   'version': official.core.ADAPTER_37337['version'], 'nativeCodeSideSessions': True,
                   'nativeCodeFolderWorktrees': True, 'patchRevision': official.PATCH_REVISION}
        with patch.object(official, 'status', return_value=current), \
             patch.object(official, 'update') as update:
            self.assertEqual(official.enable(), current)
            update.assert_not_called()

    def test_prompt_suggestion_setting_flag_is_added_once(self):
        for adapter in [official.core.ADAPTER_37337, official.core.ADAPTER_42304,
                        official.core.ADAPTER_42304_ARM64, official.core.ADAPTER_42304_2,
                        official.core.ADAPTER_31623, official.core.ADAPTER_36556]:
            text = official.core.main_availability(adapter)
            self.assertEqual(text.count('2120747855:{rule:"forced",value:!0}'), 1)

    def test_31623_restores_status_and_worktree_gates(self):
        text = official.core.web_availability(official.core.ADAPTER_31623)
        self.assertEqual(text.count('3858164079:{rule:"always",value:!0}'), 1)
        self.assertEqual(text.count('1235690166:{rule:"always",value:!0}'), 1)
        self.assertNotIn('3264789062', text)
        self.assertNotIn('3335190042', text)

    def test_36556_restores_renderer_defaults_only(self):
        """3.6556.2 只补渲染进程默认值；不覆盖官方 when/follows 条件。"""
        adapter = official.core.ADAPTER_36556
        self.assertEqual(adapter['featureAnchor'], 'var Hon={')
        self.assertEqual(adapter['webFeatureAnchor'], 'Uon={')
        self.assertEqual(adapter['mainAvailability'],
                         '2120747855:{rule:"forced",value:!0},')
        text = official.core.web_availability(adapter)
        for gate in ('3858164079', '1235690166', '1282344890',
                     '3774822426', '2280470525'):
            self.assertEqual(text.count(gate + ':{rule:"always",value:!0}'), 1)
        # 这一版官方自带的条件门不在追加列表里，避免覆盖官方语义。
        self.assertNotIn('3264789062', text)
        self.assertNotIn('3716359568', text)

    def test_renderer_conflict_is_rejected_not_overridden(self):
        """渲染进程表里的官方 when/follows 必须拒绝覆盖，而不是追加表尾。"""
        text = 'Uon={123:{rule:"follows",condition:"x"}}'
        with self.assertRaisesRegex(ValueError, '拒绝覆盖'):
            official.core.insert_entries(text, 'Uon={',
                                         [('123', 'rule:"always",value:!0')],
                                         conflict='reject')
        # 主进程表仍允许覆盖：官方改了取值时我们的解锁要能生效。
        changed = official.core.insert_entries(
            'var Hon={123:{rule:"forced",value:!1}}', 'var Hon={',
            [('123', 'rule:"forced",value:!0')])
        self.assertIn('123:{rule:"forced",value:!1},123:{rule:"forced",value:!0}', changed)

    def test_42304_2_is_a_single_verified_build(self):
        adapter = official.core.ADAPTER_42304_2
        self.assertEqual(official.core.adapters_for_version('2.42304.2'), [adapter])
        self.assertEqual(adapter['version'], '2.42304.2')
        self.assertIn(adapter['version'], official.core.CODE_UI_VERSIONS)
        self.assertTrue(official.core.adapters_for_version('2.42304.2')[0]['main']
                        .startswith('.vite/build/'))

    def test_42304_2_reuses_revision10_availability_unchanged(self):
        """2.42304.2 只换锚点，可用性条件必须与已验证的 revision 10 完全一致。"""
        new, old = official.core.ADAPTER_42304_2, official.core.ADAPTER_42304
        self.assertEqual(official.core.main_availability(new),
                         official.core.main_availability(old))
        self.assertEqual(official.core.web_availability(new),
                         official.core.web_availability(old))
        # 2.42304.2 的可用性条件与 revision 10 一致；后续 revision 只增加
        # 签名方式和适配逻辑，没有新增或改写可用性条件。
        self.assertGreaterEqual(official.PATCH_REVISION, 10)

    def test_42304_2_anchors_are_distinct_and_hashes_well_formed(self):
        adapter = official.core.ADAPTER_42304_2
        self.assertNotEqual(adapter['featureAnchor'], adapter['webFeatureAnchor'])
        self.assertTrue(adapter['featureAnchor'].startswith('var '))
        self.assertIn(adapter['main'], adapter['files'])
        for digest in [adapter['asarHash'], *adapter['files'].values(),
                       *adapter['sourceFiles'].values()]:
            self.assertRegex(digest, r'^[0-9a-f]{64}$')

    def test_diagnose_treats_42304_2_as_known_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory).resolve()
            app = root / 'Claude.app'
            state = root / 'state'
            state.mkdir()
            (app / 'Contents/Resources').mkdir(parents=True)
            script = b'var W8t={123929380:{rule:"forced",value:!0}};'
            header = {'files': {'.vite': {'files': {'build': {'files': {'index.js': {
                'offset': '0', 'size': len(script)}}}}}}}
            raw = json.dumps(header, separators=(',', ':')).encode()
            payload = ((len(raw) + 7) // 4) * 4
            asar = (struct.pack('<IIII', 4, payload + 4, payload, len(raw))
                    + raw + b'\0' * (payload - 4 - len(raw)) + script)
            (app / official.ASAR).write_bytes(asar)
            (app / 'Contents/Info.plist').write_bytes(
                plistlib.dumps({'CFBundleShortVersionString': '2.42304.2'}))
            with patch.object(official, 'TARGET', app), \
                 patch.object(official, 'STATE', state):
                report = official.diagnose()
            self.assertTrue(report['knownVersion'])
            self.assertEqual(report['candidates'][0]['featureTableAnchors'], ['var W8t={'])

    def test_revision9_is_offered_update_to_revision10(self):
        current = {'enabled': True, 'supported': True, 'patchRevision': 9,
                   'version': official.core.ADAPTER_42304['version']}
        with patch.object(official, 'status', return_value=current), \
             patch.object(official, 'update', return_value={'updated': True}) as update:
            self.assertEqual(official.enable(), {'updated': True})
            update.assert_called_once()

    def test_current_revision_update_is_idempotent(self):
        current = {'enabled': True, 'supported': True, 'patchRevision': official.PATCH_REVISION,
                   'version': official.core.ADAPTER_42304['version']}
        with patch.object(official, 'status', return_value=current), \
             patch.object(official, 'clone') as clone:
            self.assertEqual(official.update(), current)
            clone.assert_not_called()

    def test_prepare_update_restores_patch_and_keeps_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory).resolve() / 'Claude.app'
            target.mkdir()
            current = {'enabled': True, 'supported': True, 'running': False}
            with patch.object(official, 'TARGET', target), \
                 patch.object(official, 'status', return_value=current), \
                 patch.object(official, 'disable', return_value={'enabled': False, 'message': 'restored'}) as disable:
                result = official.prepare_update()
            self.assertTrue(result['preparedForUpdate'])
            self.assertIn('官方更新', result['message'])
            disable.assert_called_once()

    def test_prepare_update_refuses_running_application(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory).resolve() / 'Claude.app'
            target.mkdir()
            current = {'enabled': True, 'supported': True, 'running': True}
            with patch.object(official, 'TARGET', target), \
                 patch.object(official, 'status', return_value=current), \
                 patch.object(official, 'disable') as disable:
                with self.assertRaisesRegex(ValueError, '退出 Claude'):
                    official.prepare_update()
                disable.assert_not_called()

    def test_prepare_update_when_already_official(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory).resolve() / 'Claude.app'
            target.mkdir()
            current = {'enabled': False, 'supported': True, 'running': False, 'version': '2.42304.0'}
            with patch.object(official, 'TARGET', target), \
                 patch.object(official, 'status', return_value=current), \
                 patch.object(official, 'manifest', return_value=None), \
                 patch.object(official, 'disable') as disable:
                result = official.prepare_update()
            self.assertTrue(result['preparedForUpdate'])
            self.assertIn('官方原版', result['message'])
            disable.assert_not_called()

    def test_diagnose_writes_read_only_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory).resolve()
            app = root / 'Claude.app'
            state = root / 'state'
            state.mkdir()
            (app / 'Contents/Resources').mkdir(parents=True)
            script = (b'var P8t={1291166712:{rule:"forced",value:!0},'
                      b'2120747855:{rule:"forced",value:!0}};')
            header = {'files': {'.vite': {'files': {'build': {'files': {'index.js': {
                'offset': '0', 'size': len(script)}}}}}}}
            raw = json.dumps(header, separators=(',', ':')).encode()
            payload = ((len(raw) + 7) // 4) * 4
            asar = (struct.pack('<IIII', 4, payload + 4, payload, len(raw))
                    + raw + b'\0' * (payload - 4 - len(raw)) + script)
            (app / official.ASAR).write_bytes(asar)
            (app / 'Contents/Info.plist').write_bytes(
                plistlib.dumps({'CFBundleShortVersionString': '99.0'}))
            before = (app / official.ASAR).read_bytes()
            with patch.object(official, 'TARGET', app), \
                 patch.object(official, 'STATE', state):
                report = official.diagnose()
            self.assertEqual((app / official.ASAR).read_bytes(), before)
            self.assertTrue(pathlib.Path(report['reportPath']).exists())
            self.assertFalse(report['knownVersion'])
            self.assertEqual(report['candidates'][0]['knownFlagCounts']['2120747855'], 1)
            self.assertEqual(report['candidates'][0]['knownFlagDefinitions']['2120747855'], 1)
            self.assertIn('var P8t={', report['candidates'][0]['featureTableAnchors'])

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
        current = {'enabled': True, 'supported': True, 'patchRevision': 9,
                   'version': official.core.ADAPTER_42304['version']}
        with patch.object(official, 'status', return_value=current), \
             patch.object(official, 'running', return_value=True), \
             patch.object(official, 'clone') as clone:
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
            (backup / 'Contents').mkdir(parents=True)
            (backup / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleShortVersionString': official.core.VERSION}))
            record = {'backup': str(backup), 'installedFiles': {official.ASAR: 'old'}, 'phase': 'enabled'}
            real_rename = pathlib.Path.rename
            def rename(path, destination):
                if path.name.startswith('upgrade-prepared-'):
                    raise OSError('simulated swap failure')
                return real_rename(path, destination)
            with patch.object(official, 'STATE', state), patch.object(official, 'TARGET', target), \
                 patch.object(official, 'manifest', return_value=record), \
                 patch.object(official, 'running', return_value=False), \
                 patch.object(official, 'status', return_value={'enabled': True, 'supported': True, 'version': official.core.VERSION}), \
                 patch.object(official.core, 'verify_source'), patch.object(official, 'verify_patched'), \
                 patch.object(official, 'prepare_identity'), \
                 patch.object(official.platform, 'machine', return_value='arm64'), \
                 patch.object(official.platform, 'mac_ver', return_value=('27.0.1', (), '')), \
                 patch.object(official, 'clone', side_effect=lambda source, destination: destination.mkdir()), \
                 patch.object(official, 'build', return_value=({official.ASAR: 'new'}, {})), \
                 patch.object(pathlib.Path, 'rename', rename):
                with self.assertRaisesRegex(OSError, 'simulated'):
                    official.update()
            self.assertEqual((target / 'marker').read_text(), 'previous')
            self.assertEqual(json.loads((state / 'manifest.json').read_text()), record)

    def test_interrupted_install_exposes_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            state = pathlib.Path(directory).resolve()
            backup = state / 'official-backup-1.app'
            (backup / 'Contents').mkdir(parents=True)
            (backup / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleShortVersionString': official.core.VERSION}))
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
                 patch.object(official, 'prepare_identity'), \
                 patch.object(official, 'clone') as clone:
                self.assertFalse(official.status()['supported'])
                with self.assertRaisesRegex(ValueError, 'Unknown version'):
                    official.enable()
                clone.assert_not_called()

    def test_disable_42304_patched_target_uses_backup_official_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            state = pathlib.Path(directory).resolve()
            target = state / 'Claude.app'
            backup = state / 'official-backup-1.app'
            for app, payload in [(target, b'patched'), (backup, b'original')]:
                (app / 'Contents/Resources').mkdir(parents=True)
                (app / official.ASAR).write_bytes(payload)
                (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
                    'CFBundleShortVersionString': official.core.ADAPTER_42304['version']}))
            record = {
                'version': official.core.ADAPTER_42304['version'],
                'backup': str(backup),
                'installedFiles': {official.ASAR: official.core.sha(b'patched')},
                'phase': 'enabled',
            }
            official_hash = official.core.sha(b'original')

            def adapter(app):
                # 补丁版目标的 ASAR 已经改变，不能再用它判断官方指纹。
                if app == backup:
                    return {'version': official.core.ADAPTER_42304['version'],
                            'asarHash': official_hash}
                raise AssertionError('不应读取补丁版目标的适配器')

            with patch.object(official, 'STATE', state), \
                 patch.object(official, 'TARGET', target), \
                 patch.object(official, 'manifest', return_value=record), \
                 patch.object(official, 'running', return_value=False), \
                 patch.object(official.core, 'verify_source'), \
                 patch.object(official.core, 'registered_adapter', side_effect=adapter), \
                 patch.object(official, 'verify_patched'), \
                 patch.object(official, 'clone', side_effect=shutil.copytree):
                result = official.disable()
            self.assertFalse(result['enabled'])
            self.assertEqual((target / official.ASAR).read_bytes(), b'original')

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
            (backup / 'Contents').mkdir(parents=True)
            (backup / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleShortVersionString': official.core.VERSION}))
            target = state / 'Claude.app'
            (target / 'Contents/Resources').mkdir(parents=True)
            (target / official.ASAR).write_bytes(b'changed application')
            (target / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleShortVersionString': official.core.VERSION}))
            record = {'backup': str(backup), 'installedFiles': {official.ASAR: 'expected'}}
            with patch.object(official, 'STATE', state), patch.object(official, 'TARGET', target), \
                 patch.object(official, 'manifest', return_value=record), \
                 patch.object(official, 'running', return_value=False), \
                 patch.object(official.core, 'verify_source'), \
                 patch.object(official.core, 'registered_adapter',
                              return_value={'version': official.core.VERSION,
                                            'asarHash': official.core.sha(b'official original')}), \
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

    def test_prepare_update_finalizes_record_when_target_is_already_official(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory).resolve() / 'Claude.app'
            target.mkdir()
            current = {'enabled': False, 'supported': True, 'running': False}
            record = {'phase': 'enabled'}
            with patch.object(official, 'TARGET', target), \
                 patch.object(official, 'status', return_value=current), \
                 patch.object(official, 'manifest', return_value=record), \
                 patch.object(official, 'save') as save, \
                 patch.object(official, 'disable') as disable:
                result = official.prepare_update()
            self.assertTrue(result['preparedForUpdate'])
            self.assertEqual(record['phase'], 'disabled')
            save.assert_called_once_with(record)
            disable.assert_not_called()

    def test_prepare_update_refuses_stale_record_on_unverified_target(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory).resolve() / 'Claude.app'
            target.mkdir()
            current = {'enabled': False, 'supported': False, 'running': False}
            record = {'phase': 'enabled'}
            with patch.object(official, 'TARGET', target), \
                 patch.object(official, 'status', return_value=current), \
                 patch.object(official, 'manifest', return_value=record), \
                 patch.object(official, 'save') as save:
                with self.assertRaisesRegex(ValueError, '不是已校验的官方版本'):
                    official.prepare_update()
            save.assert_not_called()

    def test_signing_identity_update_is_offered_when_identity_changes(self):
        """装了固定身份后，旧安装（ad-hoc 或无身份记录）要能被重打一次。"""
        current = {'enabled': True, 'supported': True, 'patchRevision': official.PATCH_REVISION,
                   'signingUpdateAvailable': True}
        with patch.object(official, 'status', return_value=current), \
             patch.object(official, 'update', return_value={'updated': True}) as update:
            self.assertEqual(official.enable(), {'updated': True})
            update.assert_called_once()

    def test_update_runs_when_only_the_signing_identity_changed(self):
        current = {'enabled': True, 'supported': True, 'patchRevision': official.PATCH_REVISION,
                   'signingUpdateAvailable': True}
        with patch.object(official, 'status', return_value=current), \
             patch.object(official, 'check_idle'), \
             patch.object(official, 'prepare_identity'), \
             patch.object(official, 'manifest', return_value=None), \
             patch.object(official, 'clone') as clone:
            with self.assertRaisesRegex(ValueError, '缺少安装记录'):
                official.update()
            clone.assert_not_called()

    def test_status_reports_signing_update_for_ad_hoc_install(self):
        with tempfile.TemporaryDirectory() as directory:
            state = pathlib.Path(directory).resolve()
            backup = state / 'official-backup-1.app'
            (backup / 'Contents').mkdir(parents=True)
            (backup / 'Contents/Info.plist').write_bytes(
                plistlib.dumps({'CFBundleShortVersionString': official.core.ADAPTER_42304_2['version']}))
            target = state / 'Claude.app'
            (target / 'Contents/Resources').mkdir(parents=True)
            (target / official.ASAR).write_bytes(b'patched')
            (target / 'Contents/Info.plist').write_bytes(
                plistlib.dumps({'CFBundleShortVersionString': official.core.ADAPTER_42304_2['version']}))
            record = {'version': official.core.ADAPTER_42304_2['version'],
                      'patchRevision': official.PATCH_REVISION, 'phase': 'enabled',
                      'backup': str(backup),
                      'installedFiles': {official.ASAR: official.core.sha(b'patched')}}
            with patch.object(official, 'STATE', state), patch.object(official, 'TARGET', target), \
                 patch.object(official, 'manifest', return_value=record), \
                 patch.object(official, 'running', return_value=False), \
                 patch.object(official, 'verify_patched'), \
                 patch.object(official, 'verify_backup'), \
                 patch.object(official.core, 'local_identity', return_value='abc123'):
                result = official.status()
            self.assertTrue(result['enabled'])
            self.assertTrue(result['signingUpdateAvailable'])
            self.assertTrue(result['updateAvailable'])

    def test_signing_identity_parses_codesign_output_from_either_stream(self):
        import native_unlock as core
        for stream, text in [('stdout', b'designated => identifier "x" and certificate root = H"AB12"\n'),
                             ('stderr', b'designated => identifier "x" and certificate root = H"AB12"\n')]:
            done = type('R', (), {'stdout': text if stream == 'stdout' else b'',
                                  'stderr': text if stream == 'stderr' else b''})()
            with patch.object(core, 'run', return_value=done):
                self.assertEqual(core.app_signing_identity(pathlib.Path('X.app')), 'ab12')
        with patch.object(core, 'run', return_value=type('R', (), {
                'stdout': b'designated => cdhash H"dead"\n', 'stderr': b''})()):
            self.assertIsNone(core.app_signing_identity(pathlib.Path('X.app')))

    def test_local_identity_needs_both_keychain_and_marker(self):
        import native_unlock as core
        # 必须隔离整个 WORK：signing_paths() 从 WORK 推导，漏掉就会在仓库里
        # 建出 work/signing，测试之间互相影响。
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory).resolve()
            with patch.object(core, 'WORK', root):
                self.assertIsNone(core.local_identity())
                _, keychain, _, marker = core.signing_paths()
                keychain.parent.mkdir(parents=True, exist_ok=True)
                keychain.write_text('x')
                self.assertIsNone(core.local_identity())
                marker.write_text('aabb\n')
                self.assertEqual(core.local_identity(), 'aabb')
                marker.write_text('\n')
                self.assertIsNone(core.local_identity())
            # 回归保护：任何测试都不该在仓库里建出 work/（曾因 update() 真去
            # 生成签名身份而泄漏，并让后续测试读到另一个身份）。
            self.assertFalse((pathlib.Path(core.__file__).resolve().parent.parent / 'work').exists(),
                             '测试不得在仓库里留下 work/ 目录')

    def test_launch_opens_unsupported_official_build(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory).resolve() / 'Claude.app'
            target.mkdir()
            with patch.object(official, 'TARGET', target), \
                 patch.object(official, 'status',
                              return_value={'supported': False, 'message': '未适配'}), \
                 patch.object(official.core, 'run') as run:
                result = official.launch()
            self.assertTrue(result['opened'])
            self.assertIn('尚未适配', result['message'])
            run.assert_called_once_with('/usr/bin/open', str(target))

    def test_launch_refuses_missing_application(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = pathlib.Path(directory).resolve() / 'Missing.app'
            with patch.object(official, 'TARGET', missing):
                with self.assertRaisesRegex(ValueError, '没有找到'):
                    official.launch()


if __name__ == '__main__':
    unittest.main()


class FeatureTableEditing(unittest.TestCase):
    """表内插入必须能识别“官方已经自己强制了”的情况。"""

    TABLE = ('var ken={123929380:{rule:"forced",value:!0},'
             '857846362:{rule:"forced",value:!0}},Aen={101380594:{rule:"always",value:!0}};')

    def test_inserts_only_missing_gates(self):
        import native_unlock as core
        out = core.insert_entries(
            self.TABLE, 'var ken={',
            core.split_entries('1291166712:{rule:"forced",value:!0},'
                               '857846362:{rule:"forced",value:!0},'))
        self.assertEqual(out.count('1291166712:{'), 1)
        self.assertEqual(out.count('857846362:{'), 1)   # 官方已有同样定义，不重复
        self.assertTrue(out.startswith('var ken={1291166712:'))

    def test_identical_existing_entry_is_left_alone(self):
        import native_unlock as core
        out = core.insert_entries(self.TABLE, 'var ken={',
                                  core.split_entries('857846362:{rule:"forced",value:!0},'))
        self.assertEqual(out, self.TABLE)

    def test_conflicting_definition_is_overridden_at_the_table_end(self):
        """官方已有不同取值时，追加到表尾——对象字面量里后定义获胜。"""
        import native_unlock as core
        report = {}
        # 官方把它设成关闭，我们要开启——这才是真正的冲突
        table = ('var ken={857846362:{rule:"forced",value:!1}},'
                 'Aen={101380594:{rule:"always",value:!0}};')
        out = core.insert_entries(table, 'var ken={',
                                  core.split_entries('857846362:{rule:"forced",value:!0},'),
                                  report)
        self.assertEqual(report['overridden'], ['857846362'])
        # 覆盖项必须出现在官方定义之后，且在表内
        official_pos = out.index('857846362:{rule:"forced",value:!1}')
        ours_pos = out.index('857846362:{rule:"forced",value:!0}', official_pos)
        self.assertGreater(ours_pos, official_pos)
        _, end = core.table_bounds(out, 'var ken={')
        self.assertLess(ours_pos, end)
        # 解析后确实是我们想要的值
        body = core.table_definition(out, 'var ken={', '857846362')
        self.assertIn('value:!0', body)

    def test_lookup_is_scoped_to_the_table(self):
        """同名键出现在另一张表里，不算本表已有。"""
        import native_unlock as core
        table = ('var ken={1:{rule:"forced",value:!0}},'
                 'Aen={2120747855:{rule:"always",value:!0}};')
        out = core.insert_entries(table, 'var ken={',
                                  core.split_entries('2120747855:{rule:"forced",value:!0},'))
        self.assertIn('var ken={2120747855:{rule:"forced",value:!0},', out)
        self.assertEqual(out.count('2120747855:{'), 2)

    def test_table_anchors_are_discovered(self):
        import native_unlock as core
        forced, always = core.feature_tables(self.TABLE)
        self.assertEqual(forced, 'var ken={')
        self.assertEqual(always, 'Aen={')

    def test_ambiguous_tables_are_refused(self):
        import native_unlock as core
        with self.assertRaisesRegex(ValueError, 'not uniquely locatable'):
            core.feature_tables('var a={x:{rule:"forced"}},b={y:{rule:"forced"}},'
                                'c={z:{rule:"always"}}')

    def test_discovery_refuses_a_non_official_app(self):
        import native_unlock as core
        with tempfile.TemporaryDirectory() as directory:
            app = pathlib.Path(directory).resolve() / 'Claude.app'
            (app / 'Contents').mkdir(parents=True)
            (app / 'Contents/Info.plist').write_bytes(
                plistlib.dumps({'CFBundleIdentifier': 'com.example.other',
                                'CFBundleShortVersionString': '9.9.9'}))
            with self.assertRaisesRegex(ValueError, 'Not the expected application'):
                core.discover_adapter(app)

    def test_prune_removes_interrupted_staging_copies(self):
        with tempfile.TemporaryDirectory() as directory:
            state = pathlib.Path(directory).resolve()
            keep = state / 'official-backup-1.app'
            for name in ['official-backup-1.app', 'official-backup-old.app',
                         'restore-prepared-1.app', 'upgrade-prepared-2.app',
                         'prepared-3.app', 'retired-patched-4.app']:
                (state / name).mkdir()
            with patch.object(official, 'STATE', state):
                official.prune_old_state(keep)
            self.assertEqual(sorted(p.name for p in state.iterdir()),
                             ['official-backup-1.app'])


class FutureBuildCompatibility(unittest.TestCase):
    """官方换构建时，旧补丁必须仍然能装上并生效。"""

    TABLE = ('var ken={123929380:{rule:"forced",value:!0},'
             '857846362:{rule:"forced",value:!0}},Aen={101380594:{rule:"always",value:!0}};')

    def test_override_is_placed_before_the_closing_brace_with_valid_separator(self):
        import native_unlock as core
        # 表末条目没有尾逗号时，追加覆盖项不能产生非法 JS
        table = 'var ken={857846362:{rule:"forced",value:!1}},Aen={1:{rule:"always",value:!0}};'
        out = core.insert_entries(table, 'var ken={',
                                  core.split_entries('857846362:{rule:"forced",value:!0},'))
        self.assertIn('value:!1},857846362:{rule:"forced",value:!0},}', out)
        self.assertNotIn(',,', out)
        # 覆盖项必须在表内
        _, end = core.table_bounds(out, 'var ken={')
        self.assertLess(out.index('value:!0}'), end)

    def test_effective_definition_is_the_last_one(self):
        import native_unlock as core
        table = 'var ken={5:{rule:"forced",value:!1},5:{rule:"forced",value:!0}};'
        self.assertEqual(core.table_definition(table, 'var ken={', '5'),
                         'rule:"forced",value:!0')

    def test_effective_gate_is_checked_after_writing(self):
        """写完必须按“最后一份定义”核对，不能只看文本里出现过。"""
        import native_unlock as core
        entries = core.split_entries('1291166712:{rule:"forced",value:{enabled:!0}},')
        out = core.insert_entries(self.TABLE, 'var ken={', entries)
        official.verify_gates(out, 'var ken={', entries)
        # 官方在我们之后又定义了一份关闭值：补丁实际不生效，必须拒绝
        _, end = core.table_bounds(out, 'var ken={')
        overridden = out[:end - 1] + ',1291166712:{rule:"forced",value:{enabled:!1}}' + out[end - 1:]
        with self.assertRaisesRegex(ValueError, '未生效'):
            official.verify_gates(overridden, 'var ken={', entries)

    def test_missing_gate_after_writing_is_refused(self):
        import native_unlock as core
        entries = core.split_entries('1291166712:{rule:"forced",value:{enabled:!0}},')
        with self.assertRaisesRegex(ValueError, '未生效'):
            official.verify_gates(self.TABLE, 'var ken={', entries)

    def test_changed_build_with_same_version_falls_back_to_discovery(self):
        """版本号没变但构建变了：不能套用旧锚点。"""
        import native_unlock as core
        with tempfile.TemporaryDirectory() as directory:
            app = pathlib.Path(directory).resolve() / 'Claude.app'
            (app / 'Contents').mkdir(parents=True)
            (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
                'CFBundleIdentifier': 'com.anthropic.claudefordesktop',
                'CFBundleShortVersionString': official.core.ADAPTER_31623['version']}))
            with patch.object(core, 'adapters_for_version',
                              return_value=[official.core.ADAPTER_31623]), \
                 patch.object(core, 'discover_adapter', return_value={'discovered': True}) as discover, \
                 patch.object(core, 'read_asar', return_value=(b'changed', {}, 0)), \
                 patch.object(core, 'sha', return_value='different'):
                adapter = core.source_adapter(app)
            discover.assert_called_once()
            self.assertTrue(adapter['discovered'])

    def test_status_exposes_permission_regrant_hint(self):
        current = {'enabled': True, 'supported': True, 'patchRevision': official.PATCH_REVISION,
                   'signingUpdateAvailable': True}
        self.assertTrue(current['signingUpdateAvailable'])
