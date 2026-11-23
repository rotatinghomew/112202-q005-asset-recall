"""下期路线可行性与方案比选。

硬约束（业务背景）：
1. 继续走一条路线，必须能兑现现有借还窗口（signed 承诺优先，offer 可选）。
2. 共用洗护节点的路线联合排程，不能每条路线各取自己的最乐观回收时间；
   heavy 盒占用的 special_slots 包含在总 slots 内。
3. 损耗与补购成本必须落在 ceiling 预算区间内。
4. 停用路线只停借、不关回收点；回收盒能否调给别处取决于洗护、磨损状态与到期承诺。
5. 可出借数量按逐盒物理状态（位置/洗护/在借/丢失）计算，禁止用平均库存替代。

调拨前置（参数化、全部假设可在报告中查看）：默认取路线 expected_days 的
“全流程回收天数 - 在借天数”作为同体系节点间调拨/洗护前置；可由外部提交覆盖。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .accounting import Accounting
from .io import Commitment, Packet, UNKNOWN


@dataclass
class BoxPlanState:
    box: str
    type: str
    grade: str
    location: str | None
    wash_state: str           # clean / needs_wash / unknown
    available_day: int        # 最早可到达任意出借节点的天数（unknown 按保守无穷）
    note: str = ""


@dataclass
class WashUse:
    node: str
    day: int
    boxes: list[str]
    slots_used: int
    special_used: int


@dataclass
class CommitmentResult:
    booking: str
    route: str
    grade: str
    count: int
    state: str
    feasible: bool
    assigned_boxes: list[str]
    wash_plan: list[WashUse]
    reason: str = ""


@dataclass
class Scenario:
    name: str
    active_routes: tuple[str, ...]
    keep_returns: tuple[str, ...]          # 停借但保留回收的路线
    selected_bookings: tuple[str, ...]
    results: list[CommitmentResult]
    wash_usage: list[WashUse]
    next_period_cost: int                  # 下期可避免成本（使用+洗护+补购）
    prior_period_sunk: int                 # 旧周期已发生损耗（不可避免）
    within_budget: bool
    idle_lendable: list[str]
    unmet: list[str]
    exposure_change: int
    attribution_exposure: int              # 归属不确定下被冻结的盒数
    loss_risk: int
    capacity_risk: list[str]
    return_dependencies: list[dict[str, object]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def feasible(self) -> bool:
        return not self.unmet and self.within_budget


class PlanningEngine:
    def __init__(
        self,
        packet: Packet,
        accounting: Accounting,
        transfer_leads: dict[tuple[str, str], int] | None = None,
        unknown_pair_lead: int | None = None,
    ):
        self.packet = packet
        self.accounting = accounting
        self.transfer_leads = transfer_leads or self._default_transfer_leads()
        # 未观测点对的保守前置：取已知前置上限；接班人可用实测覆盖，禁止默认乐观
        self.unknown_pair_lead = (
            unknown_pair_lead
            if unknown_pair_lead is not None
            else max(self.transfer_leads.values(), default=3)
        )
        self.wash_calendar = self._wash_calendar()

    # ---------- 前置参数 ----------
    def _default_transfer_leads(self) -> dict[tuple[str, str], int]:
        """调拨/洗护前置的默认参数（按节点对分别取值，禁止跨点对套用）。

        - 路线 expected_days 的“全流程 - 在借”给出该路线回收点到出借点的预期前置；
        - 资料内可观测的实际调拨（M21：day7 于 W 调出、day12 于 N 接收）校准 W->N=5；
        - 未观测点对不向其它点对借用，统一落到保守上限并在报告中披露。
        所有参数可由接班人用实测记录覆盖；共用洗护节点的路线在同一份日历上联合排程，
        不允许各路线私取最乐观回收时间。
        """
        leads: dict[tuple[str, str], int] = {}
        for route in self.packet.routes.values():
            if len(route.expected_days) >= 2:
                lead = route.expected_days[1] - route.expected_days[0]
                pair = (route.return_node, route.lend)
                if pair[0] != pair[1]:
                    leads[pair] = lead
        leads[("W", "N")] = 5  # M21 实测，优先于任何预期值
        return leads

    def lead_between(self, source: str | None, target: str) -> int:
        if source is None or source == UNKNOWN:
            # 来源不明不能按最乐观估计：用未观测点对的保守前置
            return self.unknown_pair_lead
        if source == target:
            return 0
        if (source, target) in self.transfer_leads:
            return self.transfer_leads[(source, target)]
        # 未观测点对取保守前置，禁止默认乐观
        return self.unknown_pair_lead

    def _wash_calendar(self) -> dict[tuple[str, int], dict[str, int]]:
        calendar: dict[tuple[str, int], dict[str, int]] = {}
        for slot in self.packet.wash:
            calendar[(slot.node, slot.day)] = {
                "slots": slot.slots,
                "special": slot.special_slots,
                "used": 0,
                "special_used": 0,
            }
        return calendar

    # ---------- 物理可出借盒 ----------
    def box_pool(self) -> list[BoxPlanState]:
        pool: list[BoxPlanState] = []
        for snapshot in self.accounting.box_snapshots.values():
            if snapshot.type != "renew":
                continue  # 一次性盒不进入循环盒池
            if snapshot.status in {"lost", "recycled"}:
                continue
            if snapshot.status == "in_circulation":
                pool.append(
                    BoxPlanState(
                        snapshot.box, snapshot.type, snapshot.grade,
                        snapshot.location, snapshot.wash_state,
                        available_day=10**6,
                        note="仍在借用期，不能计入下期可出借",
                    )
                )
                continue
            # 在库但无洗护完成记录：需要先洗护
            pool.append(
                BoxPlanState(
                    snapshot.box, snapshot.type, snapshot.grade,
                    snapshot.location, snapshot.wash_state,
                    available_day=10**6,
                    note=snapshot.note or "回收后未见洗护完成记录，须先洗护",
                )
            )
        return pool

    def _earliest_wash(
        self, node: str, grade: str, not_before: int
    ) -> tuple[int | None, str]:
        """在指定节点找最早可用洗护日；heavy 必须有 special 余量。"""
        days = sorted(day for (n, day) in self.wash_calendar if n == node)
        for day in days:
            if day < not_before:
                continue
            cell = self.wash_calendar[(node, day)]
            if cell["used"] >= cell["slots"]:
                continue
            if grade == "heavy" and cell["special_used"] >= cell["special"]:
                continue
            if cell["special"] == 0 and grade == "heavy":
                continue
            return day, ""
        return None, f"节点 {node} 在可排程窗口内无{(' heavy 专用' if grade == 'heavy' else '')}洗护余量"

    def _reserve_wash(self, node: str, day: int, grade: str, box: str) -> WashUse:
        cell = self.wash_calendar[(node, day)]
        cell["used"] += 1
        if grade == "heavy":
            cell["special_used"] += 1
        return WashUse(node=node, day=day, boxes=[box], slots_used=1,
                       special_used=1 if grade == "heavy" else 0)

    # ---------- 单承诺可行性 ----------
    def evaluate_commitment(
        self,
        commitment: Commitment,
        pool: list[BoxPlanState],
        reserved: set[str],
    ) -> CommitmentResult:
        route = self.packet.routes[commitment.route]
        options: list[tuple[str, str, int, int]] = []  # (盒, 洗护节点, 洗护日, 到达出借日)
        reasons: list[str] = []

        for candidate in pool:
            if candidate.box in reserved or candidate.grade != commitment.grade:
                continue
            for node in route.allowed_wash:
                lead_to_lend = self.lead_between(node, route.lend)
                travel_to_wash = self.lead_between(candidate.location, node)
                not_before = self.accounting.cutoff_day + travel_to_wash
                wash_day, reason = self._earliest_wash(node, candidate.grade, not_before)
                if wash_day is None:
                    reasons.append(f"{candidate.box}@{node}: {reason}")
                    continue
                ready_at_lend = wash_day + lead_to_lend
                if ready_at_lend <= commitment.from_day:
                    options.append((candidate.box, node, wash_day, ready_at_lend))

        # 同一盒可能在多个节点可行：每盒只保留最早到达的方案，避免一盒被重复计数
        best_per_box: dict[str, tuple[str, int, int]] = {}
        for box_id, node, wash_day, ready in options:
            if box_id not in best_per_box or ready < best_per_box[box_id][2]:
                best_per_box[box_id] = (node, wash_day, ready)

        if len(best_per_box) < commitment.count:
            detail = "; ".join(sorted(set(reasons))[:5]) or "无同 grade 可洗护候选"
            return CommitmentResult(
                booking=commitment.booking, route=commitment.route,
                grade=commitment.grade, count=commitment.count,
                state=commitment.state, feasible=False, assigned_boxes=[],
                wash_plan=[], reason=f"可兑现盒不足（需 {commitment.count}）: {detail}",
            )

        ranked = sorted(best_per_box.items(), key=lambda item: (item[1][2], item[0]))
        chosen = ranked[: commitment.count]
        wash_uses: list[WashUse] = []
        assigned: list[str] = []
        for box_id, (node, wash_day, _ready) in chosen:
            # 选定后才真正占用日历；若占用瞬间发现冲突（理论上不会），显式失败
            day, reason = self._earliest_wash(node, commitment.grade, wash_day)
            if day != wash_day:
                return CommitmentResult(
                    booking=commitment.booking, route=commitment.route,
                    grade=commitment.grade, count=commitment.count,
                    state=commitment.state, feasible=False, assigned_boxes=[],
                    wash_plan=[], reason=f"洗护产能在占用时变化: {reason}",
                )
            wash_uses.append(self._reserve_wash(node, wash_day, commitment.grade, box_id))
            assigned.append(box_id)
            reserved.add(box_id)
        return CommitmentResult(
            booking=commitment.booking, route=commitment.route,
            grade=commitment.grade, count=commitment.count,
            state=commitment.state, feasible=True, assigned_boxes=assigned,
            wash_plan=wash_uses,
        )

    # ---------- 场景比选 ----------
    def run_scenario(
        self,
        name: str,
        active_routes: tuple[str, ...],
        selected_bookings: tuple[str, ...] | None = None,
        keep_returns: tuple[str, ...] = (),
        reroutes: dict[str, str] | None = None,
    ) -> Scenario:
        """执行一个比选场景。

        reroutes: booking -> 替代路线，用于“配调替代路线”方案；被改道的承诺保留
        原 booking 号但在 reason/notes 中显式标注其回收与洗护路径发生了结构变化。
        """
        reroutes = reroutes or {}
        # 每次场景独立日历
        self.wash_calendar = self._wash_calendar()
        pool = self.box_pool()
        reserved: set[str] = set()
        commitments = [
            c for c in self.packet.commitments
            if c.route in active_routes
            and (selected_bookings is None or c.booking in selected_bookings)
        ]
        # 配调替代：把指定承诺改道到替代路线（即使原路线已停借也纳入）
        for booking, alt_route in reroutes.items():
            source = next((c for c in self.packet.commitments if c.booking == booking), None)
            if source is not None and alt_route in self.packet.routes:
                commitments.append(
                    replace(source, route=alt_route)
                )
        # signed 必须纳入；offer 按选择纳入
        signed = [c for c in commitments if c.state == "signed"]
        offers = [c for c in commitments if c.state == "offer"]
        ordered = sorted(signed, key=lambda c: (c.from_day, c.booking)) + \
                  sorted(offers, key=lambda c: (c.from_day, c.booking))

        results: list[CommitmentResult] = []
        unmet: list[str] = []
        for commitment in ordered:
            result = self.evaluate_commitment(commitment, pool, reserved)
            if commitment.booking in reroutes:
                # 标注结构变化，避免与原路线结果混淆
                result = replace(
                    result,
                    reason=(result.reason or "-")
                    + f" [已由 {self._booking_route(commitment.booking)} 配调替代至 {commitment.route}]",
                )
            results.append(result)
            if not result.feasible:
                unmet.append(commitment.booking)

        wash_usage = self._collect_wash_usage()
        next_cost, sunk, risk = self._costs(results, active_routes, keep_returns)
        ceiling = min(row.ceiling for row in self.packet.costs.values())
        capacity_risk = self._capacity_risks(wash_usage)
        dependencies = self._return_dependencies(active_routes, keep_returns)
        idle = [p.box for p in pool if p.box not in reserved and p.available_day < 10**6]
        # needs_wash 的在库盒不算立即可借，但报告其物理存在
        notes: list[str] = []
        for state in pool:
            if state.box not in reserved and state.note:
                notes.append(f"{state.box}: {state.note}")

        exposure_change = sum(r.count for r in results if r.feasible)
        frozen = sum(
            1 for p in pool if p.available_day >= 10**6 and p.note.startswith("仍在借用期")
        )
        return Scenario(
            name=name, active_routes=active_routes, keep_returns=keep_returns,
            selected_bookings=tuple(c.booking for c in ordered),
            results=results, wash_usage=wash_usage,
            next_period_cost=next_cost, prior_period_sunk=sunk,
            within_budget=next_cost <= ceiling,
            idle_lendable=idle, unmet=unmet, exposure_change=exposure_change,
            attribution_exposure=frozen, loss_risk=risk,
            capacity_risk=capacity_risk, return_dependencies=dependencies,
            notes=notes,
        )

    def _return_dependencies(
        self,
        active_routes: tuple[str, ...],
        keep_returns: tuple[str, ...],
    ) -> list[dict[str, object]]:
        """停用路线是否带走其它门店仍依赖的回收流。

        逐盒检查：该盒历史上在被停路线出借、却通过仍开放的回收点回流；若回收点随
        路线一起关掉（不在 active_routes 的 return 节点、也不在 keep_returns），
        则标记为断流风险。只停借不关回收点时该风险为零。
        """
        open_return_nodes = {
            self.packet.routes[r].return_node for r in active_routes
        } | {
            self.packet.routes[r].return_node for r in keep_returns if r in self.packet.routes
        }
        stopped = [r for r in self.packet.routes if r not in active_routes]
        findings: list[dict[str, object]] = []
        for route_id in stopped:
            for flow in self.accounting.flows:
                if route_id not in flow.routes or flow.unidentified:
                    continue
                if flow.kind not in {"return", "identify_return"}:
                    continue
                node = flow.to_node
                findings.append({
                    "stopped_route": route_id,
                    "move": flow.move,
                    "box": flow.box,
                    "return_node": node,
                    "node_still_open": node in open_return_nodes,
                    "ambiguous_routes": flow.ambiguous,
                })
        return findings

    def _collect_wash_usage(self) -> list[WashUse]:
        usage: list[WashUse] = []
        for (node, day), cell in sorted(self.wash_calendar.items()):
            if cell["used"]:
                usage.append(
                    WashUse(node=node, day=day, boxes=[], slots_used=cell["used"],
                            special_used=cell["special_used"])
                )
        return usage

    def _capacity_risks(self, usage: list[WashUse]) -> list[str]:
        risks: list[str] = []
        for use in usage:
            cell = self.wash_calendar[(use.node, use.day)]
            if cell["used"] == cell["slots"]:
                risks.append(f"{use.node} day{use.day} 洗护满载({cell['used']}/{cell['slots']})")
        return risks

    def _costs(
        self,
        results: list[CommitmentResult],
        active_routes: tuple[str, ...],
        keep_returns: tuple[str, ...],
    ) -> tuple[int, int, int]:
        renew_price = self.packet.costs["renew"]
        # 下期可避免成本：承诺外借的使用成本 + 已排洗护成本 + 为未识别风险预留的补购
        exposures = sum(r.count for r in results if r.feasible)
        washes = sum(len(r.wash_plan) for r in results if r.feasible)
        use_cost = renew_price.use * exposures
        wash_cost = renew_price.wash * washes
        # 风险计提：旧周期逾期未结盒按补购单价（仍可避免：找回即冲回）
        risk_boxes = sum(
            1 for o in self.accounting.loan_outcomes.values()
            if o.status == "open_overdue" and o.type == "renew"
        )
        risk_reserve = renew_price.loss * risk_boxes
        # 旧周期已确认丢失：沉没、不可避免
        sunk = sum(
            self.packet.costs[o.type].loss
            for o in self.accounting.loan_outcomes.values()
            if o.status == "borrower_loss"
        )
        return use_cost + wash_cost + risk_reserve, sunk, risk_reserve

    def _booking_route(self, booking: str) -> str:
        source = next(
            (c for c in self.packet.commitments if c.booking == booking), None
        )
        return source.route if source else "?"

    def standard_scenarios(self) -> list[Scenario]:
        all_routes = tuple(sorted(self.packet.routes))
        non_r3 = tuple(r for r in all_routes if r != "R3")
        non_r3_bookings = tuple(
            c.booking for c in self.packet.commitments if c.route != "R3"
        )
        scenarios = [
            self.run_scenario("S0_全量(signed+全部offer)", all_routes),
            self.run_scenario(
                "S1_只停借R3不关回收",
                non_r3,
                keep_returns=("R3",),
            ),
            self.run_scenario(
                "S2_停R3且R4替代调拨",
                non_r3,
                selected_bookings=non_r3_bookings,
                keep_returns=("R3",),
                # R3 的 O3（light×2）改由 R4 承接：回收仍走 N，洗护改走 H 专用节点
                reroutes={"O3": "R4"},
            ),
            self.run_scenario(
                "S3_仅兑现signed承诺",
                all_routes,
                selected_bookings=tuple(
                    c.booking for c in self.packet.commitments if c.state == "signed"
                ),
            ),
        ]
        return scenarios
