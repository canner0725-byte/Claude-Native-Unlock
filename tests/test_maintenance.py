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

    def test_a_registered_job_is_checked_against_the_definition(self):
        """“文件存在”不等于“作业合格”：手改坏的 plist 会静默不按计划跑。"""
        import plistlib
        import tempfile
        import setup_check
        import maintenance_job as job
        with tempfile.TemporaryDirectory() as work:
            installed = pathlib.Path(work) / 'installed.plist'
            installed.write_bytes(plistlib.dumps({'Label': job.LABEL, 'RunAtLoad': True}))
            original = job.INSTALLED
            job.INSTALLED = installed
            try:
                result = setup_check.check_background_job()
            finally:
                job.INSTALLED = original
        self.assertEqual(result['state'], setup_check.PENDING)
        self.assertIn('不合格', result['detail'])


class BackgroundJobDefinition(unittest.TestCase):
    """后台作业的调度要求：每 6 小时、不唤醒电脑、不错过就丢、单实例。

    这一组只校验**定义**，不登记、不加载、不执行 launchctl。
    """

    def setUp(self):
        import maintenance_job
        self.job = maintenance_job
        self.data = maintenance_job.job_plist()

    def test_definition_is_valid(self):
        self.assertEqual(self.job.problems_in(self.data), [])

    def test_runs_every_six_hours(self):
        hours = sorted(entry['Hour'] for entry in self.data['StartCalendarInterval'])
        self.assertEqual(hours, [0, 6, 12, 18])

    def test_never_wakes_the_machine(self):
        self.assertFalse(self.data.get('WakeSystem'))
        self.assertFalse(self.data.get('RunAtLoad'))
        self.assertFalse(self.data.get('KeepAlive'))

    def test_does_not_touch_launch_agents(self):
        """写定义不能落进 ~/Library/LaunchAgents——放进去就等于登记了。"""
        self.assertNotEqual(self.job.STAGED.parent, self.job.INSTALLED.parent)
        self.assertNotIn('Library/LaunchAgents', str(self.job.STAGED))
        # 不 import subprocess 就没有能力去调 launchctl；比查字符串可靠。
        source = (ROOT / 'scripts/maintenance_job.py').read_text()
        self.assertNotIn('import subprocess', source)

    def test_write_only_ever_creates_the_staged_file(self):
        """真的跑一次 --write，确认它只碰 dist/，不碰已登记位置。"""
        import contextlib
        import io
        import tempfile
        with tempfile.TemporaryDirectory() as work:
            staged = pathlib.Path(work) / 'staged.plist'
            installed = pathlib.Path(work) / 'installed.plist'
            original = (self.job.STAGED, self.job.INSTALLED)
            self.job.STAGED, self.job.INSTALLED = staged, installed
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.job.write()
            finally:
                self.job.STAGED, self.job.INSTALLED = original
            self.assertTrue(staged.exists(), '定义没有写出来')
            self.assertFalse(installed.exists(), '写定义时碰了已登记位置')

    def test_uses_its_own_config_directory(self):
        env = self.data['EnvironmentVariables']
        self.assertEqual(env['CLAUDE_CONFIG_DIR'], str(self.job.MAINTENANCE_CONFIG))
        self.assertNotEqual(env['CLAUDE_CONFIG_DIR'], str(setup.HOME / '.claude'))


class BackgroundJobGate(unittest.TestCase):
    """外层脚本在零弹窗验证通过之前必须什么都不做。

    这是“先验证、再登记”的兜底：就算有人提前登记了作业，没有闸门文件时
    脚本也只会留一条日志就退出，不会去下载、签名或推分支。
    """

    def run_script(self, config, state):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as work:
            state = pathlib.Path(work) / 'state'
            env = {'HOME': work, 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
                   'CLAUDE_CONFIG_DIR': str(config), 'CNU_MAINTENANCE_STATE': str(state)}
            done = subprocess.run(['/bin/sh', str(ROOT / 'scripts/maintenance_run.sh')],
                                  capture_output=True, text=True, env=env, timeout=30)
        return done, state

    def test_does_nothing_without_the_gate(self):
        import tempfile
        with tempfile.TemporaryDirectory() as config:
            done, state = self.run_script(config, None)
        self.assertEqual(done.returncode, 0)
        self.assertIn('未通过零弹窗验证', done.stdout)
        self.assertIn('跳过', done.stdout)

    def test_even_with_the_gate_it_reports_the_flow_is_not_implemented(self):
        """闸门开着也不能假装维护在工作——流程还没实现就如实说没实现。"""
        import tempfile
        with tempfile.TemporaryDirectory() as config:
            pathlib.Path(config, 'verified').write_text('')
            done, state = self.run_script(config, None)
        self.assertEqual(done.returncode, 0)
        self.assertIn('尚未实现', done.stdout)

    def test_a_stale_lock_does_not_block_forever(self):
        """被 SIGKILL 打断会留下锁；不能因此永远跳过。"""
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as config, \
                tempfile.TemporaryDirectory() as work:
            state = pathlib.Path(work) / 'state'
            (state / 'run.lock').mkdir(parents=True)
            # 一个几乎肯定不存在的 pid：本机 pid 上限远小于这个数。
            (state / 'run.lock' / 'pid').write_text('999999')
            env = {'HOME': work, 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
                   'CLAUDE_CONFIG_DIR': config, 'CNU_MAINTENANCE_STATE': str(state)}
            done = subprocess.run(['/bin/sh', str(ROOT / 'scripts/maintenance_run.sh')],
                                  capture_output=True, text=True, env=env, timeout=30)
            self.assertIn('接管', done.stdout)
            self.assertFalse((state / 'run.lock').exists(), '残留锁没有被清掉')



class ProbeHonesty(unittest.TestCase):
    """动态层没跑就必须说没跑，不能因为静态层通过就当成通过。"""

    def test_dynamic_layer_reports_not_run_when_the_model_is_unavailable(self):
        source = (ROOT / 'scripts/maintenance_probe.py').read_text()
        self.assertIn('这不等于通过', source)
        self.assertIn('未执行', source)


if __name__ == '__main__':
    unittest.main()
