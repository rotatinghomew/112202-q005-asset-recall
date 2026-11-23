"""离线 TSV 记录加载。

资料约定（README）：UTF-8 Tab 分隔，首行列名，逗号分隔集合，
``-`` 表示不适用、``?`` 表示未取得——二者都是显式标记，绝不解释成零。
标识符只在各表声明的归属范围内有效（例如 sleeve 只在封签来源身份内有效）。
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import NA, UNKNOWN


def _scalar(value: str) -> str | None:
    value = value.strip()
    if value == NA:
        return None  # 显式不适用
    if value == UNKNOWN:
        return UNKNOWN  # 显式未取得，保留为未知，禁止置零
    return value


def _int(value: str) -> int | None:
    value = value.strip()
    if value in (NA, UNKNOWN):
        return None
    return int(value)


def _set(value: str) -> tuple[str, ...]:
    value = value.strip()
    if value in (NA, UNKNOWN):
        return ()
    return tuple(part.strip() for part in value.split(","))


@dataclass(frozen=True)
class Box:
    box: str
    manufacturer: str
    type: str  # renew（循环盒）/ single（一次性盒）
    grade: str  # heavy / light
    dispatch: int
    sleeve: str


@dataclass(frozen=True)
class Move:
    move: str
    box: str | None  # None=不适用；'?'=未识别
    sleeve: str | None
    loan: str | None
    day: int | None
    received: int | None
    node: str | None
    event: str
    routes: tuple[str, ...]


@dataclass(frozen=True)
class LoanTerm:
    loan: str
    due: int
    expected_node: str
    weather: str
    condition: str


@dataclass(frozen=True)
class Route:
    route: str
    lend: str
    return_node: str
    allowed_wash: tuple[str, ...]
    expected_days: tuple[int, ...]  # (预计在借天数, 预计全流程回收天数)


@dataclass(frozen=True)
class WashSlot:
    node: str
    day: int
    slots: int
    special_slots: int


@dataclass(frozen=True)
class Commitment:
    booking: str
    route: str
    from_day: int
    due: int
    grade: str
    count: int
    state: str  # signed=已承诺，必须兑现；offer=可选


@dataclass(frozen=True)
class CostRow:
    type: str
    purchase: int
    use: int
    wash: int
    loss: int
    ceiling: int


@dataclass
class Packet:
    boxes: dict[str, Box]
    moves: list[Move]
    terms: dict[str, LoanTerm]
    routes: dict[str, Route]
    wash: list[WashSlot]
    commitments: list[Commitment]
    costs: dict[str, CostRow]
    layout: dict[str, list[str]]
    folder: Path = field(default_factory=lambda: Path("data"))

    @property
    def cutoff_day(self) -> int:
        """历史记录观察截止日：覆盖流通记录中最大的发生日与接收确认日。

        ``received`` 可能晚于 ``day``（返仓确认滞后），只取 day 会把已确认归还
        误截成“仍在借用”。
        """
        return max(
            [m.day for m in self.moves if m.day is not None]
            + [m.received for m in self.moves if m.received is not None]
        )

    def boxes_of_sleeve(self, sleeve: str) -> list[Box]:
        return [b for b in self.boxes.values() if b.sleeve == sleeve]


def load_packet(folder: str | Path) -> Packet:
    folder = Path(folder)
    with (folder / "layout.json").open(encoding="utf-8") as source:
        layout = json.load(source)

    with (folder / "boxes.tsv").open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source, delimiter="\t"))
    boxes = {
        row["box"]: Box(
            box=row["box"],
            manufacturer=row["manufacturer"],
            type=row["type"],
            grade=row["grade"],
            dispatch=int(row["dispatch"]),
            sleeve=row["sleeve"],
        )
        for row in rows
    }

    with (folder / "circulation.tsv").open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source, delimiter="\t"))
    moves = [
        Move(
            move=row["move"],
            box=_scalar(row["box"]),
            sleeve=_scalar(row["sleeve"]),
            loan=_scalar(row["loan"]),
            day=_int(row["day"]),
            received=_int(row["received"]),
            node=_scalar(row["node"]),
            event=row["event"],
            routes=_set(row["route"]),
        )
        for row in rows
    ]

    with (folder / "loan_terms.tsv").open(newline="", encoding="utf-8") as source:
        terms = {
            row["loan"]: LoanTerm(
                loan=row["loan"],
                due=int(row["due"]),
                expected_node=row["expected_node"],
                weather=row["weather"],
                condition=row["condition"],
            )
            for row in csv.DictReader(source, delimiter="\t")
        }

    with (folder / "routes.tsv").open(newline="", encoding="utf-8") as source:
        routes = {}
        for row in csv.DictReader(source, delimiter="\t"):
            days = tuple(int(part) for part in row["expected_days"].split(","))
            routes[row["route"]] = Route(
                route=row["route"],
                lend=row["lend"],
                return_node=row["return"],
                allowed_wash=_set(row["allowed_wash"]),
                expected_days=days,
            )

    with (folder / "wash.tsv").open(newline="", encoding="utf-8") as source:
        wash = [
            WashSlot(
                node=row["node"],
                day=int(row["day"]),
                slots=int(row["slots"]),
                special_slots=int(row["special_slots"]),
            )
            for row in csv.DictReader(source, delimiter="\t")
        ]

    with (folder / "commitments.tsv").open(newline="", encoding="utf-8") as source:
        commitments = [
            Commitment(
                booking=row["booking"],
                route=row["route"],
                from_day=int(row["from_day"]),
                due=int(row["due"]),
                grade=row["grade"],
                count=int(row["count"]),
                state=row["state"],
            )
            for row in csv.DictReader(source, delimiter="\t")
        ]

    with (folder / "costs.tsv").open(newline="", encoding="utf-8") as source:
        costs = {
            row["type"]: CostRow(
                type=row["type"],
                purchase=int(row["purchase"]),
                use=int(row["use"]),
                wash=int(row["wash"]),
                loss=int(row["loss"]),
                ceiling=int(row["ceiling"]),
            )
            for row in csv.DictReader(source, delimiter="\t")
        }

    return Packet(
        boxes=boxes,
        moves=sorted(moves, key=lambda item: (item.day or 0, item.move)),
        terms=terms,
        routes=routes,
        wash=wash,
        commitments=commitments,
        costs=costs,
        layout=layout,
        folder=folder,
    )
