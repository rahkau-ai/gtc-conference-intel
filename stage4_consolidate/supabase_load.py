#!/usr/bin/env python3
"""Upsert schema v1 facts (JSONL) into Supabase cgt_research_facts.

Requires env vars:
  SUPABASE_URL           — e.g. https://logrdhbxtlbzzklisvhu.supabase.co
  SUPABASE_SERVICE_KEY   — service role key (from hub/.env)

Usage:
  # Load migrated legacy data
  python stage4_consolidate/supabase_load.py \\
    --in-jsonl runs/asgct-2026/migrated_v0_facts.jsonl

  # Load fresh Stage 4 output
  python stage4_consolidate/supabase_load.py \\
    --in-jsonl runs/asgct-2026/tables/master_facts_v1.jsonl

  # Preview without uploading
  python stage4_consolidate/supabase_load.py \\
    --in-jsonl runs/asgct-2026/migrated_v0_facts.jsonl --dry-run
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests", file=sys.stderr)
    sys.exit(1)

BATCH_SIZE = 200


def load_facts(jsonl_path: Path, dry_run: bool = False) -> None:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")

    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY env vars must be set", file=sys.stderr)
        print("  source the hub .env:  set -o allexport && source C:/Users/Me/Desktop/GTC/hub/.env && set +o allexport")
        sys.exit(1)

    endpoint = f"{url}/rest/v1/cgt_research_facts?on_conflict=abstract_id,source_id,schema_version"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }

    facts = []
    with open(jsonl_path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if line:
                try:
                    facts.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"WARNING: invalid JSON on line {i}: {e}", file=sys.stderr)

    print(f"Loaded  : {len(facts)} facts from {jsonl_path}")

    if dry_run:
        print("DRY RUN — not uploading. First row:")
        print(json.dumps(facts[0], indent=2) if facts else "(empty)")
        return

    total = 0
    for i in range(0, len(facts), BATCH_SIZE):
        batch = facts[i : i + BATCH_SIZE]
        resp = requests.post(endpoint, headers=headers, json=batch, timeout=60)
        if resp.status_code not in (200, 201):
            print(f"ERROR batch {i // BATCH_SIZE + 1}: HTTP {resp.status_code}", file=sys.stderr)
            print(resp.text[:400], file=sys.stderr)
            sys.exit(1)
        total += len(batch)
        pct = int(total / len(facts) * 100)
        print(f"Upserted: {total}/{len(facts)} ({pct}%)")

    print(f"\nDone: {total} rows upserted to cgt_research_facts")


def main():
    parser = argparse.ArgumentParser(description="Load schema v1 JSONL → Supabase cgt_research_facts")
    parser.add_argument("--in-jsonl", required=True, help="JSONL file from migrate_v0_to_v1.py or consolidate.py")
    parser.add_argument("--dry-run", action="store_true", help="Parse and preview, don't upload")
    args = parser.parse_args()

    path = Path(args.in_jsonl)
    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr)
        sys.exit(1)

    load_facts(path, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
