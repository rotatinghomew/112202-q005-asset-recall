"""损耗、周转与成本度量：按使用次数与按在营天数两套口径并列。

成本（单位：分，来自 costs.tsv）：
- renew：purchase=购置；use=每次实际外借的使用成本；wash=每次洗护；loss=丢失/补购单价。
- single：purchase=一次性盒单价（一次使用即退出）；use/wash=0；loss=purchase。

损耗四拆分（可并列解释，互不为零和替代）：
1. return_delay  归还延迟（迟还天数与确认滞后 scan-lag 分开披露）
2. wear          磨损（wear 事件；已 repaired 的不重复计损，但保留维修次数）
3. borrower_loss 外借人丢失（loss 事件，按 loss 单价计补购）
4. unidentified  不可辨（逾期未结 + 无法归属的匿名归还），只能计提风险，不能宣称丢失
"""
from __future__ import annotations

from dataclasses import dataclass

from .accounting import (
    Accounting,
    BORROWER_LOSS,
    OPEN_DUE,
    OPEN_OVERDUE,
    RECYCLED,
    RETURNED,
)
from .io import UNKNOWN


@dataclass
class RateCard:
    exposures: int
    service_days: int
    returned: int
    late_returns: int
    late_days: int
    overdue_before_loss: int          # 丢失/回收确认前已逾期的天数
    scan_lag_events: int
    scan_lag_days: int
    wear_events: int
    repairs: int
    borrower_loss: int
    recycled: int
    unidentified: int
    open_due: int
    # 金额（分）
    use_cost: int
    wash_cost: int
    borrower_loss_cost: int
    unidentified_risk_cost: int   # 风险计提，不等于已发生补购
    purchase_cost: int
    turnover_days_total: int      # 已终结外借的周转天数合计
    turnover_count: int

    @property
    def loss_per_use(self) -> float:
        """每百次外借的确认损耗（不含不可辨风险）。"""
        if not self.exposures:
            return 0.0
        return 100.0 * self.borrower_loss / self.exposures

    @property
    def risk_per_use(self) -> float:
        if not self.exposures:
            return 0.0
        return 100.0 * (self.borrower_loss + self.unidentified) / self.exposures

    @property
    def loss_per_service_day(self) -> float:
        if not self.service_days:
            return 0.0
        return 100.0 * self.borrower_loss / self.service_days

    @property
    def risk_per_service_day(self) -> float:
        if not self.service_days:
            return 0.0
        return 100.0 * (self.borrower_loss + self.unidentified) / self.service_days

    @property
    def avg_turnaround(self) -> float | None:
        if not self.turnover_count:
            return None
        return self.turnover_days_total / self.turnover_count

    @property
    def cost_per_use(self) -> float:
        if not self.exposures:
            return 0.0
        return self.total_cash_cost / self.exposures

    @property
    def cost_per_service_day(self) -> float:
        if not self.service_days:
            return 0.0
        return self.total_cash_cost / self.service_days

    @property
    def total_cash_cost(self) -> int:
        """已发生现金口径：使用 + 洗护 + 确认丢失补购（风险计提单列）。"""
        return self.use_cost + self.wash_cost + self.borrower_loss_cost

    def as_row(self) -> dict[str, object]:
        return {
            "exposures": self.exposures,
            "service_days": self.service_days,
            "returned": self.returned,
            "late_returns": self.late_returns,
            "late_days": self.late_days,
            "overdue_before_loss": self.overdue_before_loss,
            "scan_lag_events": self.scan_lag_events,
            "scan_lag_days": self.scan_lag_days,
            "wear_events": self.wear_events,
            "repairs": self.repairs,
            "borrower_loss": self.borrower_loss,
            "recycled": self.recycled,
            "unidentified": self.unidentified,
            "open_due": self.open_due,
            "use_cost": self.use_cost,
            "wash_cost": self.wash_cost,
            "borrower_loss_cost": self.borrower_loss_cost,
            "unidentified_risk_cost": self.unidentified_risk_cost,
            "purchase_cost": self.purchase_cost,
            "total_cash_cost": self.total_cash_cost,
            "loss_per_100_use": round(self.loss_per_use, 2),
            "risk_per_100_use": round(self.risk_per_use, 2),
            "loss_per_100_service_day": round(self.loss_per_service_day, 2),
            "risk_per_100_service_day": round(self.risk_per_service_day, 2),
            "avg_turnaround_days": round(self.avg_turnaround, 2)
            if self.avg_turnaround is not None else UNKNOWN,
            "cost_per_use": round(self.cost_per_use, 2),
            "cost_per_service_day": round(self.cost_per_service_day, 2),
        }


