"""同项目提交审计测试：合法归属确认、拒绝跨店重复领回、决定有效性与预算缺口。"""
import tempfile
import unittest
from pathlib import Path

from circulation.io import load_packet
from circulation.submissions import audit, load_submissions

ROOT = Path(__file__).resolve().parents[1]


def _write(folder: Path, name: str, header: list[str], rows: list[list[str]]) -> None:
    lines = ["\t".join(header)] + ["\t".join(row) for row in rows]
    (folder / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.packet = load_packet(ROOT / "data")

    def test_identity_confirmation_must_match_legal_assignment(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            _write(
                folder, "identity_confirmations.tsv",
                ["submission", "move", "box", "loan"],
                [
                    ["Q1", "M3", "B1", "L1"],   # 合法
                    ["Q2", "M3", "B2", "L2"],   # 非法：M3 只能归 B1
                ],
            )
            _write(folder, "new_loans.tsv",
                   ["submission", "route", "from_day", "due", "grade", "count", "box"],
                   [])
            new_loans, identities = load_submissions(folder)
            report = audit(self.packet, new_loans, identities)
            accepted = {item.submission for item in report.accepted_identities}
            rejected = dict(report.rejected)
            self.assertIn("Q1", accepted)
            self.assertIn("Q2", rejected)

    def test_unknown_move_confirmation_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            _write(
                folder, "identity_confirmations.tsv",
                ["submission", "move", "box", "loan"],
                [["Q3", "M99", "B1", "-"]],
            )
            _write(folder, "new_loans.tsv",
                   ["submission", "route", "from_day", "due", "grade", "count", "box"],
                   [])
            new_loans, identities = load_submissions(folder)
            report = audit(self.packet, new_loans, identities)
            self.assertTrue(any(item[0] == "Q3" for item in report.rejected))

    def test_new_loan_validation_and_gap_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            _write(folder, "identity_confirmations.tsv",
                   ["submission", "move", "box", "loan"], [])
            _write(
                folder, "new_loans.tsv",
                ["submission", "route", "from_day", "due", "grade", "count", "box"],
                [
                    ["Q4", "R1", "20", "23", "light", "1", "-"],   # 结构合法
                    ["Q5", "R9", "20", "23", "light", "1", "-"],   # 未知路线
                ],
            )
            new_loans, identities = load_submissions(folder)
            report = audit(self.packet, new_loans, identities)
            accepted = {item.submission for item in report.accepted_new_loans}
            self.assertIn("Q4", accepted)
            self.assertTrue(any(item[0] == "Q5" for item in report.rejected))
            # 沉没缺口与可避免缺口分列
            self.assertEqual(report.sunk_gap, 3900)
            self.assertGreaterEqual(report.avoidable_gap, 0)
            self.assertEqual(report.ceiling, 10800)

    def test_broken_decisions_name_missing_boxes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            _write(folder, "identity_confirmations.tsv",
                   ["submission", "move", "box", "loan"], [])
            _write(folder, "new_loans.tsv",
                   ["submission", "route", "from_day", "due", "grade", "count", "box"],
                   [])
            _, identities = load_submissions(folder)
            report = audit(self.packet, [], identities)
            # 现有 signed 承诺在联合排程下无法全部兑现，需点名缺盒
            broken = dict(report.decisions_broken)
            self.assertIn("O1", broken)
            self.assertIn("O2", broken)
            self.assertEqual(report.unmet_commitments["O1"], 1)

    def test_specified_box_must_be_physically_lendable(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            _write(folder, "identity_confirmations.tsv",
                   ["submission", "move", "box", "loan"], [])
            _write(
                folder, "new_loans.tsv",
                ["submission", "route", "from_day", "due", "grade", "count", "box"],
                [
                    ["Q6", "R1", "20", "23", "light", "1", "B3"],  # 在库可洗，合法
                    ["Q7", "R1", "20", "23", "light", "1", "B4"],  # 已丢失
                    ["Q8", "R2", "20", "23", "heavy", "1", "B9"],  # 在借冻结
                    ["Q9", "R1", "20", "23", "light", "1", "B6"],  # 已回收/一次性
                ],
            )
            new_loans, identities = load_submissions(folder)
            report = audit(self.packet, new_loans, identities)
            broken = dict(report.decisions_broken)
            self.assertIn("Q6", report.decisions_still_valid)
            self.assertIn("Q7", broken)
            self.assertIn("Q8", broken)
            self.assertIn("Q9", broken)


if __name__ == "__main__":
    unittest.main()
