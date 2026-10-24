"""维护代理隔离的回归测试。

对应《维护方案 修订版 v2》第七节。这一组只验证**配置本身**是否真的能拦住
越界操作，不依赖本机是否装了维护代理，也不调用模型。

教训写在这里：CLI 遇到一条无效规则（例如 `Write(...)`）会把**整份设置文件
当作没加载**，保护全部失效，而提示只在启动时出现一次。所以“没有无效规则”
本身就是一条必须守住的断言。
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import maintenance_setup as setup  # noqa: E402
import maintenance_probe as probe  # noqa: E402


class MaintenanceSettings(unittest.TestCase):
    def setUp(self):
        self.data = setup.settings()
        self.permissions = self.data['permissions']
        self.sandbox = self.data['sandbox']
        self.deny = self.permissions['deny']

    def test_confirmation_requiring_calls_are_refused_not_asked(self):
        # dontAsk：会弹确认的调用直接拒绝，后台作业不会卡住等人。
        self.assertEqual(self.permissions['defaultMode'], 'dontAsk')
        self.assertEqual(self.permissions['disableBypassPermissionsMode'], 'disable')

    def test_sandbox_cannot_be_escaped(self):
        self.assertTrue(self.sandbox['enabled'])
        self.assertFalse(self.sandbox['allowUnsandboxedCommands'])
        self.assertTrue(self.sandbox['failIfUnavailable'])
        self.assertTrue(self.sandbox['network']['strictAllowlist'])

    def test_every_out_of_bounds_path_is_denied(self):
        for label, kind, target in probe.OUT_OF_BOUNDS:
            with self.subTest(label):
                self.assertIsNotNone(
                    probe.rule_matches(self.deny, kind, target),
                    f'{label} 没有被任何拒绝规则覆盖：{target}')

    def test_every_out_of_bounds_command_is_denied(self):
        for label, command in probe.OUT_OF_BOUNDS_COMMANDS:
            with self.subTest(label):
                self.assertIsNotNone(
                    probe.command_denied(self.deny, command),
                    f'{label} 没有被任何拒绝规则覆盖：{command}')

    def test_backend_install_commands_are_denied_in_both_shapes(self):
        """两种真实调用形状都要拦住；中间隔着模块名，紧邻写法匹配不上。"""
        for command in ('python3 src/backend.py official_unlock enable',
                        'python3 src/official_unlock.py update',
                        '.venv/bin/python src/backend.py official_unlock prepare-update'):
            with self.subTest(command):
                self.assertIsNotNone(probe.command_denied(self.deny, command))

    def test_no_rule_can_silently_disable_the_whole_file(self):
        """一条无效规则会让整份配置不加载——这比少一条规则危险得多。"""
        for rule in self.deny + self.permissions['allow']:
            with self.subTest(rule):
                self.assertFalse(rule.startswith('Write('),
                                 'Write(...) 不是有效的文件规则，应写成 Edit(...)')
                self.assertTrue(rule.startswith(('Read(', 'Edit(', 'Bash(')),
                                f'未知规则形状：{rule}')
                self.assertTrue(rule.endswith(')'), f'规则未闭合：{rule}')

    def test_gateway_is_allowlisted_without_being_written_into_the_repo(self):
        """沙箱开了 strictAllowlist，网关不在名单里就连不上模型。

        但网关地址属于用户自己的部署信息，不能写进仓库——只能来自本机
        0600 文件。这里断言两件事：默认名单不含非 GitHub 域名；本机文件
        里出现的域名会被加进去。
        """
        defaults = setup.gateway_domains()
        self.assertTrue(all('github.com' in d for d in defaults
                            if d not in ('api.github.com', 'codeload.github.com')))
        source = (ROOT / 'scripts/maintenance_setup.py').read_text()
        self.assertNotIn('dragoncode', source)

    def test_key_file_never_contains_a_secret(self):
        """占位脚本必须自己报错退出，而不是带着密钥进仓库。"""
        source = (ROOT / 'scripts/maintenance_setup.py').read_text()
        self.assertIn('维护代理密钥未配置', source)
        self.assertNotIn('ANTHROPIC_AUTH_TOKEN', source)
        self.assertNotIn('sk-', source)


class SetupCheck(unittest.TestCase):
    """一次性设置体检必须读真实入口，不能读仓库内的 work/。"""

    def test_reads_through_the_real_entry_point(self):
        source = (ROOT / 'scripts/setup_check.py').read_text()
        self.assertIn('backend.configure()', source)

    def test_screen_lock_detection_uses_the_right_binary(self):
        # ioreg 在 /usr/sbin，写成 /usr/bin 会永远返回“读不出来”。
        source = (ROOT / 'scripts/setup_check.py').read_text()
        self.assertIn('/usr/sbin/ioreg', source)
        self.assertNotIn("'/usr/bin/ioreg'", source)


class ProbeHonesty(unittest.TestCase):
    """动态层没跑就必须说没跑，不能因为静态层通过就当成通过。"""

    def test_dynamic_layer_reports_not_run_when_the_model_is_unavailable(self):
        source = (ROOT / 'scripts/maintenance_probe.py').read_text()
        self.assertIn('这不等于通过', source)
        self.assertIn('未执行', source)


if __name__ == '__main__':
    unittest.main()
