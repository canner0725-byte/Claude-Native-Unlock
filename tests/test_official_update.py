"""官方新版提醒的离线回归测试。

全部离线：用夹具复现官方响应，不联网、不读本机 Claude.app。
真实联网由 `python3 src/backend.py official_update check` 自己跑。

要守住的断言：

- 渠道地址仍按官方公式拼，探针标识固定，不带账号或用户配置；
- 「没查到」永远是 unknown，绝不会被说成「已是最新」；
- 检查失败保留上次结论并说明原因，不抹掉缓存；
- 只读合并检测不联网，只读缓存；
- 缓存写入拒绝符号链接，先写临时文件再原子替换。
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import official_update as update  # noqa: E402

OFFICIAL_RESPONSE = {
    'currentRelease': '3.9683.0',
    'releases': [{
        'version': '3.9683.0',
        'updateTo': {
            'name': 'Claude 3.9683.0',
            'version': '3.9683.0',
            'url': ('https://downloads.claude.ai/releases/darwin/universal/'
                    '3.9683.0/Claude-fixture.zip'),
            'sha256': 'a' * 64,
            'size': 123,
        },
    }],
}


class ChannelRequest(unittest.TestCase):
    def test_url_matches_the_official_formula(self):
        url = update.update_url('https://api.anthropic.com', '3.6556.2',
                                os_version='15.0')
        self.assertEqual(
            url,
            'https://api.anthropic.com/api/desktop/darwin/universal/squirrel/update'
            '?device_id=probe-0000&version=3.6556.2&os_version=15.0')
        self.assertNotIn('channelUuid', url)

    def test_probe_identity_is_fixed_and_carries_no_account(self):
        # 助手不能带用户账号、设备标识或第三方 API 地址去查官方渠道。
        self.assertEqual(update.DEVICE_ID, 'probe-0000')
        self.assertEqual(update.DEFAULT_BASES,
                         ('https://api.anthropic.com', 'https://releases.claude.com'))
        source = (ROOT / 'src/official_update.py').read_text()
        for forbidden in ('keychain', 'api_key', 'API_KEY', 'Claude-3p'):
            self.assertNotIn(forbidden, source)

    def test_unknown_channel_and_arch_are_refused(self):
        for kwargs in ({'channel': 'beta'}, {'arch': 'riscv'}):
            with self.assertRaises(update.ChannelError):
                update.update_url('https://api.anthropic.com', '1.0',
                                  os_version='15.0', **kwargs)

    def test_unrecognized_response_shapes_raise(self):
        for payload in ([], {'releases': []}, {'currentRelease': '1.0'},
                        {'currentRelease': '1.0', 'releases': [{}]}):
            with self.assertRaises(update.ChannelError):
                update.parse_update_json(payload)


class Fetch(unittest.TestCase):
    """联网走系统 curl：打包后的 Python 加载证书库失败，urllib 用不了。"""

    def run_curl(self, returncode, stdout=b'{}'):
        return patch.object(update.subprocess, 'run',
                            return_value=subprocess.CompletedProcess(
                                args=[], returncode=returncode, stdout=stdout))

    def test_uses_the_system_curl_and_returns_the_body(self):
        with patch.object(update.subprocess, 'run',
                          return_value=subprocess.CompletedProcess(
                              args=[], returncode=0, stdout=b'{"ok":1}')) as run:
            result = update.fetch('https://api.anthropic.com/fixture')
        self.assertEqual(result['body'], b'{"ok":1}')
        command = run.call_args[0][0]
        self.assertEqual(command[0], '/usr/bin/curl')
        self.assertIn('--fail', command)
        self.assertIn('https://api.anthropic.com/fixture', command)
        # 打包后的 Python 加载证书库失败，所以不能再退回 urllib。
        self.assertNotIn('import urllib',
                         (ROOT / 'src/official_update.py').read_text())

    def test_http_error_and_network_error_are_told_apart(self):
        with self.run_curl(22):
            with self.assertRaises(update.ChannelError) as caught:
                update.fetch('https://api.anthropic.com/fixture')
        self.assertNotIsInstance(caught.exception, update.NetworkError)
        for code in (6, 7, 28, 35, 60):
            with self.run_curl(code):
                with self.assertRaises(update.NetworkError):
                    update.fetch('https://api.anthropic.com/fixture')


class Summarise(unittest.TestCase):
    def cache(self, latest, checked_at=None, epoch=None):
        return {'latest': latest,
                'checkedAt': update.stamp(checked_at or 0),
                'checkedAtEpoch': checked_at if epoch is None else epoch}

    def test_no_cache_is_unchecked_not_up_to_date(self):
        result = update.summarise('3.6556.2', None, now=1000)
        self.assertEqual(result['state'], 'unchecked')
        self.assertNotEqual(result['state'], 'current')

    def test_same_or_older_channel_is_current(self):
        result = update.summarise('3.6556.2', self.cache('3.6556.2', 900), now=1000)
        self.assertEqual(result['state'], 'current')
        result = update.summarise('3.6556.2', self.cache('3.1623.0', 900), now=1000)
        self.assertEqual(result['state'], 'current')

    def test_newer_channel_is_available_and_reports_adaptation(self):
        result = update.summarise('3.6556.2', self.cache('3.9683.0', 900), now=1000)
        self.assertEqual(result['state'], 'available')
        # 3.9683.0 不在登记表里：界面必须说“还没适配”，不能只说“有新版”。
        self.assertFalse(result['adapted'])

    def test_known_newer_version_is_marked_adapted(self):
        with patch.object(update.core, 'adapters_for_version',
                          return_value=[{'version': '3.9683.0'}]):
            result = update.summarise('3.6556.2', self.cache('3.9683.0', 900), now=1000)
        self.assertTrue(result['adapted'])

    def test_old_cache_is_flagged_stale(self):
        fresh = update.summarise('3.6556.2', self.cache('3.9683.0', 1000), now=1100)
        old = update.summarise('3.6556.2',
                               self.cache('3.9683.0', 1000 - update.CACHE_TTL - 1), now=1000)
        self.assertFalse(fresh['stale'])
        self.assertTrue(old['stale'])

    def test_missing_installed_version_is_unknown(self):
        result = update.summarise('', self.cache('3.9683.0', 900), now=1000)
        self.assertEqual(result['state'], 'unknown')
        self.assertNotEqual(result['state'], 'current')


class Check(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        state = pathlib.Path(temp.name).resolve()
        self.state = state
        patch.object(update.official, 'STATE', state).start()
        self.addCleanup(patch.stopall)

    def test_success_writes_cache_and_reports_available(self):
        with patch.object(update, 'installed_version', return_value='3.6556.2'), \
             patch.object(update, 'query', return_value={
                 'currentRelease': '3.9683.0', 'base': 'https://api.anthropic.com',
                 'url': 'fixture', 'builds': []}) as query:
            result = update.check()
        query.assert_called_once()
        self.assertTrue(result['checked'])
        self.assertEqual(result['state'], 'available')
        self.assertEqual(result['latest'], '3.9683.0')
        saved = json.loads((self.state / update.CACHE_NAME).read_text())
        self.assertEqual(saved['latest'], '3.9683.0')

    def test_network_failure_keeps_the_previous_conclusion(self):
        with patch.object(update, 'installed_version', return_value='3.6556.2'), \
             patch.object(update, 'query', return_value={
                 'currentRelease': '3.9683.0', 'base': 'x', 'url': 'x', 'builds': []}):
            update.check()
        with patch.object(update, 'installed_version', return_value='3.6556.2'), \
             patch.object(update, 'query',
                          side_effect=update.NetworkError('联网失败（URLError）')):
            result = update.check()
        self.assertFalse(result['checked'])
        self.assertEqual(result['state'], 'available')
        self.assertEqual(result['latest'], '3.9683.0')
        self.assertIn('网络', result['error'])
        # 失败不能把旧缓存改坏或删掉。
        self.assertTrue((self.state / update.CACHE_NAME).is_file())

    def test_failure_without_cache_is_unchecked_not_current(self):
        with patch.object(update, 'installed_version', return_value='3.6556.2'), \
             patch.object(update, 'query', side_effect=update.ChannelError('HTTP 500')):
            result = update.check()
        self.assertEqual(result['state'], 'unchecked')
        self.assertNotEqual(result['state'], 'current')
        self.assertTrue(result['error'])

    def test_short_reason_never_echoes_the_url(self):
        error = update.ChannelError('渠道返回 HTTP 500：https://api.anthropic.com/secret')
        self.assertNotIn('https://', update.short_reason(error))

    def test_symlinked_state_directory_is_not_written(self):
        outside = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: outside.rmdir())
        self.state.rmdir()
        self.state.symlink_to(outside, target_is_directory=True)
        with patch.object(update, 'installed_version', return_value='3.6556.2'), \
             patch.object(update, 'query', return_value={
                 'currentRelease': '3.9683.0', 'base': 'x', 'url': 'x', 'builds': []}):
            result = update.check()
        self.assertEqual(list(outside.iterdir()), [])
        # 不写缓存也不影响这次结论；只是下次启动要重新查。
        self.assertEqual(result['state'], 'available')

    def test_corrupt_cache_is_treated_as_missing(self):
        (self.state / update.CACHE_NAME).write_text('{not json')
        self.assertIsNone(update.read_cache())


class CachedStatus(unittest.TestCase):
    """合并检测只读缓存：不联网，也不碰 /Applications。"""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state = pathlib.Path(temp.name).resolve()
        patch.object(update.official, 'STATE', self.state).start()
        self.addCleanup(patch.stopall)

    def test_reads_the_cache_without_network(self):
        (self.state / update.CACHE_NAME).write_text(json.dumps({
            'latest': '3.9683.0', 'checkedAt': update.stamp(0),
            'checkedAtEpoch': 0}))
        with patch.object(update, 'query',
                          side_effect=AssertionError('不应联网')):
            result = update.cached_status({'version': '3.6556.2'})
        self.assertEqual(result['state'], 'available')

    def test_uninstalled_marker_is_not_used_as_a_version(self):
        result = update.cached_status({'version': '未安装'})
        self.assertEqual(result['state'], 'unknown')


class BackendWiring(unittest.TestCase):
    def test_status_all_carries_the_cached_reminder(self):
        import backend
        with patch.object(backend, 'configure'), \
             patch('official_unlock.status', return_value={'supported': True}), \
             patch('native_cli_unlock.status', return_value={'enabled': False}), \
             patch('native_features.report', return_value={'features': []}), \
             patch.object(update, 'cached_status',
                          return_value={'state': 'current', 'installed': '1.0',
                                        'latest': '1.0'}) as cached:
            result = backend.status_all()
        self.assertEqual(result['officialUpdate']['state'], 'current')
        cached.assert_called_once()

    def test_a_failing_reminder_does_not_hide_the_other_sections(self):
        import backend
        with patch.object(backend, 'configure'), \
             patch('official_unlock.status', return_value={'supported': True}), \
             patch('native_cli_unlock.status', return_value={'enabled': False}), \
             patch('native_features.report', return_value={'features': []}), \
             patch.object(update, 'cached_status', side_effect=ValueError('synthetic')):
            result = backend.status_all()
        self.assertEqual(result['officialUpdate']['state'], 'unknown')
        self.assertTrue(result['official']['supported'])
        self.assertNotIn('synthetic', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
