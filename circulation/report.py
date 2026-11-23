"""命令行报告：把核算、比较、计划与提交审计组织成可交付的 TSV/文本输出。

用法：
    python3 -m circulation.report data [--submissions submissions_dir] [--out reports]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # 允许 python3 -I circulation/report.py 直接运行
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from circulation import UNKNOWN
from circulation.accounting import build_accounting
from circulation.comparison import compare
from circulation.io import load_packet
from circulation.metrics import rate_card, route_cards, type_cards
from circulation.planning import PlanningEngine
from circulation.submissions import audit as run_audit
from circulation.submissions import load_submissions


def _write_tsv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as source:
        writer = csv.DictWriter(source, fieldnames=fields, delimiter="\t",
                                extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _encode(value) for key, value in row.items()})


def _encode(value: object) -> object:
    if value is None:
        return UNKNOWN
    if isinstance(value, (tuple, list)):
        return ",".join(str(part) for part in value)
    return value


def build_reports(data_dir: Path, out_dir: Path, submissions_dir: Path | None = None) -> dict:
    packet = load_packet(data_dir)
    accounting = build_accounting(packet)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. 逐盒快照
    box_rows = [
        {
            "box": s.box, "type": s.type, "grade": s.grade, "sleeve": s.sleeve,
            "dispatch": s.dispatch, "service_days": s.service_days,
            "exposures": s.loans, "wears": s.wears, "repairs": s.repairs,
            "transfers": s.transfers, "status": s.status,
            "location": s.location, "wash_state": s.wash_state,
            "open_loan": s.open_loan or "-", "note": s.note or "-",
        }
        for s in accounting.box_snapshots.values()
    ]
    _write_tsv(out_dir / "box_snapshots.tsv", box_rows)

    # 2. 逐外借结局
    loan_rows = [
        {
            "loan": o.loan, "box": o.box, "type": o.type, "grade": o.grade,
            "weather": o.weather, "condition": o.condition, "route": o.route,
            "loan_day": o.loan_day, "due": o.due,
            "close_day": o.close_day, "close_event": o.close_event,
            "status": o.status, "days_on_loan": o.days_on_loan,
            "late_days": o.late_days,
            "turnaround_days": o.turnaround_days,
        }
        for o in accounting.loan_outcomes.values()
    ]
    _write_tsv(out_dir / "loan_outcomes.tsv", loan_rows)

    # 3. 跨店流量（含歧义与不可辨标记）
    flow_rows = [
        {
            "move": f.move, "day": f.day, "from_node": f.from_node,
            "to_node": f.to_node, "box": f.box, "kind": f.kind,
            "routes": f.routes, "ambiguous": int(f.ambiguous),
            "unidentified": int(f.unidentified),
        }
        for f in accounting.flows
    ]
    _write_tsv(out_dir / "cross_store_flows.tsv", flow_rows)

    # 4. 双口径度量：按盒型、按路线
    type_rows = [
        {"scope": "type", "key": key, **card.as_row()}
        for key, card in type_cards(accounting).items()
    ]
    route_rows = [
        {"scope": "route", "key": key, **card.as_row()}
        for key, card in route_cards(accounting).items()
    ]
    overall = rate_card(accounting)
    metric_rows = [{"scope": "all", "key": "ALL", **overall.as_row()}] + type_rows + route_rows
    _write_tsv(out_dir / "metrics.tsv", metric_rows)

    # 5. 共同支持比较
    report = compare(accounting)
    stratum_rows = [
        {
            "grade": s.key[0], "weather": s.key[1], "condition": s.key[2],
            "renew_n": s.renew_n, "single_n": s.single_n,
            "renew_borrower_loss": s.renew_bad, "single_borrower_loss": s.single_bad,
            "renew_wear": s.renew_wear,
            "renew_unidentified": s.renew_unidentified,
            "single_unidentified": s.single_unidentified,
            "fisher_p": s.p_value if s.p_value is not None else UNKNOWN,
            "comparable": int(s.comparable), "conclusion": s.conclusion,
        }
        for s in report.strata
    ]
    _write_tsv(out_dir / "comparison_strata.tsv", stratum_rows)
    (out_dir / "comparison_structural.json").write_text(
        json.dumps(
            {
                "overall_conclusion": report.overall_conclusion,
                "identifiable": report.identifiable,
                "structural_effects": report.structural,
                "selection_notes": report.selection_notes,
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )

    # 6. 下期计划场景
    engine = PlanningEngine(packet, accounting)
    scenarios = engine.standard_scenarios()
    scenario_rows = []
    commitment_rows = []
    dependency_rows = []
    for scenario in scenarios:
        scenario_rows.append(
            {
                "scenario": scenario.name,
                "active_routes": scenario.active_routes,
                "keep_return_points": scenario.keep_returns,
                "feasible": int(scenario.feasible),
                "unmet": scenario.unmet,
                "exposure_change": scenario.exposure_change,
                "frozen_in_circulation": scenario.attribution_exposure,
                "loss_risk_reserve": scenario.loss_risk,
                "next_period_avoidable_cost": scenario.next_period_cost,
                "prior_period_sunk_loss": scenario.prior_period_sunk,
                "within_budget": int(scenario.within_budget),
                "capacity_risks": scenario.capacity_risk,
                "idle_lendable": scenario.idle_lendable,
            }
        )
        for dependency in scenario.return_dependencies:
            dependency_rows.append({"scenario": scenario.name, **dependency})
        for result in scenario.results:
            commitment_rows.append(
                {
                    "scenario": scenario.name, "booking": result.booking,
                    "route": result.route, "grade": result.grade,
                    "count": result.count, "state": result.state,
                    "feasible": int(result.feasible),
                    "assigned_boxes": result.assigned_boxes,
                    "wash_plan": [
                        f"{use.node}@d{use.day}" for use in result.wash_plan
                    ],
                    "reason": result.reason or "-",
                }
            )
    _write_tsv(out_dir / "planning_scenarios.tsv", scenario_rows)
    _write_tsv(out_dir / "planning_commitments.tsv", commitment_rows)
    _write_tsv(out_dir / "return_dependencies.tsv", dependency_rows)
    (out_dir / "planning_assumptions.json").write_text(
        json.dumps(
            {
                "cutoff_day": accounting.cutoff_day,
                "transfer_leads": {
                    f"{src}->{dst}": days
                    for (src, dst), days in sorted(engine.transfer_leads.items())
                },
                "unknown_pair_lead": engine.unknown_pair_lead,
                "note": "未观测点对采用保守前置；同节点前置为0；实测记录可覆盖默认值",
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )

    summary = {
        "cutoff_day": accounting.cutoff_day,
        "resolution_count": accounting.resolution_count,
        "ambiguous_resolution": accounting.ambiguous_resolution,
        "assignments": accounting.resolution.assignments,
        "missed_scan": sorted(accounting.resolution.missed_scan),
        "scan_lags": accounting.scan_lags,
        "unidentified_returns": [m.move for m in accounting.unidentified_returns],
        "comparison": report.overall_conclusion,
        "scenarios": [
            {
                "name": s.name, "feasible": s.feasible, "unmet": s.unmet,
                "within_budget": s.within_budget,
                "next_cost": s.next_period_cost, "sunk": s.prior_period_sunk,
            }
            for s in scenarios
        ],
    }

    # 7. 提交审计（可选）
    if submissions_dir is not None:
        new_loans, identities = load_submissions(submissions_dir)
        audit_report = run_audit(packet, new_loans, identities)
        audit_rows = [
            {
                "submission": item.submission, "route": item.route,
                "from_day": item.from_day, "due": item.due,
                "grade": item.grade, "count": item.count,
                "box": item.box or UNKNOWN, "kind": "new_loan",
            }
            for item in audit_report.accepted_new_loans
        ] + [
            {
                "submission": item.submission, "route": UNKNOWN,
                "from_day": UNKNOWN, "due": UNKNOWN, "grade": UNKNOWN,
                "count": 1, "box": item.box, "kind": f"identity:{item.move}",
            }
            for item in audit_report.accepted_identities
        ]
        for submission, reason in audit_report.rejected:
            audit_rows.append(
                {"submission": submission, "route": UNKNOWN, "from_day": UNKNOWN,
                 "due": UNKNOWN, "grade": UNKNOWN, "count": 0,
                 "box": UNKNOWN, "kind": f"REJECTED: {reason}"}
            )
        _write_tsv(out_dir / "submission_audit.tsv", audit_rows)
        summary["submission_audit"] = {
            "decisions_still_valid": audit_report.decisions_still_valid,
            "decisions_broken": audit_report.decisions_broken,
            "unmet_commitments": audit_report.unmet_commitments,
            "sunk_gap": audit_report.sunk_gap,
            "avoidable_gap": audit_report.avoidable_gap,
            "ceiling": audit_report.ceiling,
        }

    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path, nargs="?", default=Path("data"))
    parser.add_argument("--submissions", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=Path("reports"))
    args = parser.parse_args()
    summary = build_reports(args.data, args.out, args.submissions)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
