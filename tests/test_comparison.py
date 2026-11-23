"""共同支持比较测试：共同层、选择偏差、不可辨差异禁止耐久宣称。"""
import unittest
from pathlib import Path

from circulation.accounting import build_accounting
from circulation.comparison import compare
from circulation.io import load_packet

ROOT = Path(__file__).resolve().parents[1]


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.packet = load_packet(ROOT / "data")
        self.report = compare(build_accounting(self.packet))

    def test_strata_use_common_support_keys(self):
        keys = {s.key for s in self.report.strata}
        # 只有一种包装出现的层标记为不可比
        no_support = [s for s in self.report.strata if not s.comparable]
        self.assertTrue(no_support)
        for stratum in no_support:
            self.assertEqual(stratum.conclusion, "no_common_support")
        # 共同层必须两种包装都有暴露
        for stratum in self.report.strata:
            if stratum.comparable:
                self.assertGreater(stratum.renew_n, 0)
                self.assertGreater(stratum.single_n, 0)

    def test_unidentified_results_block_durability_claim(self):
        # light/wet/normal 层 single 有不可辨结果，即便 renew 出现一次丢失也不能下结论
        wet = next(s for s in self.report.strata if s.key == ("light", "wet", "normal"))
        self.assertEqual(wet.conclusion, "inconclusive")
        self.assertFalse(self.report.identifiable)
        self.assertIn("禁止宣称更耐久", self.report.overall_conclusion)

    def test_structural_effects_show_wash_and_paths(self):
        structural = self.report.structural
        self.assertIn("renew", structural)
        self.assertIn("single", structural)
        # renew 有回收后待洗盒与跨店路径；single 走 recycle 退出
        self.assertGreater(structural["renew"]["boxes_needs_wash"], 0)
        self.assertGreater(structural["single"]["recycle_share"], 0)
        self.assertTrue(structural["renew"]["return_paths"])

    def test_selection_note_flags_route_overlap(self):
        note = " ".join(self.report.selection_notes)
        self.assertIn("R1", note)
        self.assertIn("R3", note)


if __name__ == "__main__":
    unittest.main()
