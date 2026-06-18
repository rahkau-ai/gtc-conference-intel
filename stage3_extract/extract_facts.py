#!/usr/bin/env python
"""
Stage 3 — Extract structured facts from chunk .txt files using Claude Code skill.

Reads each chunk .txt file produced by Stage 1. For each abstract in the chunk,
calls Claude (via the Anthropic API or — preferred — the subscription model via
claude CLI) to extract JSON facts using the locked prompt at prompts/extract_v1.txt.

PARALLELISATION: This script can be called per-chunk and run in parallel via
the Workflow tool. The orchestration script is extract_workflow.mjs.

Hard gate (FM-12): After all chunks complete, extracted_count must equal
  manifest_count - len(known_gaps). Any shortfall = HARD GATE.

Usage:
  # Single chunk (called by Workflow tool):
  python extract_facts.py --chunk <path/to/chunk.txt> --config config.json --out-dir runs/asgct-2027/extracted

  # Gate check (called after all chunks complete):
  python extract_facts.py --validate-only --config config.json --stage1-dir runs/asgct-2027/chunks --out-dir runs/asgct-2027/extracted
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def get_prompt_hash(prompt_path: Path) -> str:
    return hashlib.sha256(prompt_path.read_bytes()).hexdigest()[:12]


def extract_from_chunk(
    chunk_txt: Path,
    prompt_path: Path,
    out_dir: Path,
    config: dict,
) -> tuple[bool, list[dict], str]:
    """Extract facts from one chunk .txt file. Returns (success, facts, error)."""
    prompt_template = prompt_path.read_text(encoding="utf-8")
    chunk_text = chunk_txt.read_text(encoding="utf-8")

    # Build the extraction prompt
    full_prompt = f"""{prompt_template}

---

ABSTRACT TEXT TO EXTRACT FROM:

{chunk_text}

Return only a valid JSON array. No prose before or after."""

    # Call claude CLI (subscription model — no API key needed)
    cmd = ["claude", "--print", "--no-markdown", full_prompt]
    try:
        result = subprocess.run(cmd, text=True, capture_output=True, timeout=300)
    except subprocess.TimeoutExpired:
        return False, [], "claude CLI timed out"
    except FileNotFoundError:
        return False, [], "claude CLI not found — ensure Claude Code is installed"

    if result.returncode != 0:
        return False, [], f"claude CLI error: {result.stderr[:200]}"

    raw = result.stdout.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        facts = json.loads(raw)
        if not isinstance(facts, list):
            return False, [], f"expected JSON array, got {type(facts).__name__}"
    except json.JSONDecodeError as e:
        return False, [], f"JSON parse error: {e} — raw[:200]: {raw[:200]}"

    # Add provenance fields
    source_id = chunk_txt.name
    prompt_hash = get_prompt_hash(prompt_path)
    for fact in facts:
        fact.setdefault("source_id", source_id)
        fact.setdefault("prompt_hash", prompt_hash)
        fact.setdefault("schema_version", "v1")

    # Write output
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / chunk_txt.stem.replace(".txt", "") / ".jsonl"
    out_file = out_dir / f"{chunk_txt.stem}_facts.jsonl"
    with out_file.open("w", encoding="utf-8") as f:
        for fact in facts:
            f.write(json.dumps(fact) + "\n")

    return True, facts, ""


def validate_coverage(stage1_dir: Path, out_dir: Path) -> tuple[str, str, dict]:
    """FM-12 gate: extracted_count == manifest_count - len(known_gaps)."""
    summary_path = stage1_dir / "stage1_summary.json"
    if not summary_path.exists():
        return "HARD_FAIL", "stage1_summary.json not found", {}

    stage1 = json.loads(summary_path.read_text())
    manifest_count = stage1["detected_count"]
    known_gaps = stage1.get("known_gaps", [])

    # Count extracted facts
    extracted_files = list(out_dir.glob("*_facts.jsonl"))
    extracted_abstract_ids: set[str] = set()
    total_facts = 0
    for f in extracted_files:
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                fact = json.loads(line)
                extracted_abstract_ids.add(str(fact.get("abstract_id", "")))
                total_facts += 1

    extracted_count = len(extracted_abstract_ids)
    expected_count = manifest_count - len(known_gaps)

    if extracted_count < expected_count:
        shortfall = expected_count - extracted_count
        return "HARD_FAIL", f"extracted {extracted_count} abstract IDs but expected {expected_count} (manifest {manifest_count} - known_gaps {len(known_gaps)} = {expected_count}). Shortfall: {shortfall}", {
            "manifest_count": manifest_count,
            "known_gaps_count": len(known_gaps),
            "expected_count": expected_count,
            "extracted_count": extracted_count,
            "total_facts": total_facts,
            "shortfall": shortfall,
        }

    return "PASS", None, {
        "manifest_count": manifest_count,
        "known_gaps_count": len(known_gaps),
        "expected_count": expected_count,
        "extracted_count": extracted_count,
        "total_facts": total_facts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunk", type=Path, help="Single chunk .txt file to process")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true", help="Run gate check only (no extraction)")
    parser.add_argument("--stage1-dir", type=Path, help="Required for --validate-only")
    args = parser.parse_args()

    cfg = json.loads(args.config.read_text())
    prompt_path = Path(args.config.parent) / cfg["extraction"]["prompt_file"]

    if args.validate_only:
        if not args.stage1_dir:
            print("--stage1-dir required with --validate-only", file=sys.stderr)
            return 1
        gate, reason, stats = validate_coverage(args.stage1_dir, args.out_dir.resolve())
        summary = {"stage": "stage3_extract_validation", "gate": gate, "gate_reason": reason, **stats}
        summary_path = args.out_dir / "stage3_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2))
        if gate == "HARD_FAIL":
            print(f"\nHARD GATE FAIL: {reason}", file=sys.stderr)
            return 1
        print(f"\nStage 3 gate: PASS. {stats.get('total_facts', 0)} facts extracted from {stats.get('extracted_count', 0)} abstracts.")
        return 0

    if not args.chunk:
        print("--chunk required (or use --validate-only)", file=sys.stderr)
        return 1

    prompt_hash = get_prompt_hash(prompt_path)
    print(f"Extracting from {args.chunk.name} (prompt hash: {prompt_hash})")
    ok, facts, error = extract_from_chunk(args.chunk, prompt_path, args.out_dir.resolve(), cfg)

    if not ok:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    print(f"OK: {len(facts)} facts extracted → {args.out_dir}/{args.chunk.stem}_facts.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
