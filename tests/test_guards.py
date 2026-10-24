"""失败即拒绝的回归测试：未知构建、指纹不符、官方定义冲突、语法检查、系统范围。

这一组对应《维护方案 修订版 v2》第五节 1～8 条。所有测试都不依赖宿主机
的平台、系统版本或已安装的 Claude，任何主机上结果一致。
"""
import json
import pathlib
import plistlib
import subprocess
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
import native_unlock as core
import official_unlock as official
import native_cli_unlock as cli


def make_asar(script: bytes) -> bytes:
    """合成一个最小可读的 ASAR：固定头 + 索引 + 文件内容。"""
    header = {'files': {'.vite': {'files': {'build': {'files': {'index.js': {
        'offset': '0', 'size': len(script)}}}}}}}
    raw = json.dumps(header, separators=(',', ':')).encode()
    payload = ((len(raw) + 7) // 4) * 4
    return (struct.pack('<IIII', 4, payload + 4, payload, len(raw))
            + raw + b'\0' * (payload - 4 - len(raw)) + script)


def make_app(root: pathlib.Path, name: str, version: str, script: bytes) -> pathlib.Path:
    app = root / name
    (app / 'Contents/Resources').mkdir(parents=True)
    (app / official.ASAR).write_bytes(make_asar(script))
    (app / 'Contents/Info.plist').write_bytes(
        plistlib.dumps({'CFBundleShortVersionString': version}))
    return app


class UnknownBuildIsDiagnoseOnly(unittest.TestCase):
    """第五节 1：未知构建只诊断、不写入。"""

    def test_unregistered_build_raises_a_typed_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            app = make_app(pathlib.Path(directory).resolve(), 'Claude.app',
                           '99.0', b'var X={1:{rule:"forced",value:!0}};')
            with self.assertRaises(core.UnregisteredBuild):
                core.source_adapter(app)

    def test_unregistered_build_cannot_be_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            app = make_app(pathlib.Path(directory).resolve(), 'Claude.app',
                           '99.0', b'var X={1:{rule:"forced",value:!0}};')
            with patch.object(official, 'TARGET', app), \
                 patch.object(official, 'STATE', pathlib.Path(directory).resolve() / 'state'), \
                 patch.object(official, 'manifest', return_value=None), \
                 patch.object(official, 'running', return_value=False), \
                 patch.object(official, 'prepare_identity'), \
                 patch.object(official, 'clone') as clone:
                (pathlib.Path(directory).resolve() / 'state').mkdir()
                result = official.status()
                self.assertFalse(result['supported'])
                self.assertTrue(result.get('unregisteredBuild'))
                self.assertTrue(result['diagnoseAvailable'])
                with self.assertRaises(core.UnregisteredBuild):
                    official.enable()
                clone.assert_not_called()

    def test_unknown_build_never_opens_renderer_gates(self):
        """旧行为会对未知构建直接打开 11 个渲染进程门：现在连适配器都拿不到。"""
        with tempfile.TemporaryDirectory() as directory:
            app = make_app(pathlib.Path(directory).resolve(), 'Claude.app',
                           '99.0', b'var X={1:{rule:"forced",value:!0}};')
            with patch.object(core, 'discover_adapter') as discover:
                with self.assertRaises(core.UnregisteredBuild):
                    core.source_adapter(app)
            discover.assert_not_called()


class FingerprintMismatchRefuses(unittest.TestCase):
    """第五节 2：已登记版本指纹不符一律拒绝。"""

    def test_registered_version_with_other_asar_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            app = make_app(pathlib.Path(directory).resolve(), 'Claude.app',
                           core.ADAPTER_36556['version'], b'not the real build')
            with self.assertRaises(core.FingerprintMismatch):
                core.source_adapter(app)

    def test_refusal_message_is_actionable(self):
        with tempfile.TemporaryDirectory() as directory:
            app = make_app(pathlib.Path(directory).resolve(), 'Claude.app',
                           core.ADAPTER_36556['version'], b'not the real build')
            with patch.object(official, 'TARGET', app), \
                 patch.object(official, 'STATE', pathlib.Path(directory).resolve() / 'state'), \
                 patch.object(official, 'manifest', return_value=None), \
                 patch.object(official, 'running', return_value=False):
                (pathlib.Path(directory).resolve() / 'state').mkdir()
                result = official.status()
            self.assertFalse(result['supported'])
            self.assertTrue(result.get('buildMismatch'))
            self.assertIn('构建与登记不一致', result['message'])


class OfficialDefinitionConflictStops(unittest.TestCase):
    """第五节 3：官方对目标门有不同定义就停止；相同才跳过；缺失才插入。"""

    def test_forced_off_is_not_overridden(self):
        text = 'var Hon={1291166712:{rule:"forced",value:{enabled:!1}}};'
        with self.assertRaises(core.OfficialDefinitionConflict):
            core.insert_entries(text, 'var Hon={',
                                core.split_entries('1291166712:{rule:"forced",value:{enabled:!0}},'))

    def test_lowered_system_ceiling_is_not_overridden(self):
        text = 'var Hon={1346958739:{rule:"forced",value:{major:26,minor:0}}};'
        with self.assertRaises(core.OfficialDefinitionConflict):
            core.insert_entries(text, 'var Hon={',
                                core.split_entries('1346958739:{rule:"forced",value:{major:27,minor:0}},'))

    def test_identical_definition_is_skipped(self):
        text = 'var Hon={2120747855:{rule:"forced",value:!0}};'
        out = core.insert_entries(text, 'var Hon={',
                                  core.split_entries('2120747855:{rule:"forced",value:!0},'))
        self.assertEqual(out, text)

    def test_missing_definition_is_inserted_at_the_head(self):
        text = 'var Hon={1:{rule:"forced",value:!0}};'
        out = core.insert_entries(text, 'var Hon={',
                                  core.split_entries('2120747855:{rule:"forced",value:!0},'))
        self.assertTrue(out.startswith('var Hon={2120747855:'))

    def test_production_build_uses_stop_not_override(self):
        """build() 的两张表都走默认拒绝；生产路径没有 override。"""
        source = (pathlib.Path(__file__).resolve().parents[1] / 'src/official_unlock.py').read_text()
        self.assertEqual(source.count("conflict='override'"), 0)
        self.assertNotIn("conflict=\"override\"", source)


class SyntaxCheckIsMandatory(unittest.TestCase):
    """第五节 4：语法检查失败即拒绝；检查器缺失同样拒绝。"""

    def test_table_fragment_is_checked_with_system_javascriptcore(self):
        if core.javascript_core() is None:
            self.skipTest('本机没有系统自带的 jsc（非 macOS）')
        text = 'var Hon={1:{rule:"forced",value:!0}};'
        core.check_table_syntax(text, 'var Hon={')

    def test_broken_table_is_refused(self):
        if core.javascript_core() is None:
            self.skipTest('本机没有系统自带的 jsc（非 macOS）')
        # 括号配平、但语法不成立：这类错误只有真的过一遍解析器才发现。
        text = 'var Hon={1:{rule:},2:{rule:"forced",value:!0}};'
        with self.assertRaises(core.SyntaxCheckFailed):
            core.check_table_syntax(text, 'var Hon={')

    def test_unbalanced_table_is_refused_before_parsing(self):
        with self.assertRaises(ValueError):
            core.check_table_syntax('var Hon={1:{rule:"forced",value:!0},2:{rule:};',
                                    'var Hon={')

    def test_missing_checker_refuses_instead_of_skipping(self):
        with patch.object(core, 'javascript_core', return_value=None):
            with self.assertRaises(core.SyntaxCheckUnavailable):
                core.check_table_syntax('var Hon={};', 'var Hon={')

    def test_parser_detail_is_returned_without_source_text(self):
        if core.javascript_core() is None:
            self.skipTest('本机没有系统自带的 jsc（非 macOS）')
        text = 'var Hon={1:{rule:}};'
        with self.assertRaises(core.SyntaxCheckFailed) as caught:
            core.check_table_syntax(text, 'var Hon={')
        self.assertIn('SyntaxError', str(caught.exception))


class SystemRangeComesFromTheRegistry(unittest.TestCase):
    """第五节 5、8：系统范围读规则；上限按已验证版本登记。"""

    def test_verified_range_matches_the_registered_build(self):
        adapter = core.ADAPTER_36556
        core.check_system(adapter, 'arm64', '27.0.1')

    def test_other_system_version_is_refused(self):
        with self.assertRaises(core.BuildRefused) as caught:
            core.check_system(core.ADAPTER_36556, 'arm64', '27.1')
        self.assertIn('27.0.1', str(caught.exception))

    def test_other_architecture_is_refused(self):
        with self.assertRaises(core.BuildRefused):
            core.check_system(core.ADAPTER_36556, 'x86_64', '27.0.1')

    def test_unregistered_adapter_has_no_system_range(self):
        with self.assertRaises(core.BuildRefused):
            core.check_system({'version': '0.0.0'}, 'arm64', '27.0.1')

    def test_system_ceiling_tracks_the_registered_version(self):
        self.assertEqual(core.system_ceiling(core.ADAPTER_36556),
                         '{major:27,minor:0}')

    def test_ceiling_does_not_rise_with_a_newer_host(self):
        """系统升到 28.x 时，登记的 27 构建仍写 27.0，并且写入会被拒绝。"""
        with self.assertRaises(core.BuildRefused):
            core.check_system(core.ADAPTER_36556, 'arm64', '28.0')
        self.assertEqual(core.system_ceiling(core.ADAPTER_36556),
                         '{major:27,minor:0}')

    def test_builtin_patch_uses_the_registry_ceiling(self):
        source = (pathlib.Path(__file__).resolve().parents[1] / 'src/official_unlock.py').read_text()
        self.assertNotIn('major:27,minor:0', source)
        self.assertIn('core.system_ceiling(adapter)', source)

    def test_hard_coded_platform_checks_are_gone(self):
        root = pathlib.Path(__file__).resolve().parents[1] / 'src'
        for name in ('official_unlock.py', 'native_cli_unlock.py'):
            text = (root / name).read_text()
            self.assertNotIn("mac_ver()[0] != '27.0.1'", text, name)

    def test_cli_platform_gate_is_testable_on_any_host(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory).resolve()
            parent = root / 'profile'
            app = parent / 'claude-code' / '2.1.308' / cli.ADAPTER_21308['subdir'] / 'claude.app'
            app.mkdir(parents=True)
            (root / 'Claude Test.app/Contents').mkdir(parents=True)
            (root / 'Claude Test.app/Contents/Info.plist').write_bytes(
                plistlib.dumps({'CFBundleShortVersionString': core.ADAPTER_36556['version']}))
            with patch.object(cli.core, 'WORK', root), \
                 patch.object(cli.core, 'APP', root / 'Claude Test.app'), \
                 patch.object(cli, 'status', return_value={'enabled': False, 'supported': True}), \
                 patch.object(cli, 'check_idle'), \
                 patch.object(cli, 'verify_source'), \
                 patch.object(cli, 'host_machine', return_value='arm64'), \
                 patch.object(cli, 'host_mac_version', return_value='9.9'):
                with self.assertRaisesRegex(ValueError, '仅验证 arm64'):
                    cli.enable('test')


class RecoveryDoesNotNeedTheRegistry(unittest.TestCase):
    """第五节 6：恢复不依赖登记表。"""

    def test_original_fingerprint_is_recorded_on_enable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory).resolve()
            script = b'var Hon={1:{rule:"forced",value:!0}};'
            app = make_app(root, 'Claude.app', core.ADAPTER_36556['version'], script)
            original = official.original_build(app)
            self.assertEqual(original['version'], core.ADAPTER_36556['version'])
            # ASAR 覆盖全部打包内容；源文件单独记，因为打补丁会改写它们。
            self.assertEqual(original['asarHash'], core.sha(make_asar(script)))
            self.assertIn('Contents/Info.plist', original['sourceFiles'])
            self.assertEqual(original['files'],
                             {'.vite/build/index.js': core.sha(script)})

    def test_record_without_registry_entry_still_restores(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory).resolve()
            script = b'var Hon={1:{rule:"forced",value:!0}};'
            backup = make_app(root, 'official-backup-1.app', '9.9.9', script)
            record = {'version': '9.9.9', 'backup': str(backup),
                      'original': official.original_build(backup),
                      'installedFiles': {official.ASAR: 'patched'}}
            with patch.object(official, 'STATE', root), \
                 patch.object(official.core, 'verify_official_signature'):
                self.assertEqual(official.verify_backup(record), backup)

    def test_modified_backup_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory).resolve()
            backup = make_app(root, 'official-backup-1.app', '9.9.9',
                              b'var Hon={1:{rule:"forced",value:!0}};')
            record = {'version': '9.9.9', 'backup': str(backup),
                      'original': official.original_build(backup),
                      'installedFiles': {}}
            (backup / official.ASAR).write_bytes(make_asar(b'var Hon={2:{}};'))
            with patch.object(official, 'STATE', root), \
                 patch.object(official.core, 'verify_official_signature'):
                with self.assertRaisesRegex(ValueError, '官方构建'):
                    official.verify_backup(record)


