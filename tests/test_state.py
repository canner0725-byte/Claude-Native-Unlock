"""机器可读状态与文档一致性。

`state/status.json` 和 `catalog/features.json` 由 `scripts/state.py` 从代码生成，
是文档的唯一事实来源。这里把它接进公开测试，防止文档和代码各说一套。
"""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import state  # noqa: E402


class StateMatchesCode(unittest.TestCase):
    def test_checked_in_state_is_current(self):
        problems = state.check(state.status())
        self.assertEqual(problems, [], '\n'.join(problems))

    def test_every_registered_build_has_a_verified_system_range(self):
        for adapter in state.status()['adapters']:
            self.assertIsNotNone(adapter['verifiedArchitecture'],
                                 adapter['version'])
            self.assertIsNotNone(adapter['systemCeiling'], adapter['version'])

    def test_ui_order_matches_the_backend_catalog(self):
        rows = state.status()['uiRows']
        self.assertEqual([row['id'] for row in rows[:-1]], list(state.load()[2].FEATURES))
        self.assertEqual(rows[-1]['id'], 'code-auto-dream')

    def test_catalog_covers_every_gate_of_the_latest_build(self):
        current = state.status()
        catalog = state.catalog(current)
        latest = current['adapters'][-1]
        for gate in latest['rendererGates'] + latest['mainGates']:
            row = next((item for item in catalog['rows']
                        if item.get('officialKey') == gate), None)
            if row is None:
                # 主进程门（如提示建议设置项）由官方配置项承载，不单列。
                continue
            self.assertTrue(row['evidence'], gate)
            self.assertIn('layer', row)
            self.assertIn('limits', row)

    def test_catalog_entries_declare_evidence_and_limits(self):
        for row in state.catalog(state.status())['rows']:
            with self.subTest(row=row['id']):
                self.assertTrue(row['evidence'])
                self.assertTrue(row['limits'])
                self.assertIsInstance(row['layer'], int)

    def test_every_shipped_gate_has_an_admission_record(self):
        """第五节 9：补进去的每个门都要按六条准入标准复核过，结论为保留。"""
        current = state.status()
        latest = current['adapters'][-1]
        rows = {row['officialKey']: row for row in state.catalog(current)['rows']}
        for gate in latest['rendererGates'] + latest['mainGates']:
            with self.subTest(gate=gate):
                self.assertIn(gate, rows)
                row = rows[gate]
                self.assertEqual(row['admission'], '保留')
                self.assertIn('controls', row)
                self.assertIn('readPoints', row)
                self.assertIsInstance(row['displayOnly'], bool)
                self.assertIsInstance(row['touchesPermissions'], bool)

    def test_no_shipped_gate_touches_permissions(self):
        """准入标准第 3 条：任何进补丁的门都不能参与权限/审批/沙箱判定。"""
        for row in state.catalog(state.status())['rows']:
            if row['id'].startswith(('renderer-', 'main-')):
                with self.subTest(gate=row['officialKey']):
                    self.assertFalse(row['touchesPermissions'], row['controls'])


if __name__ == '__main__':
    unittest.main()
