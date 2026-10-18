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

    def test_unsupported_desktop_version_has_specific_message(self):
        self.add_version('2.1.299', cli.ADAPTER_21299['subdir'])
        desktop = self.root / 'Claude.app'
        (desktop / 'Contents').mkdir(parents=True)
        (desktop / 'Contents/Info.plist').write_bytes(
            plistlib.dumps({'CFBundleShortVersionString': '9.9.9'}))
        with patch.object(cli.core, 'APP', desktop):
            result = cli.status('test')
        self.assertFalse(result['supported'])
        self.assertIn('Desktop 9.9.9 尚未适配', result['message'])


if __name__ == '__main__':
    unittest.main()
