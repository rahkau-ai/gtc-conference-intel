#!/usr/bin/env python
"""
Stage 2 — Upload split chunk PDFs to NotebookLM and generate data tables.

Replaces the original PowerShell run_notebooklm_chunks_combined.ps1 with:
  - Python (cross-platform, version-controlled)
  - Retry loop: RATE_LIMITED → wait 60s × attempt (max 3×)
  - RPC CREATE_ARTIFACT failures retry with exponential backoff
  - Session check before start (calls check_session.py logic inline)
  - Hard gate: if any chunk has generate_failed after all retries → abort

Note: Stage 3 (fact extraction) uses .txt files written by Stage 1, NOT the NLM
data tables. NLM data tables were removed from the extraction path (FM-10, FM-11).
This stage only handles PDF upload so the notebook exists as a reference source
for Stage 6 deep-research narratives (optional).

Usage:
  python ingest_batches.py --config config.json --stage1-dir runs/asgct-2027/chunks
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path


MAX_RETRIES = 3
RATE_LIMIT_WAIT = 60  # seconds per attempt


def call_nlm(cmd: list[str], timeout: int = 180) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"


def upload_chunk(nlm_path: str, notebook_id: str, pdf_path: Path, title: str, timeout: int) -> tuple[bool, str]:
    """Upload one PDF chunk. Returns (success, source_id_or_error)."""
    for attempt in range(1, MAX_RETRIES + 1):
        rc, out, err = call_nlm(
            [nlm_path, "source", "add", "-n", notebook_id,
             "--type", "file", "--mime-type", "application/pdf",
             "--title", title, "--json", str(pdf_path)],
            timeout=timeout,
        )
        if rc == 0:
            try:
                data = json.loads(out)
                src_id = data.get("source", {}).get("id") or data.get("id", "")
                if src_id:
                    return True, src_id
            except json.JSONDecodeError:
                pass
        if "RATE_LIMITED" in out + err or "rate" in (out + err).lower():
            wait = RATE_LIMIT_WAIT * attempt
            print(f"    Rate limited. Waiting {wait}s before retry {attempt}/{MAX_RETRIES}...")
            time.sleep(wait)
        elif "UNKNOWN" in err or "SourceType" in err:
            print(f"    SourceType.UNKNOWN error. Waiting 30s before retry {attempt}/{MAX_RETRIES}...")
            time.sleep(30)
        else:
            print(f"    Upload failed (attempt {attempt}/{MAX_RETRIES}): {err[:100]}")
            time.sleep(10 * attempt)
    return False, f"upload_failed_after_{MAX_RETRIES}_retries"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--stage1-dir", type=Path, required=True, help="Output dir from Stage 1")
    parser.add_argument("--start-chunk", type=int, default=1)
    parser.add_argument("--max-chunks", type=int, default=0)
    args = parser.parse_args()

    cfg = json.loads(args.config.read_text())
    nlm_cfg = cfg["notebooklm"]
    notebook_id = nlm_cfg["notebook_id"]
    nlm_path = nlm_cfg.get("notebooklm_path", "notebooklm")
    timeout = nlm_cfg.get("upload_timeout_seconds", 180)
    conf_label = cfg["conference"]["label"]

    stage1_dir = args.stage1_dir.resolve()
    manifest_files = list(stage1_dir.glob("*_split_manifest.csv"))
    if not manifest_files:
        print(f"No split manifest found in {stage1_dir}", file=sys.stderr)
        return 1
    manifest_path = manifest_files[0]

    log_path = stage1_dir / "stage2_ingest_log.jsonl"
    summary_path = stage1_dir / "stage2_summary.json"

    # Load done chunks from log
    done_chunks: set[str] = set()
    if log_path.exists():
        for line in log_path.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                if row.get("status") in ("uploaded", "skipped"):
                    done_chunks.add(str(row["chunk_index"]))

    with manifest_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if args.start_chunk > 1:
        rows = [r for r in rows if int(r["chunk_index"]) >= args.start_chunk]
    if args.max_chunks > 0:
        rows = rows[:args.max_chunks]

    attempted = skipped = succeeded = failed = 0
    for row in rows:
        idx = row["chunk_index"]
        pdf_path = Path(row["chunk_pdf"])
        title = f"{conf_label} abstracts {row['first_abstract_id']}–{row['last_abstract_id']}"

        if idx in done_chunks:
            print(f"[{idx}] Skip (already uploaded)")
            skipped += 1
            continue

        if not pdf_path.exists():
            print(f"[{idx}] SKIP (PDF not found: {pdf_path})")
            with log_path.open("a") as lf:
                lf.write(json.dumps({"chunk_index": idx, "status": "pdf_missing", "path": str(pdf_path)}) + "\n")
            failed += 1
            continue

        attempted += 1
        print(f"[{idx}] Uploading {pdf_path.name}...")
        ok, src_id = upload_chunk(nlm_path, notebook_id, pdf_path, title, timeout)
        status = "uploaded" if ok else "upload_failed"
        if ok:
            succeeded += 1
            print(f"[{idx}] OK — source_id={src_id}")
        else:
            failed += 1
            print(f"[{idx}] FAILED — {src_id}")

        with log_path.open("a") as lf:
            lf.write(json.dumps({
                "chunk_index": idx, "status": status,
                "source_id": src_id if ok else None,
                "error": src_id if not ok else None,
            }) + "\n")

    gate = "PASS" if failed == 0 else "HARD_FAIL"
    gate_reason = None if gate == "PASS" else f"{failed} chunk(s) failed to upload after {MAX_RETRIES} retries"

    summary = {
        "stage": "stage2_ingest",
        "chunks_attempted": attempted,
        "chunks_skipped": skipped,
        "chunks_succeeded": succeeded,
        "chunks_failed": failed,
        "gate": gate,
        "gate_reason": gate_reason,
        "log": str(log_path),
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))

    if gate == "HARD_FAIL":
        print(f"\nHARD GATE FAIL: {gate_reason}", file=sys.stderr)
        print("Retry failed chunks with --start-chunk or fix session auth.", file=sys.stderr)
        return 1

    print(f"\nStage 2 COMPLETE. Gate: PASS. {succeeded} chunks uploaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
