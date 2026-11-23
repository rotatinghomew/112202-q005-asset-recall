"""下期计划测试：借还窗口、联合洗护排程、专用工位、预算与逐盒可出借口径。"""
import unittest
from pathlib import Path

from circulation.accounting import build_accounting
from circulation.io import load_packet
from circulation.planning import PlanningEngine

ROOT = Path(__file__).resolve().parents[1]


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.packet = load_packet(ROOT / "data")
        self.accounting = build_accounting(self.packet)
        self.engine = PlanningEngine(self.packet, self.accounting)

    def test_pool_uses_physical_boxes_not_average_inventory(self):
        pool = {p.box: p for p in self.engine.box_pool()}
        # 丢失/回收盒不在池内；在借盒被冻结
        self.assertNotIn("B4", pool)
        self.assertNotIn("B6", pool)
        self.assertTrue(pool["B9"].available_day >= 10**6)
        self.assertIn("仍在借用期", pool["B9"].note)
        # 在库盒必须先洗护
        self.assertEqual(pool["B2"].wash_state, "needs_wash")

    def test_signed_heavy_commitments_compete_for_shared_calendar(self):
        # O1(R1,S,d13) 与 O2(R2,W,d13) 都是 heavy；day12 N 仅有 1 个 special 槽
        scenarios = {s.name: s for s in self.engine.standard_scenarios()}
        s3 = scenarios["S3_仅兑现signed承诺"]
        unmet = set(s3.unmet)
        # 联合排程下两个 day13 heavy 承诺无法同时兑现（只有 B2 能在 N day12 入洗）
        self.assertIn("O1", unmet)
        self.assertIn("O2", unmet)

    def test_heavy_special_slot_is_part_of_total(self):
        # N day13 slots=1 但 special_slots=0，heavy 在该日不可用，只能顺延到 day15
        day, _ = self.engine._earliest_wash("N", "heavy", 13)
        self.assertEqual(day, 15)
        # light 当天即可
        day, _ = self.engine._earliest_wash("N", "light", 13)
        self.assertEqual(day, 13)
        # H day12 slots=0：当天无产能，最早顺延到 day13
        self.assertEqual(self.engine._earliest_wash("H", "light", 12)[0], 13)
        self.assertEqual(self.engine._earliest_wash("H", "heavy", 12)[0], 13)

    def test_unknown_pair_uses_conservative_lead_not_optimistic(self):
        # S->N 未观测，取保守前置；同节点前置为 0
        self.assertEqual(self.engine.lead_between("N", "N"), 0)
        self.assertEqual(self.engine.lead_between("S", "N"), self.engine.unknown_pair_lead)
        self.assertEqual(self.engine.lead_between("?", "N"), self.engine.unknown_pair_lead)

    def test_observed_transfer_overrides_expected(self):
        # M21 实测 W->N=5，优先于预期差值
        self.assertEqual(self.engine.transfer_leads[("W", "N")], 5)

    def test_sunk_loss_kept_separate_from_avoidable_next_period_cost(self):
        for scenario in self.engine.standard_scenarios():
            # B4 确认丢失 3900 分是旧周期沉没成本
            self.assertEqual(scenario.prior_period_sunk, 3900)
            self.assertGreaterEqual(scenario.next_period_cost, 0)
            # 预算约束针对下期可避免成本
            self.assertIsInstance(scenario.within_budget, bool)

    def test_stop_lending_keeps_return_point_open(self):
        scenarios = {s.name: s for s in self.engine.standard_scenarios()}
        s1 = scenarios["S1_只停借R3不关回收"]
        self.assertIn("R3", s1.keep_returns)
        self.assertNotIn("R3", s1.active_routes)
        # R3 的 O3 不再进入排程
        bookings = {r.booking for r in s1.results}
        self.assertNotIn("O3", bookings)

    def test_reroute_scenario_moves_commitment_to_alternative_route(self):
        scenarios = {s.name: s for s in self.engine.standard_scenarios()}
        s2 = scenarios["S2_停R3且R4替代调拨"]
        o3 = next(r for r in s2.results if r.booking == "O3")
        # O3 被配调到 R4（H 洗护节点），与 S1 的直接停借形成结构差异
        self.assertEqual(o3.route, "R4")
        self.assertIn("配调替代", o3.reason)

    def test_capacity_full_load_is_flagged(self):
        # 手动占满 N day12 的唯一槽位后应触发满载风险
        self.engine._reserve_wash("N", 12, "heavy", "BX")
        usage = self.engine._collect_wash_usage()
        risks = self.engine._capacity_risks(usage)
        self.assertTrue(any("N day12" in risk for risk in risks))

    def test_stopping_route_keeps_other_stores_return_flow_open(self):
        # 停 R3 但保留其回收点 N：历史 R3 回收流的节点仍开放，不形成断流
        scenario = self.engine.run_scenario(
            "停R3", tuple(r for r in self.packet.routes if r != "R3"),
            keep_returns=("R3",),
        )
        for dependency in scenario.return_dependencies:
            if dependency["stopped_route"] == "R3":
                self.assertTrue(dependency["node_still_open"])


if __name__ == "__main__":
    unittest.main()
