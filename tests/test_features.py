import contextlib
import io
import json
import os
import pathlib
import plistlib
import re
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]/'src'))
import native_features as features


class NativeConfigurationSafety(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name).resolve()
        self.data = self.root / 'data'
        library = self.data / 'configLibrary'
        library.mkdir(parents=True)
        self.config = library / 'test.json'
        self.meta = library / '_meta.json'
        self.meta.write_text(json.dumps({'appliedId': 'test'}))
        self.original = {'inferenceProvider': 'gateway', 'inferenceGatewayApiKey': 'SYNTHETIC-SECRET',
                         'unrelated': {'a': 1}, 'coworkEgressAllowedHosts': ['example.test']}
        self.config.write_text(json.dumps(self.original))
        self.state = self.root / 'state'
        self.state.mkdir()
        self.stack = contextlib.ExitStack()
        for name, value in [('DATA', self.data), ('STATE', self.state)]:
            self.stack.enter_context(patch.object(features, name, value))
        self.stack.enter_context(patch.object(features, 'managed', return_value=False))
        self.stack.enter_context(patch.object(features.official, 'running', return_value=False))
        self.stack.enter_context(patch.object(features.official, 'status', return_value={
            'supported': True, 'version': features.official.core.VERSION, 'running': False}))

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    def read(self):
        return json.loads(self.config.read_text())

    def test_roundtrip_preserves_secrets_and_unrelated_config(self):
        features.change('browser')
        self.assertEqual(self.read(), {**self.original, 'builtinBrowserEnabled': True})
        self.assertEqual(os.stat(self.config).st_mode & 0o777, 0o600)
        for path in self.state.iterdir():
            self.assertNotIn('SYNTHETIC-SECRET', path.read_text())
        features.change('browser', restore=True)
        self.assertEqual(self.read(), self.original)

    def test_restore_keeps_later_unrelated_edits(self):
        features.change('file-analysis')
        latest = self.read()
        latest['unrelated'] = {'a': 2}
        latest['inferenceGatewayApiKey'] = 'ROTATED-SYNTHETIC-SECRET'
        self.config.write_text(json.dumps(latest))
        features.change('file-analysis', restore=True)
        self.assertNotIn('desktopHome', self.read())
        self.assertEqual(self.read()['unrelated'], {'a': 2})
        self.assertEqual(self.read()['inferenceGatewayApiKey'], 'ROTATED-SYNTHETIC-SECRET')

    def test_restore_recovers_preexisting_value(self):
        self.config.write_text(json.dumps({**self.original, 'desktopHome': 'simple'}))
        features.change('file-analysis')
        features.change('file-analysis', restore=True)
        self.assertEqual(self.read()['desktopHome'], 'simple')

    def test_existing_enabled_value_is_not_taken_over(self):
        self.config.write_text(json.dumps({**self.original, 'builtinBrowserEnabled': True}))
        before = self.config.read_bytes()
        features.change('browser')
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(list(self.state.iterdir()), [])
        with self.assertRaisesRegex(ValueError, '没有本工具'):
            features.change('browser', restore=True)

    def test_running_application_refuses_all_changes(self):
        with patch.object(features.official, 'running', return_value=True):
            for restore in [False, True]:
                with self.assertRaisesRegex(ValueError, '退出 Claude'):
                    features.change('browser', restore)
        self.assertEqual(self.read(), self.original)

    def test_unknown_application_refuses_configuration(self):
        with patch.object(features.official, 'status', return_value={'supported': False}):
            with self.assertRaisesRegex(ValueError, '校验未通过'):
                features.change('browser')
        self.assertEqual(self.read(), self.original)

    def test_managed_or_bootstrap_configuration_refuses_changes(self):
        with patch.object(features, 'managed', return_value=True):
            with self.assertRaisesRegex(ValueError, '管理配置'):
                features.change('browser')
        self.config.write_text(json.dumps({**self.original, 'bootstrapUrl': 'https://example.test'}))
        with self.assertRaisesRegex(ValueError, '配置服务器'):
            features.change('browser')

    def test_config_identifier_traversal_is_rejected(self):
        self.meta.write_text(json.dumps({'appliedId': '../outside'}))
        with self.assertRaisesRegex(ValueError, 'ID 不合法'):
            features.change('browser')

    def test_symlink_configuration_is_rejected(self):
        other = self.root / 'elsewhere.json'
        self.config.rename(other)
        self.config.symlink_to(other)
        with self.assertRaises(OSError):
            features.change('browser')
        self.assertEqual(json.loads(other.read_text()), self.original)

    def test_concurrent_edit_is_not_overwritten(self):
        original_read = features.active_config
        count = 0

        def read():
            nonlocal count
            count += 1
            if count == 2:
                self.config.write_text(json.dumps({**self.original, 'otherEdit': True}))
            return original_read()

        with patch.object(features, 'active_config', side_effect=read):
            with self.assertRaisesRegex(ValueError, '操作期间变化'):
                features.change('browser')
        self.assertTrue(self.read()['otherEdit'])
        self.assertNotIn('builtinBrowserEnabled', self.read())

    def test_interrupted_replace_can_resume_or_restore(self):
        write = features.atomic_write

        def crash(path, value):
            if path == self.config:
                raise OSError('simulated crash before replace')
            return write(path, value)

        with patch.object(features, 'atomic_write', side_effect=crash):
            with self.assertRaises(OSError):
                features.change('browser')
        features.change('browser')
        self.assertTrue(self.read()['builtinBrowserEnabled'])
        features.change('browser', restore=True)
        self.assertEqual(self.read(), self.original)

    def test_interruption_after_replace_keeps_original_recovery(self):
        write = features.atomic_write
        count = 0

        def crash(path, value):
            nonlocal count
            count += 1
            if count == 3:
                raise OSError('simulated crash after replace')
            return write(path, value)

        with patch.object(features, 'atomic_write', side_effect=crash):
            with self.assertRaises(OSError):
                features.change('browser')
        features.change('browser', restore=True)
        self.assertEqual(self.read(), self.original)

    def test_changed_owned_key_is_not_overwritten(self):
        features.change('file-analysis')
        self.config.write_text(json.dumps({**self.read(), 'desktopHome': 'no-vm'}))
        with self.assertRaisesRegex(ValueError, '其他操作修改'):
            features.change('file-analysis', restore=True)
        self.assertEqual(self.read()['desktopHome'], 'no-vm')

    def test_record_from_current_42304_version_is_accepted(self):
        record = {
            'version': features.official.core.ADAPTER_42304['version'],
            'configId': self.config.stem,
            'feature': 'browser',
            'key': 'builtinBrowserEnabled',
            'value': True,
            'beforePresent': False,
            'phase': 'enabled',
        }
        journal = features.record_path(self.config, 'browser')
        journal.write_text(json.dumps(record))
        loaded = features.load_record(self.config, 'browser')
        self.assertEqual(loaded['version'], features.official.core.ADAPTER_42304['version'])

    def test_record_from_current_42304_2_version_is_accepted(self):
        """官方升级到 2.42304.2 后，旧记录仍要能读，不能要求用户重开开关。"""
        version = features.official.core.ADAPTER_42304_2['version']
        self.assertIn(version, features.SUPPORTED_VERSIONS)
        record = {
            'version': version,
            'configId': self.config.stem,
            'feature': 'browser',
            'key': 'builtinBrowserEnabled',
            'value': True,
            'beforePresent': False,
            'phase': 'enabled',
        }
        features.record_path(self.config, 'browser').write_text(json.dumps(record))
        self.assertEqual(features.load_record(self.config, 'browser')['version'], version)

    def test_desktop_extensions_and_tool_search_are_off_until_set(self):
        """这两项官方默认关闭：没写就是没启用，不能显示成已开启。"""
        self.assertFalse(features.effective('desktop-extensions', self.original))
        self.assertFalse(features.effective('tool-search', self.original))

    def test_desktop_extensions_roundtrip(self):
        features.change('desktop-extensions')
        self.assertEqual(self.read(), {**self.original, 'isDesktopExtensionEnabled': True})
        self.assertTrue(features.effective('desktop-extensions', self.read()))
        features.change('desktop-extensions', restore=True)
        self.assertEqual(self.read(), self.original)
        self.assertNotIn('isDesktopExtensionEnabled', self.read())

    def test_tool_search_roundtrip_keeps_other_keys(self):
        features.change('tool-search')
        self.assertEqual(self.read(), {**self.original, 'toolSearchEnabled': True})
        features.change('tool-search', restore=True)
        self.assertEqual(self.read(), self.original)

    def test_new_switches_refuse_a_non_boolean_existing_value(self):
        self.config.write_text(json.dumps({**self.original, 'toolSearchEnabled': 'yes'}))
        with self.assertRaisesRegex(ValueError, '值异常'):
            features.change('tool-search')
        self.assertEqual(self.read()['toolSearchEnabled'], 'yes')

    def test_change_refuses_a_key_this_version_does_not_have(self):
        with patch.object(features.official, 'config_keys', return_value=set()):
            with self.assertRaisesRegex(ValueError, '没有这个设置'):
                features.change('tool-search')
        self.assertNotIn('toolSearchEnabled', self.read())

    def test_status_never_contains_credentials(self):
        self.assertNotIn('SYNTHETIC-SECRET', json.dumps(features.report()))

    def test_report_reuses_precomputed_application_status(self):
        application = {'version': features.official.core.VERSION,
                       'supported': True, 'running': False}
        with patch.object(features.official, 'status') as status:
            result = features.report(application)
        status.assert_not_called()
        self.assertTrue(result['configWritable'])
        self.assertTrue(any(item['id'] == 'browser' for item in result['features']))

    def test_malformed_config_error_does_not_echo_credentials(self):
        self.config.write_text('{"SYNTHETIC-SECRET": invalid}')
        output = io.StringIO()
        with patch.object(sys, 'argv', ['native_features.py', 'enable', 'browser']), contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit):
                features.main()
        self.assertNotIn('SYNTHETIC-SECRET', output.getvalue())


