#!/usr/bin/env python
"""
Stage 1b — Validate split output against config.official_abstract_count.

Reads stage1_summary.json from the split run. Hard-fails if:
  - detected_count < 0.90 * official_abstract_count
  - gate in stage1_summary.json != "PASS"

This is a standalone check to run manually if needed.
"""
import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary_json", type=Path, help="stage1_summary.json from the split run")
    parser.add_argument("--config", type=Path, required=True, help="config.json")
    args = parser.parse_args()

    summary = json.loads(args.summary_json.read_text())
    cfg = json.loads(args.config.read_text())
    official_count = cfg["conference"].get("official_abstract_count")

    if summary.get("gate") != "PASS":
        print(f"HARD GATE FAIL: stage1_summary.json gate={summary.get('gate')} reason={summary.get('gate_reason')}", file=sys.stderr)
        return 1

    detected = summary["detected_count"]
    if official_count:
        ratio = detected / official_count
        if ratio < 0.90:
            print(f"HARD GATE FAIL: detected {detected} < 90% of official {official_count} ({ratio:.1%})", file=sys.stderr)
            return 1
        print(f"PASS: detected {detected} / official {official_count} = {ratio:.1%} (≥ 90% threshold)")
    else:
        print(f"PASS (no official_abstract_count set): detected {detected} abstracts")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
