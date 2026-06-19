#!/usr/bin/env python
"""
Stage 4a — Normalise extracted fact JSONL files against the locked schema v1.

Validates:
  - Required fields are present and non-empty
  - Conditional fields (quant_*) present when fact_type == "datapoint"
  - No unknown keys (warns but does not fail)
  - All rows have schema_version set

Writes normalised JSONL with UNKNOWN filled for missing required fields.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

REQUIRED_FIELDS = ["abstract_id", "source_id", "source_type", "fact_type", "subject", "what", "evidence_quote", "citation", "confidence"]
CONDITIONAL_FIELDS = ["quant_value", "quant_unit", "quant_context"]
OPTIONAL_FIELDS = ["modality", "disease", "organisation", "geography", "schema_version", "prompt_hash",
                   "development_stage", "sponsor_type", "ip_signals", "aav_capsid",
                   "therapeutic_payload", "trial_id", "manufacturing_gmp_signal"]
KNOWN_FACT_TYPES = {"finding", "event", "announcement", "datapoint", "claim"}
KNOWN_CONFIDENCE = {"high", "medium", "low"}
KNOWN_MODALITIES = {"gene_therapy", "gene_editing", "cell_therapy", "mRNA", "other", None}


def normalise_row(row: dict, row_num: int) -> tuple[dict, list[str]]:
    issues: list[str] = []
    out = dict(row)

    for field in REQUIRED_FIELDS:
        if not out.get(field):
            issues.append(f"row {row_num}: missing required field '{field}' — set to UNKNOWN")
            out[field] = "UNKNOWN"

    if out.get("fact_type") == "datapoint":
        for field in CONDITIONAL_FIELDS:
            if out.get(field) is None:
                issues.append(f"row {row_num}: datapoint missing conditional field '{field}'")

    if out.get("fact_type") not in KNOWN_FACT_TYPES:
        issues.append(f"row {row_num}: unknown fact_type='{out.get('fact_type')}' — keeping as-is")

    if out.get("confidence") not in KNOWN_CONFIDENCE:
        issues.append(f"row {row_num}: unknown confidence='{out.get('confidence')}' — setting to 'low'")
        out["confidence"] = "low"

    if out.get("modality") not in KNOWN_MODALITIES:
        issues.append(f"row {row_num}: unknown modality='{out.get('modality')}' — setting to null")
        out["modality"] = None

    if out.get("evidence_quote", "") == "UNKNOWN":
        out["confidence"] = "low"

    out.setdefault("schema_version", "v1")
    return out, issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in-dir", type=Path, required=True, help="Stage 3 output dir with *_facts.jsonl")
    parser.add_argument("--out-file", type=Path, required=True, help="Output: normalised_facts.jsonl")
    args = parser.parse_args()

    in_dir = args.in_dir.resolve()
    jsonl_files = sorted(in_dir.glob("*_facts.jsonl"))
    if not jsonl_files:
        print(f"No *_facts.jsonl files found in {in_dir}", file=sys.stderr)
        return 1

    all_issues: list[str] = []
    row_num = 0
    args.out_file.parent.mkdir(parents=True, exist_ok=True)

    with args.out_file.open("w", encoding="utf-8") as out_f:
        for jsonl_file in jsonl_files:
            for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row_num += 1
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as e:
                    all_issues.append(f"row {row_num}: JSON parse error in {jsonl_file.name}: {e}")
                    continue
                normalised, issues = normalise_row(row, row_num)
                all_issues.extend(issues)
                out_f.write(json.dumps(normalised) + "\n")

    print(f"Normalised {row_num} rows → {args.out_file}")
    if all_issues:
        print(f"\nIssues found ({len(all_issues)}):")
        for issue in all_issues[:50]:
            print(f"  {issue}")
        if len(all_issues) > 50:
            print(f"  ... and {len(all_issues) - 50} more")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
