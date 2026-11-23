"""以实际外借为暴露的核算层。

口径要点：
- 盒子的一次**实际外借**（event=loan）才贡献一次暴露；归还/修理/调拨/洗护不计数。
- 清点漏扫（未结外借期间出现调拨）、仍在借用期、返仓确认不可用（identify_return 的
  接收滞后）分别记账，互不混同。
- 损耗拆成四类：归还延迟、磨损（wear）、外借人丢失（loss）、不可辨（逾期未结或
  匿名归还无法归属），四类可并列解释，绝不把未取得值当零。
- 同时给出按使用次数与按在营天数（cutoff - dispatch）的损耗与周转成本。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .attribution import Resolution, enumerate_resolutions
from .io import Move, Packet, UNKNOWN

# 贷款终结分类
RETURNED = "returned"            # 正常归还（含补确认）
BORROWER_LOSS = "borrower_loss"  # 外借人丢失（loss）
RECYCLED = "recycled"            # 回收处置（recycle，一次性盒退出）
UNIDENTIFIED = "unidentified"    # 逾期未结 / 无法归属的匿名归还
# 仍开放的两类
OPEN_DUE = "open_due"            # 仍在借用期（day <= due）
OPEN_OVERDUE = "open_overdue"    # 已逾承诺日仍未结，计入不可辨风险

LOSS_BUCKETS = ("return_delay", "wear", "borrower_loss", "unidentified")


@dataclass
class LoanOutcome:
    loan: str
    box: str
    type: str
    grade: str
    weather: str
    condition: str
    route: str
    loan_day: int
    due: int
    close_day: int | None          # None=截止日仍未结
    close_event: str | None
    close_move: str | None
    status: str
    days_on_loan: int | None       # 截止口径下的在借天数（未结用 cutoff 截尾）
    late_days: int                 # 终结时超过 due 的天数（未结按 cutoff 计）
    turnaround_days: int | None    # 外借 -> 终结全流程天数
    wash_ready: bool               # 终结后是否已具备可洗护条件（本资料无洗护事件，默认待洗）


@dataclass
class BoxSnapshot:
    box: str
    type: str
    grade: str
    sleeve: str
    dispatch: int
    service_days: int              # 在营天数 = cutoff - dispatch
    loans: int                     # 实际外借次数（暴露）
    wears: int
    repairs: int
    transfers: int
    status: str                    # available / in_circulation / lost / recycled
    location: str | None           # 截止日所在节点（未知为 UNKNOWN）
    wash_state: str                # clean / needs_wash / unknown
    open_loan: str | None
    note: str = ""


@dataclass
class CrossStoreFlow:
    move: str
    day: int | None
    from_node: str | None
    to_node: str | None
    box: str | None
    routes: tuple[str, ...]
    kind: str                      # return / transfer / identify_return
    ambiguous: bool                # 多路线兼容，归属不唯一
    unidentified: bool             # 无法归属到盒


@dataclass
class Accounting:
    packet: Packet
    resolution: Resolution
    ambiguous_resolution: bool
    resolution_count: int
    loan_outcomes: dict[str, LoanOutcome]
    box_snapshots: dict[str, BoxSnapshot]
    flows: list[CrossStoreFlow]
    unidentified_returns: list[Move]
    scan_lags: list[tuple[str, int]]          # (move, received-day)
    cutoff_day: int
    # 各归属方案下的汇总区间（方案唯一时上下限相同）
    ranges: dict[str, tuple[float, float]] = field(default_factory=dict)


def _loan_route(move: Move) -> str:
    if not move.routes:
        return UNKNOWN
    if len(move.routes) > 1:
        return ",".join(move.routes)  # 多路线兼容，保留歧义
    return move.routes[0]


def build_accounting(packet: Packet, cutoff_day: int | None = None) -> Accounting:
    cutoff = cutoff_day if cutoff_day is not None else packet.cutoff_day
    resolutions = enumerate_resolutions(packet)
    resolution = resolutions[0]
    loan_box = {
        move.loan: move.box
        for move in packet.moves
        if move.event == "loan" and move.loan and move.box and move.box != UNKNOWN
    }

    # 外借事件索引
    loan_move: dict[str, Move] = {}
    for move in packet.moves:
        if move.event == "loan" and move.loan:
            loan_move[move.loan] = move

    outcomes: dict[str, LoanOutcome] = {}
    for loan, term in packet.terms.items():
        move = loan_move.get(loan)
        if move is None or not move.box or move.box == UNKNOWN:
            continue
        box = packet.boxes[move.box]
        close_move_id = resolution.loan_closer.get(loan)
        close_move = next((m for m in packet.moves if m.move == close_move_id), None)
        loan_day = move.day if move.day is not None else cutoff
        if close_move is None:
            status = OPEN_OVERDUE if cutoff > term.due else OPEN_DUE
            days_on_loan = cutoff - loan_day
            late = max(0, cutoff - term.due)
            outcomes[loan] = LoanOutcome(
                loan=loan, box=box.box, type=box.type, grade=box.grade,
                weather=term.weather, condition=term.condition,
                route=_loan_route(move), loan_day=loan_day, due=term.due,
                close_day=None, close_event=None, close_move=None,
                status=status, days_on_loan=days_on_loan, late_days=late,
                turnaround_days=None, wash_ready=False,
            )
            continue
        close_day = close_move.day if close_move.day is not None else cutoff
        event = close_move.event
        if event in {"return", "identify_return"}:
            status = RETURNED
        elif event == "loss":
            status = BORROWER_LOSS
        elif event == "recycle":
            status = RECYCLED
        else:
            status = UNIDENTIFIED
        outcomes[loan] = LoanOutcome(
            loan=loan, box=box.box, type=box.type, grade=box.grade,
            weather=term.weather, condition=term.condition,
            route=_loan_route(move), loan_day=loan_day, due=term.due,
            close_day=close_day, close_event=event, close_move=close_move.move,
            status=status, days_on_loan=close_day - loan_day,
            late_days=max(0, close_day - term.due),
            turnaround_days=close_day - loan_day,
            wash_ready=False,
        )

    # 逐盒快照
    snapshots: dict[str, BoxSnapshot] = {}
    for box in packet.boxes.values():
        related = [m for m in packet.moves if m.box == box.box]
        loans = sum(1 for m in related if m.event == "loan")
        wears = sum(1 for m in related if m.event == "wear")
        repairs = sum(1 for m in related if m.event == "repaired")
        transfers = sum(1 for m in related if m.event == "transfer")
        open_loan = next(
            (loan for loan, holder in _open_loans(packet, resolution).items() if holder == box.box),
            None,
        )
        last = related[-1] if related else None
        if any(o.status == BORROWER_LOSS and o.box == box.box for o in outcomes.values()):
            status, location, wash_state = "lost", None, "unknown"
        elif any(o.status == RECYCLED and o.box == box.box for o in outcomes.values()):
            status, location, wash_state = "recycled", last.node if last else None, "unknown"
        elif open_loan:
            status = "in_circulation"
            location = UNKNOWN
            wash_state = "unknown"
        else:
            status = "available"
            location = last.node if last and last.node else UNKNOWN
            # 本资料没有任何洗护完成事件：所有在库盒都需要洗护套核验后才能再出借
            wash_state = "needs_wash"
        note = ""
        if box.box in resolution.missed_scan:
            note = "存在清点漏扫信号（未结外借期间出现调拨）"
        snapshots[box.box] = BoxSnapshot(
            box=box.box, type=box.type, grade=box.grade, sleeve=box.sleeve,
            dispatch=box.dispatch, service_days=cutoff - box.dispatch,
            loans=loans, wears=wears, repairs=repairs, transfers=transfers,
            status=status, location=location, wash_state=wash_state,
            open_loan=open_loan, note=note,
        )

    # 跨店流量（归还 / 调拨 / 补确认）
    flows: list[CrossStoreFlow] = []
    for move in packet.moves:
        if move.event not in {"return", "transfer", "identify_return"}:
            continue
        ambiguous = len(move.routes) > 1
        if move.event == "return" and move.box == UNKNOWN:
            box_id = resolution.assignments.get(move.move)
            unidentified = box_id is None
        else:
            box_id = move.box if move.box and move.box != UNKNOWN else None
            unidentified = box_id is None
        if move.event == "transfer":
            # 调拨行 node=调出点；接收点不在记录中显式给出，保守标记为未知，
            # 来源节点取该盒此前最近具名事件，绝不把调出点误当接收点。
            from_node = _infer_from_node(packet, move, resolution)
            to_node = UNKNOWN
        else:
            from_node = _infer_from_node(packet, move, resolution)
            to_node = move.node
        flows.append(
            CrossStoreFlow(
                move=move.move, day=move.day, from_node=from_node,
                to_node=to_node, box=box_id, routes=move.routes,
                kind=move.event, ambiguous=ambiguous, unidentified=unidentified,
            )
        )

    unidentified_returns = [
        move
        for move in packet.moves
        if move.event == "return"
        and move.box == UNKNOWN
        and move.move not in resolution.assignments
    ]
    scan_lags = [
        (move.move, move.received - move.day)
        for move in packet.moves
        if move.day is not None and move.received is not None and move.received > move.day
    ]

    accounting = Accounting(
        packet=packet, resolution=resolution,
        ambiguous_resolution=len(resolutions) > 1,
        resolution_count=len(resolutions),
        loan_outcomes=outcomes, box_snapshots=snapshots, flows=flows,
        unidentified_returns=unidentified_returns, scan_lags=scan_lags,
        cutoff_day=cutoff,
    )
    accounting.ranges = _summary_ranges(packet, resolutions, cutoff)
    return accounting


def _open_loans(packet: Packet, resolution: Resolution) -> dict[str, str]:
    """截止日仍开放的 loan -> box。"""
    opened: dict[str, str] = {}
    for move in packet.moves:
        if move.event == "loan" and move.loan and move.box and move.box != UNKNOWN:
            opened[move.loan] = move.box
    for loan in resolution.loan_closer:
        opened.pop(loan, None)
    return opened


def _infer_from_node(packet: Packet, move: Move, resolution: Resolution) -> str | None:
    """推断流量来源节点：取该盒此前最近一次具名事件的节点。

    调拨行的 ``node`` 是接收节点（如 M20 在 S 接收、M21 在 W 接收），因此来源
    不能取事件自身节点；同日多事件只取严格早于当前的前序事件，避免把接收点当来源。
    """
    box = move.box
    if move.event == "return" and (box is None or box == UNKNOWN):
        box = resolution.assignments.get(move.move)
    if not box or box == UNKNOWN:
        return None
    prior = [
        m
        for m in packet.moves
        if m.box == box
        and m.node
        and (
            m.day is None
            or move.day is None
            or m.day < move.day
            or (m.day == move.day and m.move < move.move)
        )
    ]
    if not prior:
        return None
    return prior[-1].node


def _summarize(packet: Packet, resolution: Resolution, cutoff: int) -> dict[str, float]:
    """单套合法归属下的轻量汇总，供不确定性区间枚举复用。"""
    loan_box = {
        move.loan: move.box
        for move in packet.moves
        if move.event == "loan" and move.loan and move.box and move.box != UNKNOWN
    }
    exposures = len(loan_box)
    closed = resolution.loan_closer
    close_moves = {move_id: next(m for m in packet.moves if m.move == move_id)
                   for move_id in closed.values()}
    borrower_loss = sum(
        1 for m in packet.moves if m.event == "loss" and m.move in closed.values()
    )
    recycled = sum(
        1 for m in packet.moves if m.event == "recycle" and m.move in closed.values()
    )
    returned = sum(
        1
        for loan, move_id in closed.items()
        if next(m for m in packet.moves if m.move == move_id).event
        in {"return", "identify_return"}
    )
    # 逾期未结
    overdue_open = 0
    for loan, term in packet.terms.items():
        if loan not in closed and cutoff > term.due:
            overdue_open += 1
    wears = sum(1 for m in packet.moves if m.event == "wear")
    late_returns = 0
    total_late_days = 0
    for loan, move_id in closed.items():
        move = next(m for m in packet.moves if m.move == move_id)
        if move.event in {"return", "identify_return"} and move.day is not None:
            late = max(0, move.day - packet.terms[loan].due)
            total_late_days += late
            if late:
                late_returns += 1
    return {
        "exposures": float(exposures),
        "returned": float(returned),
        "borrower_loss": float(borrower_loss),
        "recycled": float(recycled),
        "unidentified": float(overdue_open),
        "wear_events": float(wears),
        "late_returns": float(late_returns),
        "late_days": float(total_late_days),
    }


def _summary_ranges(
    packet: Packet, resolutions: list[Resolution], cutoff: int
) -> dict[str, tuple[float, float]]:
    keys = ["exposures", "returned", "borrower_loss", "recycled", "unidentified",
            "wear_events", "late_returns", "late_days"]
    lows: dict[str, float] = {}
    highs: dict[str, float] = {}
    for resolution in resolutions:
        summary = _summarize(packet, resolution, cutoff)
        for key in keys:
            value = summary[key]
            lows[key] = value if key not in lows else min(lows[key], value)
            highs[key] = value if key not in highs else max(highs[key], value)
    return {key: (lows[key], highs[key]) for key in keys}
