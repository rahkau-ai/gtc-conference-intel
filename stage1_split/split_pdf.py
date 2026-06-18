#!/usr/bin/env python
"""
Stage 1 - Split a conference abstract PDF into chunks for fact extraction.

TWO-PASS APPROACH (FM-01 gate):
  Pass 1: Full-PDF scan. Records detected_count and known_gaps (abstract IDs with
           no parseable body text). Emits nothing.
  Pass 2: Chunked split. The sum of deduplicated abstract_ids across all chunks
           must equal detected_count exactly. Any mismatch = HARD GATE.

Also extracts plain text (.txt) alongside each chunk PDF so Stage 3 (Claude Code
fact extraction) can read text files directly without PyMuPDF dependency.

Usage:
  python split_pdf.py <input_pdf> --config <config.json> --out-dir <run_dir/chunks>

Run summary written to <out_dir>/stage1_summary.json.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF


@dataclass(frozen=True)
class AbstractHit:
    abstract_id: int
    title: str
    page_index: int
    has_body: bool  # True if substantial text follows the heading on same/next page


@dataclass(frozen=True)
class Chunk:
    index: int
    first_abstract_id: int
    last_abstract_id: int
    abstract_ids: tuple[int, ...]
    start_page_index: int
    end_page_index: int
    output_pdf: Path
    output_txt: Path


def clean_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip()


def detect_abstracts(doc: fitz.Document, heading_re: re.Pattern) -> tuple[list[AbstractHit], list[int]]:
    """Pass 1: scan entire PDF. Returns (hits, known_gaps).
    known_gaps = abstract IDs where the heading matched but no body text was found."""
    hits: list[AbstractHit] = []
    seen: set[int] = set()
    pages_text: list[str] = [doc.load_page(i).get_text("text") for i in range(doc.page_count)]

    for page_idx, text in enumerate(pages_text):
        for line in text.splitlines():
            m = heading_re.match(line)
            if not m:
                continue
            abstract_id = int(m.group("id"))
            title = clean_title(m.group("title"))
            if abstract_id in seen or len(title) < 20:
                continue
            seen.add(abstract_id)

            # Check body: look at remaining lines on this page + first lines of next page
            next_page_text = pages_text[page_idx + 1] if page_idx + 1 < len(pages_text) else ""
            body_window = "\n".join(text.splitlines()[text.splitlines().index(line) + 1:]) + "\n" + next_page_text
            # Body must have at least 100 characters of real text after the heading
            body_text = re.sub(r"\s+", " ", body_window[:2000]).strip()
            has_body = len(body_text) >= 100

            hits.append(AbstractHit(
                abstract_id=abstract_id,
                title=title,
                page_index=page_idx,
                has_body=has_body,
            ))

    hits.sort(key=lambda h: (h.page_index, h.abstract_id))
    known_gaps = [h.abstract_id for h in hits if not h.has_body]
    return hits, known_gaps


def build_chunks(
    hits: list[AbstractHit],
    max_abstracts: int,
    max_pages: int,
    total_pages: int,
    output_dir: Path,
    prefix: str,
) -> list[Chunk]:
    if not hits:
        raise ValueError("No abstract headings detected. Check --heading-regex or PDF quality.")

    chunks: list[Chunk] = []
    chunk_index = 1
    start = 0
    while start < len(hits):
        end_exclusive = min(start + max_abstracts, len(hits))
        if max_pages > 0:
            while end_exclusive > start + 1:
                next_start = hits[end_exclusive].page_index if end_exclusive < len(hits) else total_pages - 1
                span = next_start - hits[start].page_index + 1
                if span <= max_pages:
                    break
                end_exclusive -= 1

        group = hits[start:end_exclusive]
        next_group = hits[end_exclusive:end_exclusive + max_abstracts]
        first, last = group[0], group[-1]
        start_page = first.page_index
        end_page = next_group[0].page_index if next_group else total_pages - 1

        name = f"{prefix}_{first.abstract_id:04d}-{last.abstract_id:04d}_chunk_{chunk_index:04d}"
        chunks.append(Chunk(
            index=chunk_index,
            first_abstract_id=first.abstract_id,
            last_abstract_id=last.abstract_id,
            abstract_ids=tuple(h.abstract_id for h in group),
            start_page_index=start_page,
            end_page_index=end_page,
            output_pdf=output_dir / f"{name}.pdf",
            output_txt=output_dir / f"{name}.txt",
        ))
        chunk_index += 1
        start = end_exclusive
    return chunks


def write_chunk(input_pdf: Path, chunk: Chunk) -> None:
    src = fitz.open(input_pdf)
    out = fitz.open()
    out.insert_pdf(src, from_page=chunk.start_page_index, to_page=chunk.end_page_index)
    out.save(chunk.output_pdf)
    out.close()

    # Extract plain text for Stage 3 (Claude Code fact extraction)
    text_parts = []
    for page_idx in range(chunk.start_page_index, chunk.end_page_index + 1):
        text_parts.append(src.load_page(page_idx).get_text("text"))
    src.close()
    chunk.output_txt.write_text("\n\n".join(text_parts), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_pdf", type=Path)
    parser.add_argument("--config", type=Path, required=True, help="Path to config.json")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    cfg = json.loads(args.config.read_text())
    conf_cfg = cfg["conference"]
    split_cfg = cfg["splitting"]
    gates_cfg = cfg.get("gates", {})

    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    heading_re = re.compile(conf_cfg.get("heading_regex", r"^\s*(?P<id>\d{1,4})\s+(?P<title>[A-Z][^\n]{20,})"))
    official_count = conf_cfg.get("official_abstract_count")
    prefix = split_cfg.get("prefix", args.input_pdf.stem)
    max_abstracts = split_cfg.get("max_abstracts_per_chunk", 40)
    max_pages = split_cfg.get("max_pages_per_chunk", 40)

    print("Stage 1 - Pass 1: full PDF scan...")
    doc = fitz.open(args.input_pdf)
    total_pages = doc.page_count
    hits, known_gaps = detect_abstracts(doc, heading_re)
    doc.close()

    detected_count = len(hits)
    print(f"  Detected {detected_count} abstract headings; {len(known_gaps)} known gaps (no body text)")

    # HARD GATE: official count check (FM-04)
    if official_count and detected_count < 0.90 * official_count:
        print(f"\nHARD GATE FAIL: detected {detected_count} but official_abstract_count={official_count}. "
              f"Delta {100*(1 - detected_count/official_count):.1f}% > 10% tolerance.", file=sys.stderr)
        print("Check PDF quality, OCR, or heading_regex. Aborting.", file=sys.stderr)
        return 1

    print("Stage 1 - Pass 2: building chunks...")
    chunks = build_chunks(hits, max_abstracts, max_pages, total_pages, out_dir, prefix)

    for chunk in chunks:
        write_chunk(args.input_pdf, chunk)
        print(f"  Chunk {chunk.index:04d}: abstracts {chunk.first_abstract_id}-{chunk.last_abstract_id} "
              f"-> {chunk.output_txt.name}")

    # HARD GATE: chunk abstract IDs must reproduce detected_count exactly (FM-01)
    all_ids_in_chunks: set[int] = set()
    for chunk in chunks:
        all_ids_in_chunks.update(chunk.abstract_ids)
    reproduced_count = len(all_ids_in_chunks)

    if reproduced_count != detected_count:
        print(f"\nHARD GATE FAIL: detected {detected_count} abstracts in Pass 1 but chunks "
              f"contain {reproduced_count} unique IDs. Mismatch = {detected_count - reproduced_count}.", file=sys.stderr)
        return 1

    # Write manifest
    manifest_path = out_dir / f"{prefix}_split_manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "chunk_index", "first_abstract_id", "last_abstract_id",
            "abstract_count", "start_page", "end_page",
            "abstract_ids", "chunk_pdf", "chunk_txt",
        ])
        w.writeheader()
        for chunk in chunks:
            w.writerow({
                "chunk_index": chunk.index,
                "first_abstract_id": chunk.first_abstract_id,
                "last_abstract_id": chunk.last_abstract_id,
                "abstract_count": len(chunk.abstract_ids),
                "start_page": chunk.start_page_index + 1,
                "end_page": chunk.end_page_index + 1,
                "abstract_ids": ";".join(str(i) for i in chunk.abstract_ids),
                "chunk_pdf": str(chunk.output_pdf),
                "chunk_txt": str(chunk.output_txt),
            })

    # Write known_gaps file for Stage 3 gate
    known_gaps_path = out_dir / f"{prefix}_known_gaps.json"
    known_gaps_path.write_text(json.dumps({"known_gaps": known_gaps}), encoding="utf-8")

    summary = {
        "stage": "stage1_split",
        "input_pdf": str(args.input_pdf.resolve()),
        "detected_count": detected_count,
        "known_gaps_count": len(known_gaps),
        "known_gaps": known_gaps,
        "chunks_created": len(chunks),
        "official_abstract_count": official_count,
        "manifest": str(manifest_path),
        "known_gaps_file": str(known_gaps_path),
        "gate": "PASS",
        "gate_reason": None,
    }
    summary_path = out_dir / "stage1_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"\nStage 1 COMPLETE. Gate: PASS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
