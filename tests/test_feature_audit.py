"""功能表审计工具的单元测试。

这个工具只读官方 JS 源码、不碰安装，所以可以用合成片段测，
不需要真的装一份 Claude.app。
"""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import feature_audit as audit  # noqa: E402


SAMPLE = (
    'function JU(){let e=xFr();return{launch:RU,'
    'quietPenguin:jPr(PFr),wakeScheduler:jPr(QFr),'
    'spaceMemoryBridge:Pb("1197768857")?RU:{status:"unavailable"},'
    'multiAccount:!K().authentication.disableMultiAccount&&Pb("96101707")?RU:{status:"unavailable"},'
    'sideSessions:Sir()?RU:{status:"unavailable"},'
    'dead:{status:"unavailable"},'
    'trayIcon:RU}}'
)


class Classify(unittest.TestCase):
    def test_literals_are_not_evidence(self):
        self.assertEqual(audit.classify("RU"), "literal_supported")
        self.assertEqual(audit.classify('{status:"unavailable"}'), "literal_unavailable")
        self.assertEqual(audit.classify('{status:"unsupported",reason:"x"}'), "literal_unsupported")

    def test_predicates_are_evidence(self):
        # 三元优先：先判条件再二选一，比「以 Pb( 开头」更贴实际形状
        self.assertEqual(audit.classify('Pb("1197768857")?RU:{status:"unavailable"}'), "ternary")
        self.assertEqual(audit.classify("jPr(PFr)"), "predicate_call")
        self.assertEqual(audit.classify('Sir()?RU:{status:"unavailable"}'), "ternary")

    def test_bare_gate_lookup_without_ternary(self):
        self.assertEqual(audit.classify('Pb("1197768857")'), "gate_lookup")

    def test_question_mark_inside_call_is_not_ternary(self):
        """可选链 `?.` 和参数里的问号不能当成三元。"""
        # K() 是函数调用，`.workspace?.x` 里的 `?.` 不该把它变成三元
        self.assertEqual(audit.classify("K().workspace?.keepAwakeEnabled!==!1"), "predicate_call")
        self.assertEqual(audit.classify("jPr(a.app.isPackaged?RU:0)"), "predicate_call")
        # `??` 同理：整条是「以函数调用开头」，不是三元
        self.assertEqual(audit.classify("RMe(K())??zMe(K())"), "predicate_call")


class ExtractEntries(unittest.TestCase):
    def test_reads_every_entry(self):
        entries = dict(audit.extract_ju_entries(SAMPLE))
        self.assertEqual(len(entries), 8)
        self.assertEqual(entries["launch"], "RU")
        self.assertEqual(entries["dead"], '{status:"unavailable"}')
        self.assertIn("Pb(\"1197768857\")", entries["spaceMemoryBridge"])

    def test_ternary_with_nested_braces_is_not_split(self):
        """顶层切分不能被三元里的对象字面量打断。"""
        entries = dict(audit.extract_ju_entries(SAMPLE))
        self.assertEqual(entries["multiAccount"],
                         '!K().authentication.disableMultiAccount&&Pb("96101707")?RU:{status:"unavailable"}')

    def test_missing_ju_raises(self):
        with self.assertRaises(ValueError):
            audit.extract_ju_entries("function other(){return{}}")

    def test_variables_before_return_are_ignored(self):
        """`return` 前面的局部变量不能混进条目。"""
        entries = dict(audit.extract_ju_entries(SAMPLE))
        self.assertNotIn("e", entries)


class LiveFormula(unittest.TestCase):
    """用真实的 JU() 片段（含 Hon 表）验证字面量与判定的分界。

    没有安装时跳过，不让这个测试成为环境依赖。
    """

    @classmethod
    def setUpClass(cls):
        try:
            cls.result = audit.audit(audit.DEFAULT_APP)
        except (FileNotFoundError, ValueError):
            raise unittest.SkipTest("本机没有可读的 Claude.app")

    def test_counts_add_up(self):
        counts = self.result["counts"]
        literals = (counts.get("literal_supported", 0)
                    + counts.get("literal_unavailable", 0)
                    + counts.get("literal_unsupported", 0))
        self.assertEqual(self.result["literal_entries"], literals)
        self.assertEqual(self.result["predicate_entries"] + literals, self.result["entries"])

    def test_hon_table_readable(self):
        self.assertIsNone(self.result["hon_error"])
        self.assertGreater(self.result["hon_entries"], 0)

    def test_known_literal_is_flagged(self):
        """btwChildSessions 在预载表里是字面量——这正是它曾误导我们的原因。"""
        row = next((r for r in self.result["rows"] if r["feature"] == "btwChildSessions"), None)
        self.assertIsNotNone(row)
        self.assertEqual(row["kind"], "literal_unavailable")


if __name__ == "__main__":
    unittest.main()