class InterruptedInstallRecovery(unittest.TestCase):
    """第五节 7：每个阶段中断都能恢复。"""

    def test_missing_target_with_record_is_recoverable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory).resolve()
            backup = make_app(root, 'official-backup-1.app', core.ADAPTER_36556['version'],
                              b'var Hon={1:{rule:"forced",value:!0}};')
            record = {'version': core.ADAPTER_36556['version'], 'backup': str(backup),
                      'original': official.original_build(backup),
                      'installedFiles': {official.ASAR: 'patched'},
                      'phase': 'prepared'}
            with patch.object(official, 'STATE', root), \
                 patch.object(official, 'TARGET', root / 'Missing.app'), \
                 patch.object(official, 'running', return_value=False), \
                 patch.object(official, 'manifest', return_value=record), \
                 patch.object(official.core, 'verify_official_signature'):
                result = official.status()
            self.assertTrue(result['recoveryRequired'])

    def test_prepare_update_recovers_an_interrupted_install(self):
        current = {'enabled': True, 'supported': True, 'recoveryRequired': True,
                   'running': False}
        with patch.object(official, 'status', return_value=current), \
             patch.object(official, 'disable',
                          return_value={'enabled': False, 'supported': True}) as disable:
            result = official.prepare_update()
        self.assertTrue(result['preparedForUpdate'])
        disable.assert_called_once()

    def test_prepare_update_still_refuses_a_running_app(self):
        current = {'enabled': False, 'supported': True, 'running': True}
        with patch.object(official, 'status', return_value=current), \
             patch.object(official, 'disable') as disable:
            with self.assertRaisesRegex(ValueError, '退出 Claude'):
                official.prepare_update()
        disable.assert_not_called()

    def test_upgrade_prepared_phase_is_recognised(self):
        record = {'phase': 'upgrade-prepared', 'installedFiles': {official.ASAR: 'new'},
                  'upgradeFromFiles': {official.ASAR: 'old'}, 'upgradeFromRevision': 13}
        with patch.object(pathlib.Path, 'read_bytes', return_value=b'old'), \
             patch.object(core, 'sha', return_value='old'):
            effective = official.effective_record(pathlib.Path('Synthetic.app'), record)
        self.assertEqual(effective['patchRevision'], 13)


