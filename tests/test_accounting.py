"""核算口径测试：暴露只认真实外借、四类损耗、双口径、漏扫/在借/确认滞后分列。"""
import unittest
from pathlib import Path

from circulation.accounting import (
    BORROWER_LOSS,
    OPEN_DUE,
    OPEN_OVERDUE,
    RETURNED,
    build_accounting,
)
from circulation.io import load_packet
from circulation.metrics import rate_card, route_cards, type_cards

ROOT = Path(__file__).resolve().parents[1]


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.packet = load_packet(ROOT / "data")
        self.accounting = build_accounting(self.packet)

    def test_cutoff_includes_received_confirmation_day(self):
        # M21/M22 的 received=12 晚于 day，截止日必须覆盖到 12
        self.assertEqual(self.accounting.cutoff_day, 12)

    def test_exposure_counts_only_actual_loans(self):
        total = sum(s.loans for s in self.accounting.box_snapshots.values())
        self.assertEqual(total, 10)  # 恰好 10 条 loan 事件
        # 归还/调拨/维修/洗护不贡献暴露
        self.assertEqual(
            self.accounting.ranges["exposures"], (10.0, 10.0)
        )

    def test_loan_outcome_four_way_split(self):
        outcomes = self.accounting.loan_outcomes
        self.assertEqual(outcomes["L4"].status, BORROWER_LOSS)   # B4 外借人丢失
        self.assertEqual(outcomes["L1"].status, RETURNED)
        self.assertEqual(outcomes["L2"].status, RETURNED)        # 补确认也算归还
        # 截止日 12：L5(due10) 逾期未结=不可辨；L9/L10(due15) 仍在借用期
        self.assertEqual(outcomes["L5"].status, OPEN_OVERDUE)
        self.assertEqual(outcomes["L9"].status, OPEN_DUE)
        self.assertEqual(outcomes["L10"].status, OPEN_DUE)

    def test_scan_lag_separate_from_late_return(self):
        lags = dict(self.accounting.scan_lags)
        # M22 返仓确认滞后 7 天；M21 调拨接收滞后 5 天
        self.assertEqual(lags["M22"], 7)
        self.assertEqual(lags["M21"], 5)
        # 没有任何迟还（late_days 全部为 0），与确认滞后严格分开
        self.assertEqual(self.accounting.ranges["late_days"], (0.0, 0.0))

    def test_unknown_is_not_treated_as_zero(self):
        # B5/B9/B10 在借，位置未知必须保留 '?'
        for box_id in ("B5", "B9", "B10"):
            self.assertEqual(self.accounting.box_snapshots[box_id].location, "?")

    def test_box_statuses_reflect_physical_state(self):
        snap = self.accounting.box_snapshots
        self.assertEqual(snap["B4"].status, "lost")
        self.assertEqual(snap["B6"].status, "recycled")
        self.assertEqual(snap["B5"].status, "in_circulation")
        # 在库盒无洗护完成记录，不能直接当可出借
        for box_id in ("B1", "B2", "B3", "B8"):
            self.assertEqual(snap[box_id].wash_state, "needs_wash")

    def test_dual_basis_metrics(self):
        cards = type_cards(self.accounting)
        renew, single = cards["renew"], cards["single"]
        self.assertEqual(renew.exposures, 6)
        self.assertEqual(single.exposures, 4)
        # 按次数与按在营天数两套口径都给出
        self.assertGreater(renew.cost_per_use, 0)
        self.assertGreater(renew.cost_per_service_day, 0)
        self.assertEqual(renew.service_days, 59)
        # R3 承担唯一确认丢失，风险率最高
        r3 = route_cards(self.accounting)["R3"]
        self.assertEqual(r3.borrower_loss, 1)
        self.assertEqual(r3.unidentified, 1)
        self.assertGreater(r3.risk_per_use, 50)

    def test_unidentified_risk_separate_from_confirmed_loss(self):
        card = rate_card(self.accounting)
        self.assertEqual(card.borrower_loss, 1)       # 仅 B4
        self.assertEqual(card.unidentified, 1)       # 仅 L5/B5
        self.assertGreater(card.unidentified_risk_cost, 0)
        # L4 丢失前已逾期 1 天，归入归还延迟维度，与确认丢失并列
        self.assertEqual(card.overdue_before_loss, 1)
        # 风险计提不计入已发现金成本
        self.assertNotEqual(
            card.unidentified_risk_cost, card.total_cash_cost
        )

    def test_cross_store_flow_marks_ambiguity(self):
        m3 = next(f for f in self.accounting.flows if f.move == "M3")
        self.assertTrue(m3.ambiguous)       # R1,R2 多路线兼容
        self.assertEqual(m3.box, "B1")      # 但盒归属唯一
        self.assertFalse(m3.unidentified)

    def test_transfer_receiver_unknown_not_confused_with_recorded_node(self):
        # 调拨行 node 是调出点；接收点未显式记录，必须标 '?'，不能假装已知
        m21 = next(f for f in self.accounting.flows if f.move == "M21")
        self.assertEqual(m21.kind, "transfer")
        self.assertEqual(m21.from_node, "S")
        self.assertEqual(m21.to_node, "?")


if __name__ == "__main__":
    unittest.main()
