#!/usr/bin/env python
"""
Stage 4b — Deduplicate, write master CSV, compute SHA256 checksum.

Reads normalised_facts.jsonl. Deduplicates on (abstract_id, source_id).
Writes master CSV + <filename>.sha256 checksum file.
Writes stage4_summary.json with pre_dedup, dups_removed, final_count.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')
from pathlib import Path


MASTER_CSV_FIELDS = [
    "abstract_id", "source_id", "source_type", "fact_type",
    "subject", "what",
    "quant_value", "quant_unit", "quant_context",
    "modality", "disease", "organisation", "geography",
    "evidence_quote", "citation", "confidence", "schema_version", "prompt_hash",
    # Tier 2 intelligence signals (v1-python upgrade)
    "development_stage", "sponsor_type", "ip_signals", "aav_capsid",
    "therapeutic_payload", "trial_id", "manufacturing_gmp_signal",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in-file", type=Path, required=True, help="normalised_facts.jsonl")
    parser.add_argument("--out-csv", type=Path, required=True, help="Master CSV output path")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()

    cfg = json.loads(args.config.read_text())
    run_id = cfg["extraction"]["run_id"]

    rows: list[dict] = []
    for line in args.in_file.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))

    pre_dedup = len(rows)

    # Dedup on (abstract_id, source_id) — keep first occurrence
    seen: set[tuple] = set()
    deduped: list[dict] = []
    for row in rows:
        key = (str(row.get("abstract_id", "")), str(row.get("source_id", "")))
        if key not in seen:
            seen.add(key)
            deduped.append(row)

    dups_removed = pre_dedup - len(deduped)
    final_count = len(deduped)

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MASTER_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(deduped)

    # SHA256 checksum (FM-18)
    checksum = hashlib.sha256(args.out_csv.read_bytes()).hexdigest()
    checksum_path = args.out_csv.parent / f"{args.out_csv.name}.sha256"
    checksum_path.write_text(f"{checksum}  {args.out_csv.name}\n", encoding="utf-8")

    summary = {
        "stage": "stage4_consolidate",
        "run_id": run_id,
        "pre_dedup": pre_dedup,
        "dups_removed": dups_removed,
        "final_count": final_count,
        "master_csv": str(args.out_csv),
        "checksum": checksum,
        "checksum_file": str(checksum_path),
    }
    summary_path = args.out_csv.parent / "stage4_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"\nStage 4 COMPLETE. {pre_dedup} → dedup → {final_count} rows. SHA256: {checksum[:12]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
