#!/usr/bin/env python
"""
Stage 2a — Check NotebookLM session is valid before starting batch ingest.

Runs `notebooklm source list -n <notebook_id> --json` and verifies the response
is parseable JSON with a `sources` key. Exits non-zero if session appears expired
or the CLI returns an error.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()

    cfg = json.loads(args.config.read_text())
    nlm_cfg = cfg["notebooklm"]
    notebook_id = nlm_cfg["notebook_id"]
    nlm_path = nlm_cfg.get("notebooklm_path", "notebooklm")

    print(f"Checking NotebookLM session for notebook {notebook_id}...")
    try:
        result = subprocess.run(
            [nlm_path, "source", "list", "-n", notebook_id, "--json"],
            text=True, capture_output=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        print("FAIL: notebooklm CLI timed out (30s). Is the CLI installed?", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print(f"FAIL: notebooklm not found at {nlm_path}", file=sys.stderr)
        return 1

    if result.returncode != 0:
        print(f"FAIL (exit {result.returncode}): {result.stderr.strip()}", file=sys.stderr)
        print("Session may be expired. Re-auth: run inject-cookies.py then retry.", file=sys.stderr)
        return 1

    try:
        data = json.loads(result.stdout)
        count = data.get("count", len(data.get("sources", [])))
        print(f"PASS: session valid. Notebook has {count} existing sources.")
        return 0
    except json.JSONDecodeError:
        print(f"FAIL: could not parse JSON response: {result.stdout[:200]}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
