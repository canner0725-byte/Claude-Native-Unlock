import pathlib
import plistlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
import native_cli_unlock as cli


class CliVersionSelection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name).resolve()
        self.parent = self.root / 'profile'
        (self.parent / 'claude-code').mkdir(parents=True)
        self.stack = __import__('contextlib').ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(cli.core, 'WORK', self.root))
        self.stack.enter_context(patch.object(cli.core, 'APP', self.root / 'Claude Test.app'))

    def add_version(self, version, subdir):
        app = self.parent / 'claude-code' / version / subdir / 'claude.app'
        app.mkdir(parents=True)
        return app

    def test_latest_installed_cli_is_selected(self):
        old = self.add_version('2.1.295', cli.ADAPTER_21295['subdir'])
        new = self.add_version('2.1.299', cli.ADAPTER_21299['subdir'])
        target, state, _, adapter = cli.locations('test')
        self.assertEqual(adapter['version'], '2.1.299')
        self.assertEqual(target, new)
        self.assertNotEqual(target, old)
        self.assertEqual(state, self.root / 'native-code-memory' / 'test' / '2.1.299')

    def test_newer_unsupported_cli_is_refused_instead_of_using_old_adapter(self):
        self.add_version('2.1.299', cli.ADAPTER_21299['subdir'])
        self.add_version('2.1.300', 'unknownsubdir')
        with self.assertRaisesRegex(ValueError, 'CLI 2.1.300 尚未适配'):
            cli.locations('test')

    def test_missing_cli_installation_is_refused(self):
        with self.assertRaisesRegex(ValueError, '未找到 Claude Code CLI 安装'):
            cli.locations('test')

    def test_unrelated_directories_are_ignored(self):
        self.add_version('2.1.299', cli.ADAPTER_21299['subdir'])
        (self.parent / 'claude-code' / 'latest').mkdir()
        (self.parent / 'claude-code' / '.DS_Store').write_text('x')
        target, _, _, adapter = cli.locations('test')
        self.assertEqual(adapter['version'], '2.1.299')
        self.assertTrue(str(target).endswith('claude.app'))

    def test_42304_2_desktop_version_is_supported(self):
        """2.42304.2 仍内置 2.1.299 的 CLI 清单，沿用同一个适配器。"""
        self.add_version('2.1.299', cli.ADAPTER_21299['subdir'])
        desktop = self.root / 'Claude.app'
        (desktop / 'Contents').mkdir(parents=True)
        (desktop / 'Contents/Info.plist').write_bytes(
            plistlib.dumps({'CFBundleShortVersionString': '2.42304.2'}))
        with patch.object(cli.core, 'APP', desktop), \
             patch.object(cli, 'verify_source'), \
             patch.object(cli, 'running', return_value=False):
            result = cli.status('test')
        self.assertTrue(result['supported'])
        self.assertEqual(result['version'], '2.1.299')

    def test_unregistered_desktop_version_still_reports_the_cli(self):
        """桌面端没登记不该让 Code 这一栏失效。

        桌面端版本不是这一栏的准入条件：CLI 补丁认的是 CLI 二进制指纹和
        官方签名。以前这里是一张固定白名单，官方升一次桌面端就整栏报
        “尚未适配”，哪怕 CLI 补丁装得好好的。
        """
        self.add_version('2.1.299', cli.ADAPTER_21299['subdir'])
        desktop = self.root / 'Claude.app'
        (desktop / 'Contents').mkdir(parents=True)
        (desktop / 'Contents/Info.plist').write_bytes(
            plistlib.dumps({'CFBundleShortVersionString': '9.9.9'}))
        self.assertNotIn('9.9.9', cli.core.VERIFIED_SYSTEM)
        with patch.object(cli.core, 'APP', desktop), \
             patch.object(cli, 'verify_source'), \
             patch.object(cli, 'running', return_value=False):
            result = cli.status('test')
        self.assertTrue(result['supported'])
        self.assertEqual(result['desktopVersion'], '9.9.9')
        self.assertNotIn('尚未适配', result['message'])

    def test_unregistered_desktop_uses_host_system_range(self):
        """没登记的桌面端按本机系统范围校验，登记过的按登记表。"""
        desktop = self.root / 'Claude.app'
        (desktop / 'Contents').mkdir(parents=True)
        (desktop / 'Contents/Info.plist').write_bytes(
            plistlib.dumps({'CFBundleShortVersionString': '9.9.9'}))
        adapter = cli.desktop_adapter(desktop)
        self.assertTrue(adapter['discovered'])
        self.assertEqual(adapter['system'][0], cli.host_machine())
        self.assertEqual(
            adapter['system'][1],
            tuple(int(p) for p in cli.host_mac_version().split('.'))[:3])
        # 登记过的桌面端不标 discovered，系统范围回到登记表。
        registered = self.root / 'Claude2.app'
        (registered / 'Contents').mkdir(parents=True)
        (registered / 'Contents/Info.plist').write_bytes(plistlib.dumps(
            {'CFBundleShortVersionString': cli.core.ADAPTER_36556['version']}))
        self.assertFalse(cli.desktop_adapter(registered)['discovered'])


if __name__ == '__main__':
    unittest.main()