if __name__ == '__main__':
    unittest.main()


class ProtocolVersionIsExplicit(unittest.TestCase):
    """界面遇到不认识的协议版本时提示更新，而不是显示成错误状态。"""

    def test_status_all_carries_the_protocol_version(self):
        import backend
        with patch.object(backend, 'configure'), \
             patch('official_unlock.status', return_value={'supported': True}), \
             patch('native_cli_unlock.status', return_value={'enabled': False}), \
             patch('native_features.report', return_value={'features': []}):
            result = backend.status_all()
        self.assertEqual(result['protocol'], core.PROTOCOL_VERSION)

    def test_error_envelope_carries_the_protocol_version(self):
        self.assertEqual(core.envelope({'error': 'x'})['protocol'],
                         core.PROTOCOL_VERSION)

    def test_envelope_does_not_overwrite_an_existing_version(self):
        self.assertEqual(core.envelope({'protocol': 99})['protocol'], 99)

    def test_swift_declares_the_same_protocol_version(self):
        source = (pathlib.Path(__file__).resolve().parents[1]
                  / 'app/NativeUnlock.swift').read_text()
        self.assertIn('static let version = %d' % core.PROTOCOL_VERSION, source)

    def test_backend_writes_tracebacks_to_stderr_only(self):
        """stdout 必须是界面能解析的 JSON；traceback 只走 stderr。"""
        source = (pathlib.Path(__file__).resolve().parents[1] / 'src/backend.py').read_text()
        self.assertIn('traceback.print_exc()', source)
        self.assertLess(source.index('traceback.print_exc()'),
                        source.index("emit({'error': '操作未完成"))
