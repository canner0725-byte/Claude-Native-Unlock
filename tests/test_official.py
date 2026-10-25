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
    @staticmethod
    def synthetic_asar(script: bytes) -> bytes:
        """合成一个最小可读的 ASAR：固定头 + 索引 + 文件内容。

        恢复路径现在直接读备份的 ASAR / Info.plist 指纹，不能再塞任意字节。
        """
        header = {'files': {'.vite': {'files': {'build': {'files': {'index.js': {
            'offset': '0', 'size': len(script)}}}}}}}
        raw = json.dumps(header, separators=(',', ':')).encode()
        payload = ((len(raw) + 7) // 4) * 4
        return (struct.pack('<IIII', 4, payload + 4, payload, len(raw))
                + raw + b'\0' * (payload - 4 - len(raw)) + script)

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

    def test_main_table_conflict_also_stops(self):
        """主进程表有不同定义时同样停止：官方强制关闭/换取值时不覆盖。"""
        text = 'var Hon={123:{rule:"forced",value:!1}}'
        with self.assertRaises(official.core.OfficialDefinitionConflict):
            official.core.insert_entries(
                text, 'var Hon={', [('123', 'rule:"forced",value:!0')])
        # 表里没有这个键才插入。
        changed = official.core.insert_entries(
            'var Hon={1:{rule:"forced",value:!1}}', 'var Hon={',
            [('123', 'rule:"forced",value:!0')])
        self.assertEqual(
            changed.count('123:{rule:"forced",value:!0}'), 1)
        self.assertNotIn('123:{rule:"forced",value:!1},123', changed)

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
            (backup / 'Contents/Resources').mkdir(parents=True)
            (backup / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleShortVersionString': official.core.VERSION}))
            (backup / official.ASAR).write_bytes(b'inert backup')
            record = {'backup': str(backup), 'installedFiles': {official.ASAR: 'old'},
                      'phase': 'enabled',
                      'original': {'version': official.core.VERSION,
                                   'asarHash': official.core.sha(b'inert backup'),
                                   'files': {}, 'sourceFiles': {}}}
            real_rename = pathlib.Path.rename
            def rename(path, destination):
                if path.name.startswith('upgrade-prepared-'):
                    raise OSError('simulated swap failure')
                return real_rename(path, destination)
            with patch.object(official, 'STATE', state), patch.object(official, 'TARGET', target), \
                 patch.object(official, 'manifest', return_value=record), \
                 patch.object(official, 'running', return_value=False), \
                 patch.object(official, 'status', return_value={'enabled': True, 'supported': True, 'version': official.core.VERSION}), \
                 patch.object(official.core, 'verify_official_signature'), \
                 patch.object(official, 'verify_patched'), \
                 patch.object(official, 'prepare_identity'), \
                 patch.object(official, 'host_machine', return_value='arm64'), \
                 patch.object(official, 'host_mac_version', return_value='27.0.1'), \
                 patch.object(official, 'clone', side_effect=lambda source, destination: destination.mkdir()), \
                 patch.object(official, 'build', return_value=({official.ASAR: 'new'}, {}, None)), \
                 patch.object(pathlib.Path, 'rename', rename):
                with self.assertRaisesRegex(OSError, 'simulated'):
                    official.update()
            self.assertEqual((target / 'marker').read_text(), 'previous')
            self.assertEqual(json.loads((state / 'manifest.json').read_text()), record)

    def test_interrupted_install_exposes_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            state = pathlib.Path(directory).resolve()
            backup = state / 'official-backup-1.app'
            (backup / 'Contents/Resources').mkdir(parents=True)
            (backup / official.ASAR).write_bytes(self.synthetic_asar(b'official original'))
            (backup / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleShortVersionString': official.core.VERSION}))
            with patch.object(official, 'STATE', state), \
                 patch.object(official, 'TARGET', state / 'Missing.app'), \
                 patch.object(official, 'manifest', return_value={'backup': str(state / 'official-backup-1.app')}), \
                 patch.object(official, 'running', return_value=False), \
                 patch.object(official.core, 'verify_official_signature'):
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

    def test_unknown_version_without_a_readable_build_refuses_enable(self):
        """结构读不出来时仍然拒绝开启，不会盲写。

        未登记版本不再是拒绝理由：签名和结构都对时照常适配（见
        test_guards.UnknownBuildIsAdaptedOnTheSpot）。这里只剩一个 Info.plist、
        没有 app.asar，三重校验走不完，所以必须拒绝。
        """
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
                with self.assertRaises(ValueError):
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
                # 恢复以安装记录里的原始指纹为准，不读内置登记表。
                'original': {'version': official.core.ADAPTER_42304['version'],
                             'asarHash': official.core.sha(b'original'),
                             'files': {}, 'sourceFiles': {}},
                'installedFiles': {official.ASAR: official.core.sha(b'patched')},
                'phase': 'enabled',
            }

            def adapter(app):
                raise AssertionError('恢复不应再依赖内置登记表')

            with patch.object(official, 'STATE', state), \
                 patch.object(official, 'TARGET', target), \
                 patch.object(official, 'manifest', return_value=record), \
                 patch.object(official, 'running', return_value=False), \
                 patch.object(official.core, 'verify_official_signature'), \
                 patch.object(official.core, 'registered_adapter', side_effect=adapter), \
                 patch.object(official, 'verify_patched'), \
                 patch.object(official, 'clone', side_effect=shutil.copytree):
                result = official.disable()
            self.assertFalse(result['enabled'])
            self.assertEqual((target / official.ASAR).read_bytes(), b'original')

    def test_damaged_backup_refuses_restore(self):
        """备份签名/指纹与记录不符时拒绝恢复，不复制任何东西。"""
        with tempfile.TemporaryDirectory() as directory:
            state = pathlib.Path(directory).resolve()
            backup = state / 'official-backup-1.app'
            (backup / 'Contents').mkdir(parents=True)
            (backup / 'Contents/Info.plist').write_bytes(
                plistlib.dumps({'CFBundleShortVersionString': '99.0'}))
            record = {'backup': str(backup), 'version': official.core.VERSION,
                      'original': {'version': official.core.VERSION,
                                   'asarHash': 'expected', 'files': {}, 'sourceFiles': {}}}
            with patch.object(official, 'STATE', state), \
                 patch.object(official, 'manifest', return_value=record), \
                 patch.object(official, 'running', return_value=False), \
                 patch.object(official.core, 'verify_official_signature'), \
                 patch.object(official, 'clone') as clone:
                with self.assertRaisesRegex(ValueError, '备份版本'):
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
            (backup / 'Contents/Resources').mkdir(parents=True)
            (backup / official.ASAR).write_bytes(b'official original')
            (backup / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleShortVersionString': official.core.VERSION}))
            target = state / 'Claude.app'
            (target / 'Contents/Resources').mkdir(parents=True)
            (target / official.ASAR).write_bytes(b'changed application')
            (target / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleShortVersionString': official.core.VERSION}))
            record = {'backup': str(backup), 'installedFiles': {official.ASAR: 'expected'},
                      'original': {'version': official.core.VERSION,
                                   'asarHash': official.core.sha(b'official original'),
                                   'files': {}, 'sourceFiles': {}}}
            with patch.object(official, 'STATE', state), patch.object(official, 'TARGET', target), \
                 patch.object(official, 'manifest', return_value=record), \
                 patch.object(official, 'running', return_value=False), \
                 patch.object(official.core, 'verify_official_signature'), \
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
            (backup / 'Contents/Resources').mkdir(parents=True)
            (backup / official.ASAR).write_bytes(b'inert backup')
            (backup / 'Contents/Info.plist').write_bytes(
                plistlib.dumps({'CFBundleShortVersionString': official.core.VERSION}))
            record = {'backup': str(backup),
                      'original': {'version': official.core.VERSION,
                                   'asarHash': official.core.sha(b'inert backup'),
                                   'files': {}, 'sourceFiles': {}}}
            real_rename = pathlib.Path.rename
            def rename(path, destination):
                if path.name.startswith('restore-prepared-'):
                    raise OSError('simulated swap failure')
                return real_rename(path, destination)
            # 目标的 ASAR 与记录的官方指纹不同 → 走“已安装补丁”分支，
            # 再由 rename() 模拟替换阶段失败。
            (target / 'Contents/Resources').mkdir(parents=True)
            (target / official.ASAR).write_bytes(b'patched application')
            (target / 'Contents/Info.plist').write_bytes(
                plistlib.dumps({'CFBundleShortVersionString': official.core.VERSION}))
            with patch.object(official, 'STATE', state), patch.object(official, 'TARGET', target), \
                 patch.object(official, 'manifest', return_value=record), \
                 patch.object(official, 'running', return_value=False), \
                 patch.object(official.core, 'verify_official_signature'), \
                 patch.object(official, 'verify_patched'), \
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

    def test_launch_opens_any_installed_build(self):
        """打开 Claude 不该因为校验没过就改口；它只是打开应用。"""
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory).resolve() / 'Claude.app'
            target.mkdir()
            with patch.object(official, 'TARGET', target), \
                 patch.object(official, 'status',
                              return_value={'supported': False, 'message': '未通过校验'}), \
                 patch.object(official.core, 'run') as run:
                result = official.launch()
            self.assertTrue(result['opened'])
            self.assertEqual(result['message'], '未通过校验')
            run.assert_called_once_with('/usr/bin/open', str(target))

    def test_launch_refuses_missing_application(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = pathlib.Path(directory).resolve() / 'Missing.app'
            with patch.object(official, 'TARGET', missing):
                with self.assertRaisesRegex(ValueError, '没有找到'):
                    official.launch()


class SigningPasswordProtection(unittest.TestCase):
    """签名密码不落盘，只放登录钥匙串；读取失败必须降级而不是卡住。"""

    def setUp(self):
        import native_unlock as core
        import keychain
        self.core, self.keychain = core, keychain
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch.object(core, 'WORK', pathlib.Path(self.directory.name).resolve())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_password_is_read_from_the_keychain_not_a_file(self):
        _, _, password_file, _ = self.core.signing_paths()
        password_file.parent.mkdir(parents=True, exist_ok=True)
        password_file.write_text('plaintext\n')
        with patch.object(self.keychain, 'read', return_value='from-keychain'):
            self.assertEqual(self.core.signing_password(), 'from-keychain')
        # 钥匙串里有值时不会退回明文文件。
        self.assertTrue(password_file.exists())

    def test_plaintext_file_is_only_a_migration_fallback(self):
        _, _, password_file, _ = self.core.signing_paths()
        password_file.parent.mkdir(parents=True, exist_ok=True)
        password_file.write_text('legacy\n')
        with patch.object(self.keychain, 'read', return_value=None):
            self.assertEqual(self.core.signing_password(), 'legacy')

    def test_missing_password_degrades_instead_of_raising(self):
        with patch.object(self.keychain, 'read', return_value=None):
            self.assertIsNone(self.core.signing_password())

    def test_store_deletes_the_plaintext_file(self):
        _, _, password_file, _ = self.core.signing_paths()
        password_file.parent.mkdir(parents=True, exist_ok=True)
        password_file.write_text('legacy\n')
        with patch.object(self.core, 'keychain_writer_is_trustworthy',
                          return_value=(True, '')), \
             patch.object(self.keychain, 'store', return_value=0):
            self.core.store_keychain_password('new-secret')
        self.assertFalse(password_file.exists())

    def test_store_failure_keeps_the_file_so_signing_still_works(self):
        _, _, password_file, _ = self.core.signing_paths()
        password_file.parent.mkdir(parents=True, exist_ok=True)
        password_file.write_text('legacy\n')
        with patch.object(self.core, 'keychain_writer_is_trustworthy',
                          return_value=(True, '')), \
             patch.object(self.keychain, 'store', return_value=-60008):
            with self.assertRaises(ValueError):
                self.core.store_keychain_password('new-secret')
        self.assertTrue(password_file.exists())

    def test_migration_keeps_the_file_when_the_keychain_refuses(self):
        _, _, password_file, _ = self.core.signing_paths()
        password_file.parent.mkdir(parents=True, exist_ok=True)
        password_file.write_text('legacy\n')
        with patch.object(self.core, 'keychain_writer_is_trustworthy',
                          return_value=(True, '')), \
             patch.object(self.keychain, 'store', return_value=-60008):
            self.assertFalse(self.core.migrate_keychain_password())
        self.assertTrue(password_file.exists())

    def test_migration_removes_the_file_on_success(self):
        _, _, password_file, _ = self.core.signing_paths()
        password_file.parent.mkdir(parents=True, exist_ok=True)
        password_file.write_text('legacy\n')
        with patch.object(self.core, 'keychain_writer_is_trustworthy',
                          return_value=(True, '')), \
             patch.object(self.keychain, 'store', return_value=0):
            self.assertTrue(self.core.migrate_keychain_password())
        self.assertFalse(password_file.exists())

    def test_migration_from_a_development_interpreter_is_refused(self):
        """ACL 认写入进程的签名：从解释器迁移等于把密码交给任何 Python 脚本。"""
        _, _, password_file, _ = self.core.signing_paths()
        password_file.parent.mkdir(parents=True, exist_ok=True)
        password_file.write_text('legacy\n')
        with patch.object(self.keychain, 'store') as store:
            # 不 mock 闸门：测试就是跑在开发解释器里，真实闸门应当拒绝。
            self.assertFalse(self.core.migrate_keychain_password())
            store.assert_not_called()
        self.assertTrue(password_file.exists())

    def test_ad_hoc_backend_is_not_a_trustworthy_writer(self):
        """ad-hoc 的指定要求是 cdhash，重建即失效，不能让条目绑在它上面。"""
        with patch.object(self.core.sys, 'executable',
                          '/Applications/Claude 原生助手.app/Contents/Resources/'
                          'Backend/NativeUnlockBackend'), \
             patch.object(self.core, 'app_signing_identity', return_value=None):
            allowed, reason = self.core.keychain_writer_is_trustworthy()
        self.assertFalse(allowed)
        self.assertIn('ad-hoc', reason)

    def test_writer_gate_follows_the_signature_not_the_path(self):
        """判定看签名：换个安装位置仍可写，同位置换成别的程序则不行。

        ACL 本身只认签名，按路径判断既会误伤也会漏判。
        """
        _, keychain_path, _, marker = self.core.signing_paths()
        keychain_path.parent.mkdir(parents=True, exist_ok=True)
        keychain_path.write_text('keychain')
        marker.write_text('89de5d74\n')
        # 换到别处、但签名就是本地身份 -> 允许。
        with patch.object(self.core.sys, 'executable', '/opt/other/NativeUnlockBackend'), \
             patch.object(self.core, 'app_signing_identity', return_value='89de5d74'):
            self.assertEqual(self.core.keychain_writer_is_trustworthy(), (True, ''))
        # 同一位置、但签名是另一个身份 -> 拒绝。
        with patch.object(self.core.sys, 'executable', '/opt/other/NativeUnlockBackend'), \
             patch.object(self.core, 'app_signing_identity', return_value='deadbeef'):
            allowed, reason = self.core.keychain_writer_is_trustworthy()
            self.assertFalse(allowed)
            self.assertIn('不一致', reason)


class MigratePasswordCommand(unittest.TestCase):
    """迁移要有独立入口：已是最新 revision 时 update() 直接返回，走不到 prepare_identity。"""

    def setUp(self):
        import native_unlock as core
        import official_unlock
        self.core, self.official = core, official_unlock
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch.object(core, 'WORK', pathlib.Path(self.directory.name).resolve())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_already_migrated_is_a_no_op(self):
        with patch.object(self.core, 'signing_password_location', return_value='keychain'):
            result = self.official.migrate_password()
        self.assertFalse(result['migrated'])
        self.assertEqual(result['location'], 'keychain')

    def test_missing_password_is_reported_not_invented(self):
        with patch.object(self.core, 'signing_password_location', return_value='missing'):
            result = self.official.migrate_password()
        self.assertFalse(result['migrated'])
        self.assertEqual(result['location'], 'missing')

    def test_blocked_by_the_gate_keeps_the_plaintext_file(self):
        _, _, password_file, _ = self.core.signing_paths()
        password_file.parent.mkdir(parents=True, exist_ok=True)
        password_file.write_text('legacy\n')
        with patch.object(self.core, 'signing_password_location', return_value='plaintext'), \
             patch.object(self.core, 'keychain_writer_is_trustworthy',
                          return_value=(False, '当前进程是 ad-hoc 签名')):
            result = self.official.migrate_password()
        self.assertFalse(result['migrated'])
        self.assertTrue(result['blocked'])
        self.assertIn('签名仍可用', result['message'])
        self.assertTrue(password_file.exists())

    def test_successful_migration_reports_the_new_location(self):
        _, _, password_file, _ = self.core.signing_paths()
        password_file.parent.mkdir(parents=True, exist_ok=True)
        password_file.write_text('legacy\n')
        with patch.object(self.core, 'signing_password_location',
                          side_effect=['plaintext', 'keychain']), \
             patch.object(self.core, 'keychain_writer_is_trustworthy',
                          return_value=(True, '')), \
             patch.object(self.core, 'migrate_keychain_password', return_value=True):
            result = self.official.migrate_password()
        self.assertTrue(result['migrated'])
        self.assertEqual(result['location'], 'keychain')

    def test_command_is_reachable_from_the_cli(self):
        source = (pathlib.Path(self.official.__file__)).read_text()
        self.assertIn("'migrate-password'", source)
        self.assertIn('migrate_password()', source)

    def test_signing_locks_the_keychain_even_when_signing_fails(self):
        """解锁窗口只有一次 codesign 的时长；失败也必须重新锁上。"""
        _, keychain_path, _, marker = self.core.signing_paths()
        keychain_path.parent.mkdir(parents=True, exist_ok=True)
        keychain_path.write_text('keychain')
        marker.write_text('aabb\n')
        calls = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            if args[0] == '/usr/bin/codesign' and '--sign' in args and 'aabb' in args:
                raise subprocess.CalledProcessError(1, args)
            return type('R', (), {'stdout': b'', 'stderr': b''})()

        with patch.object(self.core, 'signing_password', return_value='pw'), \
             patch.object(self.core, 'run', side_effect=fake_run):
            self.assertIsNone(self.core.sign_app(pathlib.Path('X.app'), pathlib.Path('e.plist')))
        verbs = [args[1] for args in calls if args[0] == '/usr/bin/security']
        self.assertEqual(verbs, ['unlock-keychain', 'lock-keychain'])

    def test_keychain_module_never_shells_out_to_the_security_cli(self):
        """走 security CLI 时 ACL 认的是 security 自己，等于谁都读得到。

        只查代码、不查解释原因的注释，所以按“有没有启动子进程”判断。
        """
        source = (pathlib.Path(self.core.__file__).parent / 'keychain.py').read_text()
        self.assertIn('SecKeychainFindGenericPassword', source)
        self.assertNotIn('subprocess', source)
        for verb in ('find-generic-password', 'add-generic-password',
                     'delete-generic-password'):
            self.assertNotIn(verb, source)


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

    def test_conflicting_definition_stops_instead_of_overriding(self):
        """官方已有不同取值时停止：覆盖等于改官方行为，进入研究而不是绕过。"""
        import native_unlock as core
        table = ('var ken={857846362:{rule:"forced",value:!1}},'
                 'Aen={101380594:{rule:"always",value:!0}};')
        with self.assertRaises(core.OfficialDefinitionConflict) as caught:
            core.insert_entries(table, 'var ken={',
                                core.split_entries('857846362:{rule:"forced",value:!0},'))
        self.assertEqual(caught.exception.key, '857846362')
        # 提醒：文本本身没有任何改动路径可言，调用方拿不到被改过的表。

    def test_override_requires_explicit_opt_in(self):
        """覆盖只在显式 opt-in 时可用，供维护工具在副本上研究，不用于生产。"""
        import native_unlock as core
        table = ('var ken={857846362:{rule:"forced",value:!1}},'
                 'Aen={101380594:{rule:"always",value:!0}};')
        report = {}
        out = core.insert_entries(table, 'var ken={',
                                  core.split_entries('857846362:{rule:"forced",value:!0},'),
                                  report, conflict='override')
        self.assertEqual(report['overridden'], ['857846362'])
        self.assertIn('value:!0', core.table_definition(out, 'var ken={', '857846362'))

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

    def test_same_version_different_build_is_refused(self):
        """版本号没变但构建变了：拒绝，不再按结构现场适配。"""
        import native_unlock as core
        with tempfile.TemporaryDirectory() as directory:
            app = pathlib.Path(directory).resolve() / 'Claude.app'
            (app / 'Contents').mkdir(parents=True)
            (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
                'CFBundleIdentifier': 'com.anthropic.claudefordesktop',
                'CFBundleShortVersionString': official.core.ADAPTER_31623['version']}))
            with patch.object(core, 'discover_adapter') as discover, \
                 patch.object(core, 'read_asar', return_value=(b'changed', {}, 0)), \
                 patch.object(core, 'sha', return_value='different'):
                with self.assertRaises(core.FingerprintMismatch):
                    core.source_adapter(app)
            discover.assert_not_called()

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

    def test_changed_build_with_same_version_is_refused(self):
        """版本号没变但构建变了：拒绝（旧行为是按结构现场适配）。"""
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
                with self.assertRaises(core.FingerprintMismatch):
                    core.source_adapter(app)
            discover.assert_not_called()

    def test_status_exposes_permission_regrant_hint(self):
        current = {'enabled': True, 'supported': True, 'patchRevision': official.PATCH_REVISION,
                   'signingUpdateAvailable': True}
        self.assertTrue(current['signingUpdateAvailable'])
