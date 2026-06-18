#!/usr/bin/env python
"""
Stage 4c — Coverage report and soft gate (FM-15).

Reads master CSV and stage1_summary.json. Computes:
  - Coverage per column (% non-null, non-UNKNOWN)
  - Gap analysis: abstract IDs in manifest but not in master CSV

Emits SOFT GATE (warning, not hard stop) if gap% > threshold.
Writes coverage_report.json.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master-csv", type=Path, required=True)
    parser.add_argument("--stage1-summary", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()

    cfg = json.loads(args.config.read_text())
    gates = cfg.get("gates", {})
    warn_threshold = gates.get("coverage_warning_threshold_pct", 10)
    hard_threshold = gates.get("coverage_hard_fail_threshold_pct", 30)

    stage1 = json.loads(args.stage1_summary.read_text())
    manifest_count = stage1["detected_count"]
    known_gaps = set(str(x) for x in stage1.get("known_gaps", []))

    with args.master_csv.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    total = len(rows)
    extracted_ids = set(r.get("abstract_id", "") for r in rows)

    # Gap analysis: IDs in manifest but not in extracted (excluding known gaps)
    manifest_ids = set()
    manifest_path = args.stage1_summary.parent / [f for f in args.stage1_summary.parent.iterdir() if f.name.endswith("_split_manifest.csv")][0].name
    if manifest_path.exists():
        with manifest_path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                for aid in row.get("abstract_ids", "").split(";"):
                    manifest_ids.add(aid.strip())

    unexpected_gaps = manifest_ids - extracted_ids - known_gaps
    gap_count = len(unexpected_gaps)
    gap_pct = (gap_count / manifest_count * 100) if manifest_count > 0 else 0

    # Column coverage
    columns = rows[0].keys() if rows else []
    col_coverage: dict[str, float] = {}
    for col in columns:
        filled = sum(1 for r in rows if r.get(col) and r[col].strip() not in ("", "UNKNOWN", "not_reported", "not_applicable", "unclear"))
        col_coverage[col] = round(filled / total * 100, 1) if total > 0 else 0.0

    # Gate logic
    if gap_pct >= hard_threshold:
        gate = "HARD_FAIL"
        gate_reason = f"gap% {gap_pct:.1f}% ≥ hard threshold {hard_threshold}%"
    elif gap_pct >= warn_threshold:
        gate = "SOFT_WARNING"
        gate_reason = f"gap% {gap_pct:.1f}% ≥ warning threshold {warn_threshold}% — sign-off required before proceeding"
    else:
        gate = "PASS"
        gate_reason = None

    report = {
        "stage": "stage4_coverage",
        "manifest_count": manifest_count,
        "extracted_count": total,
        "known_gaps_count": len(known_gaps),
        "unexpected_gaps_count": gap_count,
        "gap_pct": round(gap_pct, 2),
        "gate": gate,
        "gate_reason": gate_reason,
        "signed_off_by": None,
        "column_coverage": col_coverage,
    }

    out_path = args.master_csv.parent / "coverage_report.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))

    if gate == "HARD_FAIL":
        print(f"\nHARD GATE FAIL: {gate_reason}", file=sys.stderr)
        return 1
    elif gate == "SOFT_WARNING":
        print(f"\nSOFT WARNING: {gate_reason}")
        print("To proceed: add 'signed_off_by': '<your name>' to coverage_report.json and re-run Stage 5.")
        return 2  # Exit 2 = soft warning (not a failure)

    print(f"\nStage 4c COMPLETE. Gate: PASS. Gap: {gap_pct:.1f}%.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
