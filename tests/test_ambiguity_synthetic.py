"""合成数据测试：多套合法归属时必须枚举并保守呈现，禁止跨店重复领回。

构造最小资料：同一封签 K 绑定两个盒，两笔外借后出现一个兼容两盒的匿名归还，
此时存在两套合法归属，求解器必须全部枚举。
"""
import csv
import json
import tempfile
import unittest
from pathlib import Path

from circulation.attribution import enumerate_resolutions
from circulation.io import load_packet

LAYOUT = {
    "boxes.tsv": ["box", "manufacturer", "type", "grade", "dispatch", "sleeve"],
    "circulation.tsv": ["move", "box", "sleeve", "loan", "day", "received",
                        "node", "event", "route"],
    "loan_terms.tsv": ["loan", "due", "expected_node", "weather", "condition"],
    "routes.tsv": ["route", "lend", "return", "allowed_wash", "expected_days"],
    "wash.tsv": ["node", "day", "slots", "special_slots"],
    "commitments.tsv": ["booking", "route", "from_day", "due", "grade", "count", "state"],
    "costs.tsv": ["type", "purchase", "use", "wash", "loss", "ceiling"],
}


def _write(folder: Path, name: str, header: list[str], rows: list[list[str]]) -> None:
    with (folder / name).open("w", newline="", encoding="utf-8") as source:
        writer = csv.writer(source, delimiter="\t", lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


class AmbiguityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        folder = Path(self.tmp.name)
        (folder / "layout.json").write_text(json.dumps(LAYOUT), encoding="utf-8")
        _write(folder, "boxes.tsv", LAYOUT["boxes.tsv"], [
            ["B1", "Y", "renew", "heavy", "1", "K"],
            ["B2", "Y", "renew", "heavy", "1", "K"],
        ])
        _write(folder, "circulation.tsv", LAYOUT["circulation.tsv"], [
            ["M1", "B1", "K", "L1", "2", "2", "S", "loan", "R1"],
            ["M2", "B2", "K", "L2", "3", "3", "S", "loan", "R1"],
            # 匿名归还兼容 B1/B2，两套归属都合法
            ["M3", "?", "K", "?", "5", "5", "N", "return", "R1"],
        ])
        _write(folder, "loan_terms.tsv", LAYOUT["loan_terms.tsv"], [
            ["L1", "8", "N", "dry", "normal"],
            ["L2", "8", "N", "dry", "normal"],
        ])
        _write(folder, "routes.tsv", LAYOUT["routes.tsv"], [
            ["R1", "S", "N", "N", "2,4"],
        ])
        _write(folder, "wash.tsv", LAYOUT["wash.tsv"], [
            ["N", "12", "1", "1"],
        ])
        _write(folder, "commitments.tsv", LAYOUT["commitments.tsv"], [])
        _write(folder, "costs.tsv", LAYOUT["costs.tsv"], [
            ["renew", "3900", "30", "220", "3900", "10800"],
        ])
        self.folder = folder

    def tearDown(self):
        self.tmp.cleanup()

    def test_multiple_legal_resolutions_are_enumerated(self):
        packet = load_packet(self.folder)
        resolutions = enumerate_resolutions(packet)
        self.assertEqual(len(resolutions), 2)
        assigned = {tuple(sorted(r.assignments.items()))[0][1] for r in resolutions}
        self.assertEqual(assigned, {"B1", "B2"})
        # 每套方案中匿名归还只关闭一个外借，另一笔保持开放，绝不重复领回
        for resolution in resolutions:
            self.assertEqual(len(resolution.loan_closer), 1)
            box_closed = next(iter(resolution.assignments.values()))
            still_open = {"B1", "B2"} - {box_closed}
            self.assertEqual(len(still_open), 1)


if __name__ == "__main__":
    unittest.main()
