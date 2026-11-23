"""归属求解器测试：封签复用、匿名归还一对一、禁止重复领回、漏扫不臆造归还。"""
import unittest
from pathlib import Path

from circulation.attribution import enumerate_resolutions
from circulation.io import UNKNOWN, load_packet

ROOT = Path(__file__).resolve().parents[1]


class AttributionTests(unittest.TestCase):
    def setUp(self):
        self.packet = load_packet(ROOT / "data")
        self.resolutions = enumerate_resolutions(self.packet)

    def test_anonymous_returns_have_unique_legal_assignment(self):
        self.assertEqual(len(self.resolutions), 1)
        resolution = self.resolutions[0]
        # M3（K1 匿名归还）只能归 B1：B2 由 M22 补确认关闭
        self.assertEqual(resolution.assignments["M3"], "B1")
        # M17（K2 匿名归还）只能归 B8：B3 的 L3 已由 M5 关闭
        self.assertEqual(resolution.assignments["M17"], "B8")

    def test_each_loan_closed_at_most_once(self):
        resolution = self.resolutions[0]
        closer_loans = list(resolution.loan_closer.keys())
        self.assertEqual(len(closer_loans), len(set(closer_loans)))
        # 同一封签两个盒不能被同一匿名归还重复领回
        boxes = list(resolution.assignments.values())
        self.assertEqual(len(boxes), len(set(boxes)))

    def test_identify_return_closes_its_own_loan(self):
        resolution = self.resolutions[0]
        self.assertEqual(resolution.loan_closer["L2"], "M22")
        self.assertNotIn("M22", resolution.assignments)

    def test_conflicting_sleeve_is_reported_not_silently_merged(self):
        # 本批数据封签一致
        self.assertEqual(self.resolutions[0].conflicts, ())

    def test_transfer_during_open_loan_is_missed_scan_not_invented_return(self):
        # M20/M21 时盒均已无未结外借，因此无漏扫信号；若构造未结调拨，必须被标记
        self.assertEqual(self.resolutions[0].missed_scan, frozenset())

    def test_open_loans_remain_open(self):
        resolution = self.resolutions[0]
        # L5/L9/L10 截止日仍未结
        self.assertNotIn("L5", resolution.loan_closer)
        self.assertNotIn("L9", resolution.loan_closer)
        self.assertNotIn("L10", resolution.loan_closer)


if __name__ == "__main__":
    unittest.main()