def _base_filter(box_types: tuple[str, ...] | None, routes: tuple[str, ...] | None):
    def keep(outcome, snapshot) -> bool:
        if box_types and snapshot.type not in box_types:
            return False
        if routes:
            outcome_routes = set(
                outcome.route.split(",") if outcome.route and outcome.route != UNKNOWN else []
            )
            if not outcome_routes.intersection(routes):
                return False
        return True

    return keep


def rate_card(
    accounting: Accounting,
    box_types: tuple[str, ...] | None = None,
    routes: tuple[str, ...] | None = None,
) -> RateCard:
    packet = accounting.packet
    keep = _base_filter(box_types, routes)
    selected_loans = [
        outcome
        for outcome in accounting.loan_outcomes.values()
        if keep(outcome, accounting.box_snapshots[outcome.box])
    ]
    selected_boxes = {
        outcome.box for outcome in selected_loans
    }
    # 在营天数按盒去重；只覆盖至少有一次外借的盒才与暴露口径一致
    service_days = sum(
        accounting.box_snapshots[box_id].service_days for box_id in selected_boxes
    )

    exposures = len(selected_loans)
    returned = sum(1 for o in selected_loans if o.status == RETURNED)
    late_returns = sum(1 for o in selected_loans if o.late_days > 0 and o.status == RETURNED)
    late_days = sum(o.late_days for o in selected_loans if o.status == RETURNED)
    overdue_before_loss = sum(
        o.late_days
        for o in selected_loans
        if o.status in {BORROWER_LOSS, RECYCLED}
    )
    wear_events = sum(
        accounting.box_snapshots[box_id].wears for box_id in selected_boxes
    )
    repairs = sum(
        accounting.box_snapshots[box_id].repairs for box_id in selected_boxes
    )
    borrower_loss = sum(1 for o in selected_loans if o.status == BORROWER_LOSS)
    recycled = sum(1 for o in selected_loans if o.status == RECYCLED)
    unidentified = sum(1 for o in selected_loans if o.status == OPEN_OVERDUE)
    open_due = sum(1 for o in selected_loans if o.status == OPEN_DUE)
    turnarounds = [o.turnaround_days for o in selected_loans if o.turnaround_days is not None]

    scan_lag_events = 0
    scan_lag_days = 0
    selected_moves = {o.close_move for o in selected_loans}
    for move_id, lag in accounting.scan_lags:
        if move_id in selected_moves:
            scan_lag_events += 1
            scan_lag_days += lag

    # 成本按盒型取价；混合选择时分别累加
    use_cost = wash_cost = loss_cost = risk_cost = purchase_cost = 0
    for box_id in selected_boxes:
        box_type = accounting.box_snapshots[box_id].type
        price = packet.costs[box_type]
        box_loans = [o for o in selected_loans if o.box == box_id]
        use_cost += price.use * len(box_loans)
        purchase_cost += price.purchase  # 购置沉没成本，保留结构不混进单次比较
        box_loss = sum(1 for o in box_loans if o.status == BORROWER_LOSS)
        loss_cost += price.loss * box_loss
        risk_cost += price.loss * sum(
            1 for o in box_loans if o.status == OPEN_OVERDUE
        )
    # 本资料没有洗护完成事件，wash 实际发生为 0；单价保留供下期计划
    wash_cost += 0

    return RateCard(
        exposures=exposures, service_days=service_days, returned=returned,
        late_returns=late_returns, late_days=late_days,
        overdue_before_loss=overdue_before_loss,
        scan_lag_events=scan_lag_events, scan_lag_days=scan_lag_days,
        wear_events=wear_events, repairs=repairs, borrower_loss=borrower_loss,
        recycled=recycled, unidentified=unidentified, open_due=open_due,
        use_cost=use_cost, wash_cost=wash_cost, borrower_loss_cost=loss_cost,
        unidentified_risk_cost=risk_cost, purchase_cost=purchase_cost,
        turnover_days_total=sum(turnarounds), turnover_count=len(turnarounds),
    )


def route_cards(accounting: Accounting) -> dict[str, RateCard]:
    """逐路线卡片。多路线兼容的流量在每条兼容路线上都只呈现，不重复加总。"""
    cards: dict[str, RateCard] = {}
    for route in sorted(accounting.packet.routes):
        cards[route] = rate_card(accounting, routes=(route,))
    return cards


def type_cards(accounting: Accounting) -> dict[str, RateCard]:
    return {
        box_type: rate_card(accounting, box_types=(box_type,))
        for box_type in sorted(accounting.packet.costs)
    }
