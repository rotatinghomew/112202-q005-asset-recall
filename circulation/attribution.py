"""封签复用下的合法归属枚举。

难点（业务背景）：sleeve 是封签来源身份，可被多个盒复用；早期记录只有封签号与
接收批次（``box=?``、``loan=?``），一个匿名归还可能兼容多个盒。必须在**一套合法
归属**下计算跨店流量与损耗：

- 一次外借（loan）至多被一个终结事件（return / identify_return / loss / recycle）关闭；
- 一个匿名归还只能匹配给一个盒，禁止两个店同时领回同一盒；
- 候选盒必须在归还发生前已外借、且当时仍持有未结外借；
- ``identify_return`` 是补确认：按其 loan 找到出借盒，确认日以 ``received`` 计；
- 漏扫不臆造归还：外借后无任何终结记录、却出现该盒的调拨，只记“清点漏扫信号”，
  在借状态仍然保留。

枚举全部合法匹配；若不止一套，下游必须报告归属不确定性区间，不能挑选有利方案。
"""
from __future__ import annotations

from dataclasses import dataclass

from .io import Packet, UNKNOWN

# 终结一次外借的事件类型
CLOSING_EVENTS = frozenset({"return", "identify_return", "loss", "recycle"})


@dataclass(frozen=True)
class Resolution:
    """一套合法归属。"""

    # 匿名归还 move -> 盒
    assignments: dict[str, str]
    # loan -> 终结它的 move
    loan_closer: dict[str, str]
    # 存在漏扫信号的盒（外借未终结却出现该盒调拨）
    missed_scan: frozenset[str]
    # 无法与任何盒登记封签对齐的事件（数据冲突，单独披露）
    conflicts: tuple[str, ...]


def _loan_box_map(packet: Packet) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for move in packet.moves:
        if move.event == "loan" and move.loan and move.box and move.box != UNKNOWN:
            if move.loan in mapping and mapping[move.loan] != move.box:
                raise ValueError(f"{move.move}: 同一外借 {move.loan} 对应两个盒")
            mapping[move.loan] = move.box
    return mapping


def _check_sleeve_conflicts(packet: Packet) -> tuple[str, ...]:
    conflicts: list[str] = []
    for move in packet.moves:
        if move.box and move.box != UNKNOWN and move.sleeve:
            registered = packet.boxes[move.box].sleeve
            if registered != move.sleeve:
                conflicts.append(
                    f"{move.move}: 盒 {move.box} 登记封签 {registered} 与记录封签 {move.sleeve} 不符"
                )
    return tuple(conflicts)


def enumerate_resolutions(packet: Packet) -> list[Resolution]:
    """在完整时间线回放中分支，返回全部合法归属方案。"""
    loan_box = _loan_box_map(packet)
    conflicts = _check_sleeve_conflicts(packet)
    timeline = sorted(packet.moves, key=lambda item: (item.day or 0, item.move))

    results: list[Resolution] = []
    seen: set[tuple[tuple[str, str], ...]] = set()

    def record(
        assignments: dict[str, str],
        closer: dict[str, str],
        missed: set[str],
    ) -> None:
        key = tuple(sorted(assignments.items()))
        if key in seen:
            return
        seen.add(key)
        results.append(
            Resolution(
                assignments=dict(assignments),
                loan_closer=dict(closer),
                missed_scan=frozenset(missed),
                conflicts=conflicts,
            )
        )

    def replay(
        index: int,
        open_loans: dict[str, str],  # loan -> box
        assignments: dict[str, str],
        closer: dict[str, str],
        missed: set[str],
    ) -> None:
        if index == len(timeline):
            record(assignments, closer, missed)
            return
        move = timeline[index]
        event = move.event

        if event == "loan":
            if not move.loan or not move.box or move.box == UNKNOWN:
                return  # 非法记录，剪枝
            if move.box in open_loans.values():
                return  # 盒未归还又外借，剪枝
            open_loans[move.loan] = move.box
            replay(index + 1, open_loans, assignments, closer, missed)
            del open_loans[move.loan]
            return

        if event == "return":
            if move.box == UNKNOWN:
                candidates = (
                    sorted(
                        {
                            holder
                            for holder in open_loans.values()
                            if move.sleeve and packet.boxes[holder].sleeve == move.sleeve
                        }
                    )
                    if move.sleeve
                    else []
                )
                if not candidates:
                    # 无合法候选：匿名归还不可辨，不关闭任何外借，单独保留
                    replay(index + 1, open_loans, assignments, closer, missed)
                    return
                for box in candidates:  # 每个候选盒各形成一套合法归属
                    open_loan = next(loan for loan, holder in open_loans.items() if holder == box)
                    del open_loans[open_loan]
                    assignments[move.move] = box
                    closer[open_loan] = move.move
                    replay(index + 1, open_loans, assignments, closer, missed)
                    open_loans[open_loan] = box
                    del assignments[move.move]
                    del closer[open_loan]
                return
            # 具名归还
            if move.box not in open_loans.values():
                return
            open_loan = move.loan or next(
                loan for loan, holder in open_loans.items() if holder == move.box
            )
            if open_loan not in open_loans:
                return
            del open_loans[open_loan]
            closer[open_loan] = move.move
            replay(index + 1, open_loans, assignments, closer, missed)
            open_loans[open_loan] = move.box
            del closer[open_loan]
            return

        if event == "identify_return":
            if not move.loan or move.loan not in loan_box:
                return
            if move.loan in closer or move.loan not in open_loans:
                return  # 同一外借被重复终结，剪枝
            box = loan_box[move.loan]
            if move.box and move.box != UNKNOWN and move.box != box:
                return
            del open_loans[move.loan]
            closer[move.loan] = move.move
            replay(index + 1, open_loans, assignments, closer, missed)
            open_loans[move.loan] = box
            del closer[move.loan]
            return

        if event in {"loss", "recycle"}:
            if not move.box or move.box == UNKNOWN:
                return
            if move.box not in open_loans.values():
                return
            open_loan = move.loan or next(
                loan for loan, holder in open_loans.items() if holder == move.box
            )
            if open_loan not in open_loans:
                return
            del open_loans[open_loan]
            closer[open_loan] = move.move
            replay(index + 1, open_loans, assignments, closer, missed)
            open_loans[open_loan] = move.box
            del closer[open_loan]
            return

        if event in {"wear", "repaired"}:
            if not move.box or move.box == UNKNOWN:
                return
            replay(index + 1, open_loans, assignments, closer, missed)
            return

        if event == "transfer":
            if not move.box or move.box == UNKNOWN:
                return
            if move.box in open_loans.values():
                # 未结外借期间出现调拨：清点漏扫信号，但不臆造归还
                missed.add(move.box)
                replay(index + 1, open_loans, assignments, closer, missed)
                missed.discard(move.box)
                return
            replay(index + 1, open_loans, assignments, closer, missed)
            return

        # 未知事件类型：保守拒绝，避免静默吞掉新口径
        raise ValueError(f"未支持的事件类型: {move.event} ({move.move})")

    replay(0, {}, {}, {}, set())
    if not results:
        raise ValueError("不存在任何合法归属回放，资料存在结构性冲突")
    return results