class ConfigKeyAvailability(unittest.TestCase):
    """官方可能在新版里删掉某个设置键；删了就禁用开关，不能装作能点。"""

    def fake_app(self, root):
        app = root / 'Claude.app'
        (app / 'Contents/Resources').mkdir(parents=True)
        script = (b'var ken={1:{rule:"forced",value:!0}};'
                  b'var x={flatKey:"toolSearchEnabled"};')
        header = {'files': {'.vite': {'files': {'build': {'files': {'index.js': {
            'offset': '0', 'size': len(script)}}}}}}}
        raw = json.dumps(header, separators=(',', ':')).encode()
        payload = ((len(raw) + 7) // 4) * 4
        asar = (struct.pack('<IIII', 4, payload + 4, payload, len(raw))
                + raw + b'\0' * (payload - 4 - len(raw)) + script)
        (app / features.official.ASAR).write_bytes(asar)
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(
            {'CFBundleShortVersionString': features.official.core.ADAPTER_31623['version']}))
        return app

    def test_reads_flat_keys_from_the_installed_chunk(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.fake_app(pathlib.Path(directory).resolve())
            adapter = {'version': '3.1623.0', 'main': '.vite/build/index.js'}
            with patch.object(features.official, 'TARGET', app), \
                 patch.object(features.official, 'CONFIG_KEY_CACHE', {}), \
                 patch.object(features.official.core, 'adapters_for_version',
                              return_value=[adapter]):
                keys = features.official.config_keys()
            self.assertIn('toolSearchEnabled', keys)
            self.assertNotIn('skipWebFetchPreflight', keys)

    def test_missing_key_is_reported_unavailable(self):
        application = {'version': '3.1623.0', 'supported': True, 'running': False}
        with patch.object(features.official, 'status', return_value=application), \
             patch.object(features.official, 'config_keys',
                          return_value={'builtinBrowserEnabled'}):
            result = features.report()
        item = next(f for f in result['features'] if f['id'] == 'tool-search')
        self.assertFalse(item['available'])
        self.assertFalse(item['enabled'])
        self.assertEqual(item['message'], '此版本没有这个设置')


class ConfigHealthLog(unittest.TestCase):
    """只读 Claude 自己的日志判断配置自检结果，读不到就不猜。"""

    def test_reads_the_last_health_line(self):
        with tempfile.TemporaryDirectory() as directory:
            log = pathlib.Path(directory) / 'main.log'
            log.write_text(
                "2026-10-21 22:22:21 [info] [custom-3p] ConfigHealth recomputed "
                "{ state: 'healthy', provider: 'gateway' }\n"
                "2026-10-22 00:15:15 [info] [custom-3p] ConfigHealth recomputed "
                "{ state: 'provider_error', provider: 'gateway' }\n")
            health = features.config_health(log)
        self.assertEqual(health['state'], 'provider_error')
        self.assertFalse(health['healthy'])
        self.assertEqual(health['at'], '2026-10-22 00:15:15')

    def test_missing_log_returns_none(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(features.config_health(pathlib.Path(directory) / 'none.log'))

    def test_log_without_health_line_returns_none(self):
        with tempfile.TemporaryDirectory() as directory:
            log = pathlib.Path(directory) / 'main.log'
            log.write_text('2026-10-22 00:15:15 [info] Starting app\n')
            self.assertIsNone(features.config_health(log))

    def test_unhealthy_health_is_surfaced_in_report(self):
        application = {'version': '3.1623.0', 'supported': True, 'running': True}
        with patch.object(features.official, 'status', return_value=application), \
             patch.object(features.official, 'config_keys', return_value=None), \
             patch.object(features, 'config_health',
                          return_value={'state': 'auth_failed', 'at': '2026-10-22 00:15:15',
                                        'healthy': False}):
            result = features.report()
        self.assertFalse(result['configHealth']['healthy'])
        self.assertIn('auth_failed', result['message'])

    def test_health_from_before_the_last_change_is_marked_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            log = root / 'main.log'
            log.write_text(
                "2020-01-01 00:00:00 [info] [custom-3p] ConfigHealth recomputed "
                "{ state: 'healthy', provider: 'gateway' }\n")
            state = root / 'state'
            state.mkdir()
            (state / 'record.json').write_text('{}')  # mtime 是现在
            with patch.object(features, 'STATE', state):
                health = features.config_health(log)
        self.assertTrue(health['stale'])
        self.assertTrue(health['healthy'])

    def test_stale_health_message_explains_restart(self):
        application = {'version': '3.1623.0', 'supported': True, 'running': True}
        with patch.object(features.official, 'status', return_value=application), \
             patch.object(features.official, 'config_keys', return_value=None), \
             patch.object(features, 'config_health',
                          return_value={'state': 'healthy', 'at': '2026-10-22 00:15:15',
                                        'healthy': True, 'stale': True}):
            result = features.report()
        self.assertIn('下次启动', result['message'])


class CodeSessionFlags(unittest.TestCase):
    """只读运行中的 Code CLI，核对桌面端是否真的把设置传了下去。"""

    def run_flags(self, listing, env=''):
        from types import SimpleNamespace
        def fake(*args):
            if args[:2] == ('ps', '-axo'):
                return SimpleNamespace(stdout=listing.encode())
            if args[:2] == ('ps', 'eww'):
                return SimpleNamespace(stdout=env.encode())
            raise AssertionError(args)
        with patch.object(features.official.core, 'run', side_effect=fake):
            return features.code_session_flags()

    def test_detects_settings_and_tool_search_env(self):
        line = ('111 /Users/x/Library/Application Support/Claude-3p/claude-code/2.1.304/abc/'
                'claude.app/Contents/MacOS/claude --output-format stream-json '
                '--settings {skipWebFetchPreflight:true} --verbose\n')
        flags = self.run_flags(line, 'claude ENABLE_TOOL_SEARCH=force')
        self.assertEqual(flags['count'], 1)
        self.assertTrue(flags['preflight'])
        self.assertTrue(flags['toolSearch'])

    def test_reports_false_when_switch_not_passed(self):
        line = ('111 /Users/x/Library/Application Support/Claude-3p/claude-code/2.1.304/abc/'
                'claude.app/Contents/MacOS/claude --output-format stream-json '
                '--settings {deniedMcpServers:[]} --verbose\n')
        flags = self.run_flags(line, 'claude CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1')
        self.assertEqual(flags['count'], 1)
        self.assertFalse(flags['preflight'])
        self.assertFalse(flags['toolSearch'])

    def test_tool_search_is_read_from_managed_settings(self):
        """桌面端把 ENABLE_TOOL_SEARCH 放在 --managed-settings 里，不在进程环境。"""
        line = ('111 /Users/x/Library/Application Support/Claude-3p/claude-code/2.1.304/abc/'
                'claude.app/Contents/MacOS/claude --output-format stream-json '
                '--managed-settings {env:{ENABLE_TOOL_SEARCH:force}} '
                "--settings {skipWebFetchPreflight:true}\n")
        flags = self.run_flags(line, 'claude CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1')
        self.assertEqual(flags['count'], 1)
        self.assertTrue(flags['preflight'])
        self.assertTrue(flags['toolSearch'])

    def test_ignores_other_processes(self):
        flags = self.run_flags('222 /usr/bin/something --output-format stream-json\n')
        self.assertEqual(flags['count'], 0)


class InterfaceParity(unittest.TestCase):
    """界面里的开关列表必须和后端的 FEATURES 对得上，避免一边加了一边没加。"""

    def setUp(self):
        self.source = (pathlib.Path(__file__).resolve().parents[1]/'app/NativeUnlock.swift').read_text()
        match = re.search(r'let featureIDs = \[(.*?)\]', self.source, re.S)
        self.ids = re.findall(r'"([a-z-]+)"', match.group(1))

    def test_ui_ids_match_backend_feature_keys(self):
        # 界面最后一个开关是 Code 自动整理，它走 CLI 后端，不在 FEATURES 里。
        self.assertEqual(self.ids[:-1], list(features.FEATURES))
        self.assertEqual(self.ids[-1], 'code-auto-dream')

    def test_ui_rows_have_titles_and_descriptions(self):
        titles = re.search(r'let featureTitles = \[(.*?)\]', self.source, re.S).group(1)
        self.assertEqual(len(re.findall(r'"', titles)) // 2, len(self.ids))
        descriptions = re.search(r'let featureDescriptions = \[(.*?)\]\n', self.source, re.S).group(1)
        self.assertEqual(len(re.findall(r'^\s*"', descriptions, re.M)), len(self.ids))

    def test_ui_state_arrays_cover_every_row(self):
        for name in ['featureEnabled', 'featureRestorable']:
            match = re.search(rf'var {name}: \[Bool\] = Array\(repeating: false, count: (\d+)\)', self.source)
            self.assertIsNotNone(match, name)
            self.assertEqual(int(match.group(1)), len(self.ids), name)


if __name__ == '__main__':
    unittest.main()
