#!/usr/bin/env python
"""
Stage 6a — Build citations.json from master CSV (FM-25).

assemble.mjs validates every {{cite:NNN}} marker against this file before
building the HTML report.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master-csv", type=Path, required=True)
    parser.add_argument("--out-file", type=Path, required=True)
    args = parser.parse_args()

    citations: dict[str, dict] = {}
    with args.master_csv.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            aid = row.get("abstract_id", "")
            if not aid:
                continue
            citations[aid] = {
                "abstract_id": aid,
                "subject": row.get("subject", ""),
                "what": row.get("what", ""),
                "organisation": row.get("organisation", ""),
                "citation": row.get("citation", ""),
                "evidence_quote": row.get("evidence_quote", ""),
                "confidence": row.get("confidence", ""),
                "modality": row.get("modality", ""),
                "disease": row.get("disease", ""),
            }

    args.out_file.parent.mkdir(parents=True, exist_ok=True)
    args.out_file.write_text(json.dumps(citations, indent=2), encoding="utf-8")
    print(f"citations.json written: {len(citations)} entries → {args.out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
