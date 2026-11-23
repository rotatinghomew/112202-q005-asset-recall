"""renew（循环盒）与 single（一次性盒）的共同适用条件比较。

规则（业务背景）：
- 只在两种包装**都有实际暴露**的适用条件层内比较（共同支持），层键取 grade ×
  weather × 出借前 condition；单层样本或某层只有一种包装时，不给耐久结论。
- 暴露只认真实外借次数；按“累计归还次数”比较会高估只在耐磨路线循环的旧盒。
- 并列显示洗护状态与回收路径的结构差异（磨损率、维修率、回收/丢失路径构成）。
- 控制后仍无法识别的差异（不可辨占比、未结外借）不得用于宣称更耐久；
  Fisher 精确检验仅作显著性提示，样本不足时结论一律标 inconclusive。
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isnan

from .accounting import Accounting, BORROWER_LOSS, OPEN_OVERDUE, RECYCLED, RETURNED
from .io import UNKNOWN

try:
    from scipy.stats import fisher_exact
except Exception:  # pragma: no cover - 依赖缺失时降级
    fisher_exact = None


@dataclass
class Stratum:
    key: tuple[str, str, str]
    renew_n: int
    single_n: int
    renew_bad: int          # 确认坏结果：外借人丢失（recycle 是一次性盒的正常退出，不计坏）
    single_bad: int
    renew_wear: int
    renew_unidentified: int
    single_unidentified: int
    p_value: float | None
    comparable: bool
    conclusion: str         # renew_better / single_better / inconclusive / no_common_support


@dataclass
class ComparisonReport:
    strata: list[Stratum]
    structural: dict[str, dict[str, object]]
    selection_notes: list[str]
    overall_conclusion: str
    identifiable: bool


def _stratum_key(outcome) -> tuple[str, str, str]:
    return outcome.grade, outcome.weather, outcome.condition


def compare(accounting: Accounting) -> ComparisonReport:
    outcomes = list(accounting.loan_outcomes.values())
    keys = sorted({_stratum_key(o) for o in outcomes})
    strata: list[Stratum] = []

    for key in keys:
        members = [o for o in outcomes if _stratum_key(o) == key]
        renew = [o for o in members if o.type == "renew"]
        single = [o for o in members if o.type == "single"]
        renew_n, single_n = len(renew), len(single)
        renew_bad = sum(1 for o in renew if o.status == BORROWER_LOSS)
        single_bad = sum(1 for o in single if o.status == BORROWER_LOSS)
        renew_unid = sum(1 for o in renew if o.status == OPEN_OVERDUE)
        single_unid = sum(1 for o in single if o.status == OPEN_OVERDUE)
        renew_boxes = {o.box for o in renew}
        renew_wear = sum(
            accounting.box_snapshots[box_id].wears for box_id in renew_boxes
        )
        common = renew_n > 0 and single_n > 0
        p_value = None
        conclusion = "no_common_support"
        if common:
            if fisher_exact is not None and (renew_bad or single_bad):
                table = [[renew_bad, renew_n - renew_bad], [single_bad, single_n - single_bad]]
                p_value = float(fisher_exact(table, alternative="two-sided").pvalue)
                if isnan(p_value):
                    p_value = None
            # 任一组存在不可辨结果，或总样本过小（<6），禁止耐久宣称
            if renew_unid or single_unid or (renew_n + single_n) < 6:
                conclusion = "inconclusive"
            elif p_value is not None and p_value >= 0.05:
                conclusion = "inconclusive"
            elif renew_bad / renew_n < single_bad / single_n:
                conclusion = "renew_better"
            elif renew_bad / renew_n > single_bad / single_n:
                conclusion = "single_better"
            else:
                conclusion = "inconclusive"
        strata.append(
            Stratum(
                key=key, renew_n=renew_n, single_n=single_n,
                renew_bad=renew_bad, single_bad=single_bad,
                renew_wear=renew_wear, renew_unidentified=renew_unid,
                single_unidentified=single_unid, p_value=p_value,
                comparable=common, conclusion=conclusion,
            )
        )

    structural = _structural_effects(accounting)
    notes = _selection_notes(accounting)
    decided = [s for s in strata if s.conclusion in {"renew_better", "single_better"}]
    unidentified_present = any(
        s.renew_unidentified or s.single_unidentified for s in strata
    )
    if not decided:
        overall = "inconclusive：共同支持层内没有可识别的耐久差异，禁止宣称更耐久"
        identifiable = False
    elif unidentified_present:
        overall = "inconclusive：存在不可辨结果，差异不能全部识别，不得据此宣称更耐久"
        identifiable = False
    else:
        winners = {s.conclusion for s in decided}
        overall = " / ".join(sorted(winners))
        identifiable = True
    return ComparisonReport(
        strata=strata, structural=structural, selection_notes=notes,
        overall_conclusion=overall, identifiable=identifiable,
    )


def _structural_effects(accounting: Accounting) -> dict[str, dict[str, object]]:
    """洗护状态与回收路径的结构影响（按包装类型）。"""
    result: dict[str, dict[str, object]] = {}
    for box_type in sorted(accounting.packet.costs):
        boxes = [s for s in accounting.box_snapshots.values() if s.type == box_type]
        outcomes = [o for o in accounting.loan_outcomes.values() if o.type == box_type]
        exposures = len(outcomes)
        returned = sum(1 for o in outcomes if o.status == RETURNED)
        loss = sum(1 for o in outcomes if o.status == BORROWER_LOSS)
        recycled = sum(1 for o in outcomes if o.status == RECYCLED)
        unid = sum(1 for o in outcomes if o.status == OPEN_OVERDUE)
        needs_wash = sum(1 for s in boxes if s.wash_state == "needs_wash")
        path_counts: dict[str, int] = {}
        for flow in accounting.flows:
            if flow.unidentified:
                continue
            snapshot = accounting.box_snapshots.get(flow.box or "")
            if snapshot is None or snapshot.type != box_type:
                continue
            label = f"{flow.from_node or UNKNOWN}->{flow.to_node or UNKNOWN}/{flow.kind}"
            path_counts[label] = path_counts.get(label, 0) + 1
        result[box_type] = {
            "exposures": exposures,
            "return_share": round(returned / exposures, 3) if exposures else UNKNOWN,
            "borrower_loss_share": round(loss / exposures, 3) if exposures else UNKNOWN,
            "recycle_share": round(recycled / exposures, 3) if exposures else UNKNOWN,
            "unidentified_share": round(unid / exposures, 3) if exposures else UNKNOWN,
            "boxes_needs_wash": needs_wash,
            "return_paths": dict(sorted(path_counts.items())),
        }
    return result


def _selection_notes(accounting: Accounting) -> list[str]:
    notes: list[str] = []
    # 选择性循环：renew 是否集中在特定路线
    renew_routes: dict[str, int] = {}
    single_routes: dict[str, int] = {}
    for outcome in accounting.loan_outcomes.values():
        target = renew_routes if outcome.type == "renew" else single_routes
        for route in (outcome.route.split(",") if outcome.route != UNKNOWN else []):
            target[route] = target.get(route, 0) + 1
    notes.append(
        f"renew 路线暴露分布: {dict(sorted(renew_routes.items()))}；"
        f"single 路线暴露分布: {dict(sorted(single_routes.items()))}"
    )
    if set(renew_routes) != set(single_routes):
        notes.append("两类包装的路线集合不完全重合，跨层外推存在选择偏差风险")
    if accounting.ambiguous_resolution:
        notes.append(
            f"存在 {accounting.resolution_count} 套合法归属，损耗结论已按归属区间保守呈现"
        )
    else:
        notes.append("封签匿名归还在本批数据上归属唯一，无跨店重复领回空间")
    return notes
