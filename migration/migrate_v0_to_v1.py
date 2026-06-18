#!/usr/bin/env python3
"""Migrate ASGCT 2026 master CSV (44-col old schema) to universal schema v1 JSONL.

Usage:
  python migration/migrate_v0_to_v1.py \\
    --in-csv "C:/Users/Me/Desktop/GTC/research/asgct-2026-abstracts/tables/ASGCT_2026_Master_Combined.csv" \\
    --out-jsonl runs/asgct-2026/migrated_v0_facts.jsonl
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

MODALITY_PATTERNS = [
    (r"AAV|adeno.associated|lentivir|retrovir|viral.vector", "gene_therapy"),
    (r"CRISPR|Cas9|base.edit|prime.edit|zinc.finger|TALEN", "gene_editing"),
    (r"\bmRNA\b|lipid.nanoparticle|LNP", "mRNA"),
    (r"CAR.T|CAR.NK|CAR T|cell therap|TIL|T.cell", "cell_therapy"),
]


def normalize_modality(raw: str):
    if not raw or raw.strip().lower() in ("", "unknown", "not_reported", "n/a"):
        return None
    for pattern, modality in MODALITY_PATTERNS:
        if re.search(pattern, raw, re.IGNORECASE):
            return modality
    return "other"


def truncate_to_25_words(text: str) -> str:
    words = text.strip().split()
    return " ".join(words[:25])


def migrate_row(row: dict) -> dict:
    abstract_id = row.get("abstract_id", "").strip()
    source_id = row.get("source_pdf", "").strip() or f"ASGCT2026_abstract_{abstract_id}"

    raw_snippet = row.get("evidence_snippet", "").strip()
    evidence_quote = truncate_to_25_words(raw_snippet) if raw_snippet else "UNKNOWN"

    raw_confidence = row.get("confidence", "").strip().lower()
    confidence = raw_confidence if raw_confidence in ("high", "medium", "low") else "low"
    if not raw_snippet:
        confidence = "low"

    modality = normalize_modality(row.get("delivery_modalities", ""))

    is_industry = row.get("academic_vs_industry", "").lower() not in ("academic", "")
    sponsor = row.get("company_or_sponsor", "").strip()
    first_author = row.get("first_author_name", "").strip()
    institutions = row.get("institutions_raw", "")[:60].strip()

    if is_industry and sponsor:
        subject = sponsor
    elif first_author:
        subject = f"{first_author} / {institutions}" if institutions else first_author
    else:
        subject = sponsor or "UNKNOWN"

    what = row.get("most_important_author_conclusion", "").strip()
    if not what:
        what = row.get("relevance_notes", "").strip() or "UNKNOWN"

    quant_value = None
    quant_unit = None
    quant_context = None
    if row.get("has_dose_data", "").strip().lower() == "yes":
        try:
            quant_value = float(row.get("dose_value_numeric", "") or "")
        except (ValueError, TypeError):
            pass
        raw_unit = row.get("dose_unit", "").strip()
        quant_unit = raw_unit if raw_unit and raw_unit.lower() not in ("", "not_reported") else None
        rel_notes = row.get("relevance_notes", "").strip()
        quant_context = rel_notes[:200] if rel_notes else None

    citation = f"{first_author} et al., ASGCT 2026, Abstract {abstract_id}" if first_author else f"ASGCT 2026, Abstract {abstract_id}"

    return {
        "abstract_id": abstract_id,
        "source_id": source_id,
        "source_type": "pdf_abstract",
        "fact_date": "2026-05-13",
        "fact_type": "finding",
        "subject": subject,
        "what": what,
        "quant_value": quant_value,
        "quant_unit": quant_unit,
        "quant_context": quant_context,
        "modality": modality,
        "disease": row.get("disease_area", "").strip() or None,
        "organisation": sponsor or None,
        "geography": row.get("geographic_origin", "").strip() or None,
        "evidence_quote": evidence_quote,
        "citation": citation,
        "confidence": confidence,
        "schema_version": "v0-legacy",
    }


def main():
    parser = argparse.ArgumentParser(description="Migrate old 44-col CSV → schema v1 JSONL")
    parser.add_argument("--in-csv", required=True, help="Path to ASGCT_2026_Master_Combined.csv")
    parser.add_argument("--out-jsonl", required=True, help="Output JSONL path")
    parser.add_argument("--dry-run", action="store_true", help="Print first 3 rows, don't write")
    args = parser.parse_args()

    in_path = Path(args.in_csv)
    if not in_path.exists():
        print(f"ERROR: {in_path} not found", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.out_jsonl)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    migrated = skipped = low_confidence = 0
    rows_preview = []

    with open(in_path, encoding="utf-8") as f_in:
        reader = csv.DictReader(f_in)
        rows = list(reader)

    with open(out_path, "w", encoding="utf-8") as f_out:
        for row in rows:
            if not row.get("abstract_id", "").strip():
                skipped += 1
                continue
            fact = migrate_row(row)
            if fact["confidence"] == "low":
                low_confidence += 1
            if args.dry_run:
                rows_preview.append(fact)
                if len(rows_preview) >= 3:
                    break
                continue
            f_out.write(json.dumps(fact) + "\n")
            migrated += 1

    if args.dry_run:
        print("DRY RUN — first 3 rows:")
        for r in rows_preview:
            print(json.dumps(r, indent=2))
        return

    print(f"Migrated : {migrated} rows  →  {out_path}")
    print(f"Skipped  : {skipped} (no abstract_id)")
    print(f"Low conf : {low_confidence} rows (evidence_quote missing or confidence unknown)")


if __name__ == "__main__":
    main()
