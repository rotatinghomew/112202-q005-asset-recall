"""下期新外借与错归属确认的同项目提交、决定有效性审计。

提交文件（UTF-8 Tab 分隔，沿用 ``-`` / ``?`` 约定）：
- new_loans.tsv：列 ``submission``、``route``、``from_day``、``due``、``grade``、
  ``count``、``box``（可选，指定盒号；``-`` 表示由计划员分配）。
- identity_confirmations.tsv：列 ``submission``、``move``、``box``、``loan``，
  把匿名归还在合法归属内确认到具体盒；不在任何合法归属中的确认一律拒绝。

审计回答三件事：
1. 哪项决定仍有效（计划引擎在新提交加入后仍可行）；
2. 哪些承诺未履行、缺几个盒；
3. 预算缺口是旧周期已发生损耗（沉没）还是下期仍可避免的成本。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .attribution import enumerate_resolutions
from .io import NA, UNKNOWN, Packet
from .accounting import build_accounting
from .planning import PlanningEngine, Scenario

NEW_LOAN_FIELDS = ["submission", "route", "from_day", "due", "grade", "count", "box"]
IDENTITY_FIELDS = ["submission", "move", "box", "loan"]


@dataclass(frozen=True)
class NewLoan:
    submission: str
    route: str
    from_day: int
    due: int
    grade: str
    count: int
    box: str | None


@dataclass(frozen=True)
class IdentityConfirmation:
    submission: str
    move: str
    box: str
    loan: str | None


@dataclass
class SubmissionAudit:
    accepted_new_loans: list[NewLoan]
    accepted_identities: list[IdentityConfirmation]
    rejected: list[tuple[str, str]]          # (submission, 原因)
    decisions_still_valid: list[str]
    decisions_broken: list[tuple[str, str]]  # (booking/决定, 原因)
    unmet_commitments: dict[str, int]        # booking -> 缺盒数
    sunk_gap: int                            # 旧周期已发生损耗
    avoidable_gap: int                       # 下期仍可避免成本
    ceiling: int
    scenario: Scenario


def _read_tsv(path: Path, fields: list[str]) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.reader(source, delimiter="\t", strict=True)
        header = next(reader, None)
        if header != fields:
            raise ValueError(f"{path.name}: 表头应为 {fields}")
        rows = []
        for line, values in enumerate(reader, start=2):
            if len(values) != len(fields) or any(not v.strip() for v in values):
                raise ValueError(f"{path.name}:{line}: 列数不符或存在未标记空值")
            rows.append(dict(zip(fields, values)))
        return rows


def load_submissions(folder: str | Path) -> tuple[list[NewLoan], list[IdentityConfirmation]]:
    folder = Path(folder)
    new_loans = [
        NewLoan(
            submission=row["submission"], route=row["route"],
            from_day=int(row["from_day"]), due=int(row["due"]),
            grade=row["grade"], count=int(row["count"]),
            box=None if row["box"] in (NA, UNKNOWN) else row["box"],
        )
        for row in _read_tsv(folder / "new_loans.tsv", NEW_LOAN_FIELDS)
    ]
    identities = [
        IdentityConfirmation(
            submission=row["submission"], move=row["move"], box=row["box"],
            loan=None if row["loan"] in (NA, UNKNOWN) else row["loan"],
        )
        for row in _read_tsv(
            folder / "identity_confirmations.tsv", IDENTITY_FIELDS
        )
    ]
    return new_loans, identities


def audit(
    packet: Packet,
    new_loans: list[NewLoan],
    identities: list[IdentityConfirmation],
) -> SubmissionAudit:
    resolutions = enumerate_resolutions(packet)
    legal_moves = {
        move: box
        for resolution in resolutions
        for move, box in resolution.assignments.items()
    }
    rejected: list[tuple[str, str]] = []
    accepted_ids: list[IdentityConfirmation] = []
    for item in identities:
        if item.move not in legal_moves:
            rejected.append((item.submission, f"{item.move} 不是可确认的匿名归还"))
            continue
        if legal_moves[item.move] != item.box:
            rejected.append(
                (item.submission,
                 f"{item.move} 的合法归属盒为 {legal_moves[item.move]}，不能确认为 {item.box}")
            )
            continue
        accepted_ids.append(item)

    # 新外借校验：路线存在、grade 合法、窗口非倒序
    accepted_loans: list[NewLoan] = []
    for item in new_loans:
        if item.route not in packet.routes:
            rejected.append((item.submission, f"未知路线 {item.route}"))
            continue
        if item.grade not in {"heavy", "light"}:
            rejected.append((item.submission, f"未知 grade {item.grade}"))
            continue
        if item.due < item.from_day or item.count <= 0:
            rejected.append((item.submission, "借还窗口或数量非法"))
            continue
        accepted_loans.append(item)

    accounting = build_accounting(packet)
    engine = PlanningEngine(packet, accounting)
    scenario = engine.run_scenario(
        "提交后复核", tuple(sorted(packet.routes))
    )

    valid: list[str] = []
    broken: list[tuple[str, str]] = []
    unmet: dict[str, int] = {}
    for result in scenario.results:
        if result.feasible:
            valid.append(result.booking)
        else:
            broken.append((result.booking, result.reason))
            unmet[result.booking] = result.count - len(result.assigned_boxes)

    # 新外借叠加后的额外需求：指定盒号逐盒校验物理可用性，其余按剩余产能校验
    spare = _spare_capacity(packet, engine, scenario)
    pool_by_box = {state.box: state for state in engine.box_pool()}
    used_by_submission: set[str] = set()
    for item in accepted_loans:
        if item.box is not None:
            state = pool_by_box.get(item.box)
            if state is None:
                broken.append(
                    (item.submission, f"盒 {item.box} 不在循环盒池（丢失/回收/非循环），不能指定出借")
                )
                unmet[item.submission] = item.count
                continue
            if state.grade != item.grade:
                broken.append(
                    (item.submission,
                     f"盒 {item.box} grade={state.grade} 与要求 {item.grade} 不符")
                )
                unmet[item.submission] = item.count
                continue
            if state.note.startswith("仍在借用期"):
                broken.append((item.submission, f"盒 {item.box} 仍在借用期，截止日不可出借"))
                unmet[item.submission] = item.count
                continue
            if item.box in used_by_submission:
                broken.append((item.submission, f"盒 {item.box} 被同一批提交重复指定"))
                unmet[item.submission] = item.count
                continue
            used_by_submission.add(item.box)
            valid.append(item.submission)
            continue
        if spare >= item.count:
            valid.append(item.submission)
            spare -= item.count
        else:
            broken.append((item.submission, f"洗护/可出借余量不足，缺 {item.count - spare} 盒"))
            unmet[item.submission] = item.count - spare
            spare = 0

    ceiling = min(row.ceiling for row in packet.costs.values())
    gap = max(0, scenario.next_period_cost - ceiling)
    return SubmissionAudit(
        accepted_new_loans=accepted_loans, accepted_identities=accepted_ids,
        rejected=rejected, decisions_still_valid=valid, decisions_broken=broken,
        unmet_commitments=unmet, sunk_gap=scenario.prior_period_sunk,
        avoidable_gap=gap, ceiling=ceiling, scenario=scenario,
    )


def _spare_capacity(packet: Packet, engine: PlanningEngine, scenario: Scenario) -> int:
    """剩余可承载新外借的盒数：洗护日历剩余槽位与在库循环盒数的较小值。

    只统计 grade 无关的通用下界；heavy 专用槽的额外约束在原因中说明。
    """
    spare_slots = 0
    for (node, day), cell in engine.wash_calendar.items():
        spare_slots += max(0, cell["slots"] - cell["used"])
    pool = engine.box_pool()
    on_shelf = sum(
        1 for state in pool if state.note and not state.note.startswith("仍在借用期")
    )
    reserved = {box for result in scenario.results for box in result.assigned_boxes}
    free_boxes = sum(
        1 for state in pool if state.box not in reserved
        and state.note and not state.note.startswith("仍在借用期")
    )
    return min(spare_slots, free_boxes)
