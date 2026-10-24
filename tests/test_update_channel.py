"""官方更新渠道探测与更新代办的回归测试。

这一组全部**离线**：用夹具复现官方响应，不联网、不读本机 Claude.app。
真实的联网探测由 `scripts/update_channel.py` 自己跑，测试只保证：

- 官方公式拼出来的地址是对的（渠道 UUID、参数顺序）；
- 官方响应能解析成版本 + 指纹；
- 结构不对、字段缺失、渠道为空，都明确报错而不是静默返回空；
- 更新代办的三种结论判断正确，且演练脚本绝不执行写操作。

教训写在这里：上一个版本把渠道地址猜成了 `latest-mac.yml`，结果四次 404，
却因为“响应不是更新元数据”只是一句 note 而看起来像成功。所以“结构不对
必须抛异常”本身就是要守住的断言。
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'src'))
import update_channel  # noqa: E402
import rehearse_update  # noqa: E402

# 官方 stable 渠道在 2026-10-23 的真实响应（去掉与本工具无关的字段）。
OFFICIAL_RESPONSE = {
    "currentRelease": "3.6556.2",
    "releases": [{
        "version": "3.6556.2",
        "updateTo": {
            "name": "Claude 3.6556.2",
            "version": "3.6556.2",
            "pub_date": "2026-10-23T00:36:12.758104",
            "url": ("https://downloads.claude.ai/releases/darwin/universal/"
                    "3.6556.2/Claude-20c019a1e61fa5b687616af57d31f97bb3181723.zip"),
            "notes": "Production Release - No Notes",
            "sha256": "5f3e36bd0c500f0e02cca84848f5caf453372117ec664153db187b372754c67a",
            "size": 378072300,
        },
    }],
}


class UpdateUrl(unittest.TestCase):
    def test_stable_has_no_channel_uuid(self):
        url = update_channel.update_url('https://api.anthropic.com', '3.6556.2',
                                        'stable', os_version='15.0')
        self.assertEqual(
            url,
            'https://api.anthropic.com/api/desktop/darwin/universal/squirrel/update'
            '?device_id=probe-0000&version=3.6556.2&os_version=15.0')
        self.assertNotIn('channelUuid', url)

    def test_latest_and_staggered_carry_their_uuid(self):
        # UUID 抄自官方源码 AQr 表；抄错就会查到别的渠道，必须钉住。
        for channel, uuid in (
                ('latest', '77a14fc4-808e-4345-ac3e-090be671bf94'),
                ('staggered', '51760d71-67e2-4366-9bf5-4221cda21f61')):
            url = update_channel.update_url('https://api.anthropic.com', '3.6556.2',
                                            channel, os_version='15.0')
            self.assertIn('&channelUuid=' + uuid, url)

    def test_unknown_channel_and_arch_are_refused(self):
        with self.assertRaises(update_channel.ChannelError):
            update_channel.update_url('https://api.anthropic.com', '1.0', 'beta',
                                      os_version='15.0')
        with self.assertRaises(update_channel.ChannelError):
            update_channel.update_url('https://api.anthropic.com', '1.0', 'stable',
                                      arch='riscv', os_version='15.0')

    def test_arch_goes_into_the_path_not_the_query(self):
        # 实测 arm64 与 universal 是内容不同的包（sha256 不同），
        # 所以架构必须进路径；漏了它就会拿错校验值。
        url = update_channel.update_url('https://api.anthropic.com', '3.6556.2',
                                        'stable', arch='arm64', os_version='15.0')
        self.assertIn('/api/desktop/darwin/arm64/squirrel/update?', url)
        self.assertNotIn('arch=', url)

    def test_missing_version_or_os_version_is_refused(self):
        # 参数不全时不能拼出一个“看起来能用的”地址。
        with self.assertRaises(update_channel.ChannelError):
            update_channel.update_url('https://api.anthropic.com', '', 'stable',
                                      os_version='15.0')
        with self.assertRaises(update_channel.ChannelError):
            update_channel.update_url('https://api.anthropic.com', '1.0', 'stable',
                                      os_version='')


class ParseResponse(unittest.TestCase):
    def test_official_shape_yields_version_and_fingerprint(self):
        parsed = update_channel.parse_update_json(OFFICIAL_RESPONSE)
        self.assertEqual(parsed['currentRelease'], '3.6556.2')
        self.assertEqual(len(parsed['builds']), 1)
        build = parsed['builds'][0]
        self.assertEqual(build['version'], '3.6556.2')
        self.assertEqual(build['size'], 378072300)
        self.assertEqual(len(build['sha256']), 64)

    def test_empty_releases_is_not_an_error_but_is_visible(self):
        # staggered 渠道实测就是这种形状：有当前版本，但没有可下载构建。
        parsed = update_channel.parse_update_json(
            {'currentRelease': '3.6556.2', 'releases': []})
        self.assertEqual(parsed['builds'], [])

    def test_unrecognized_shapes_raise(self):
        bad = [
            [],                                                    # 不是对象
            {'releases': []},                                      # 缺 currentRelease
            {'currentRelease': '1.0'},                             # 缺 releases
            {'currentRelease': '1.0', 'releases': {}},             # releases 不是数组
            {'currentRelease': '1.0', 'releases': [{}]},           # 缺 updateTo
            {'currentRelease': '1.0', 'releases': [{'updateTo': {}}]},  # 缺 version/url
        ]
        for payload in bad:
            with self.assertRaises(update_channel.ChannelError):
                update_channel.parse_update_json(payload)

    def test_yaml_feed_is_gone_not_silently_ignored(self):
        # 官方用的是 json 服务；把旧 YAML 解析留着会让人以为还支持。
        self.assertFalse(hasattr(update_channel, 'parse_yaml_feed'))


class CompareVersions(unittest.TestCase):
    def test_numeric_segments_not_lexicographic(self):
        # 按字符串比会把 3.9683.0 判成比 3.6556.2 小，正好判反。
        self.assertEqual(update_channel.compare_versions('3.9683.0', '3.6556.2'), 1)
        self.assertEqual(update_channel.compare_versions('3.6556.2', '3.9683.0'), -1)
        self.assertEqual(update_channel.compare_versions('3.6556.2', '3.6556.2'), 0)
        self.assertEqual(update_channel.compare_versions('3.6556', '3.6556.0'), 0)


class Decision(unittest.TestCase):
    def test_offline_gives_unknown_not_up_to_date(self):
        decision = rehearse_update.decide({'version': '3.6556.2'}, None, [])
        self.assertEqual(decision['kind'], 'unknown')

    def test_same_version_is_up_to_date(self):
        feed = update_channel.parse_update_json(OFFICIAL_RESPONSE)
        decision = rehearse_update.decide({'version': '3.6556.2'}, feed, ['3.6556.2'])
        self.assertEqual(decision['kind'], 'up_to_date')

    def test_new_version_absent_from_registry_needs_adaptation(self):
        feed = update_channel.parse_update_json(OFFICIAL_RESPONSE)
        decision = rehearse_update.decide({'version': '3.1623.0'}, feed, ['3.1623.0'])
        self.assertEqual(decision['kind'], 'needs_adaptation')

    def test_new_adapted_version_with_old_patch_needs_yield(self):
        feed = update_channel.parse_update_json(OFFICIAL_RESPONSE)
        decision = rehearse_update.decide(
            {'version': '3.1623.0', 'enabled': True}, feed, ['3.6556.2'])
        self.assertEqual(decision['kind'], 'needs_yield')
        self.assertTrue(decision['patched'])

    def test_new_adapted_version_without_patch_is_ready(self):
        feed = update_channel.parse_update_json(OFFICIAL_RESPONSE)
        decision = rehearse_update.decide(
            {'version': '3.1623.0', 'enabled': False}, feed, ['3.6556.2'])
        self.assertEqual(decision['kind'], 'ready_to_update')


class RehearsalIsReadOnly(unittest.TestCase):
    def test_yield_plan_never_executes(self):
        plan = rehearse_update.yield_plan({'running': False})
        self.assertFalse(plan['executed'])
        self.assertIn('prepare-update', plan['command'])
        # 计划里必须写明它不会做的事，避免被读成“会自动更新”。
        joined = ' '.join(plan['will_not'])
        self.assertIn('不会下载', joined)
        self.assertIn('不会删除', joined)

    def test_yield_plan_reports_running_and_recovery(self):
        plan = rehearse_update.yield_plan({'running': True, 'recoveryRequired': True})
        self.assertTrue(plan['running'])
        self.assertTrue(plan['recoveryRequired'])

    def test_rehearsal_never_imports_a_write_path(self):
        # 演练脚本只能调 status()；出现 enable/update/disable 就是越界。
        source = (ROOT / 'scripts/rehearse_update.py').read_text()
        for forbidden in ('official_unlock.enable(',
                          'official_unlock.update(',
                          'official_unlock.disable(',
                          'official_unlock.prepare_update('):
            self.assertNotIn(forbidden, source)


class AdaptedVersions(unittest.TestCase):
    def test_registry_is_read_from_adapters(self):
        versions = rehearse_update.adapted_versions()
        self.assertIn('3.6556.2', versions)
        # 排序按数字段：3.1623.0 必须排在 3.6556.2 前面。
        self.assertLess(versions.index('3.1623.0'), versions.index('3.6556.2'))

    def test_registry_entries_carry_fingerprints(self):
        import native_unlock as core
        for name in dir(core):
            if not name.startswith('ADAPTER_'):
                continue
            adapter = getattr(core, name)
            self.assertTrue(adapter.get('version'), name + ' 缺少 version')
            self.assertTrue(adapter.get('files'), name + ' 缺少文件指纹')


class AppValidation(unittest.TestCase):
    def test_missing_app_is_refused(self):
        with self.assertRaises(update_channel.ChannelError):
            update_channel.app_info(pathlib.Path('/nonexistent/Claude.app'))

    def test_bundle_and_team_are_the_official_ones(self):
        self.assertEqual(update_channel.OFFICIAL_BUNDLE,
                         'com.anthropic.claudefordesktop')
        self.assertEqual(update_channel.OFFICIAL_TEAM, 'Q6L2SF6YDW')


if __name__ == '__main__':
    unittest.main()
