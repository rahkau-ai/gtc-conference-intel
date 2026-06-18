#!/usr/bin/env python
"""
Stage 5 — Generate charts from master CSV. Charts are content-hashed (FM-22).

Reads master CSV (verifies SHA256 checksum first). Generates standard chart set.
Each PNG written with a companion <file>.sha256.
Writes charts_manifest.json for assemble.mjs pre-flight check.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    print("matplotlib not installed. Run: pip install matplotlib", file=sys.stderr)
    sys.exit(1)


def load_brand_tokens(skill_dir: Path) -> dict:
    tokens_path = skill_dir / "shared" / "brand_tokens.json"
    if tokens_path.exists():
        return json.loads(tokens_path.read_text())
    return {"colors": {"primary": "#0f52ba", "gold": "#F4A623"}}


def verify_checksum(csv_path: Path) -> bool:
    sha_path = csv_path.parent / f"{csv_path.name}.sha256"
    if not sha_path.exists():
        return False
    expected = sha_path.read_text().split()[0]
    actual = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    return expected == actual


def save_chart(fig: "plt.Figure", out_path: Path) -> str:
    fig.savefig(out_path, bbox_inches="tight", dpi=150)
    import matplotlib.pyplot as plt
    plt.close(fig)
    checksum = hashlib.sha256(out_path.read_bytes()).hexdigest()
    sha_path = out_path.parent / f"{out_path.name}.sha256"
    sha_path.write_text(f"{checksum}  {out_path.name}\n", encoding="utf-8")
    return checksum


def bar_chart(data: dict, title: str, xlabel: str, color: str, top_n: int = 15) -> "plt.Figure":
    import matplotlib.pyplot as plt
    items = sorted(data.items(), key=lambda x: x[1], reverse=True)[:top_n]
    if not items:
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.text(0.5, 0.5, "No data", ha="center", va="center")
        ax.set_title(title)
        return fig
    labels, values = zip(*items)
    fig, ax = plt.subplots(figsize=(10, max(4, len(labels) * 0.4)))
    bars = ax.barh(range(len(labels)), values, color=color, alpha=0.85)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel(xlabel, fontsize=10)
    ax.set_title(title, fontsize=12, fontweight="bold", pad=12)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for bar, val in zip(bars, values):
        ax.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                str(val), va="center", fontsize=8)
    fig.tight_layout()
    return fig


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master-csv", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--skill-dir", type=Path, default=Path(__file__).parent.parent)
    args = parser.parse_args()

    if not verify_checksum(args.master_csv):
        print(f"WARNING: SHA256 checksum missing or mismatch for {args.master_csv.name}. "
              "Charts will be generated but provenance is unverified.", file=sys.stderr)

    tokens = load_brand_tokens(args.skill_dir)
    colors = tokens.get("colors", {})
    PRIMARY = colors.get("primary", "#0f52ba")
    GOLD = colors.get("gold", "#F4A623")

    args.out_dir.mkdir(parents=True, exist_ok=True)

    with args.master_csv.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    total = len(rows)
    manifest: list[dict] = []

    charts = [
        ("modality", "Modality Breakdown", PRIMARY, 15, "modality_breakdown.png"),
        ("disease", "Top Disease Areas", PRIMARY, 20, "top_diseases.png"),
        ("organisation", "Top Organisations", GOLD, 20, "top_organisations.png"),
        ("geography", "Geographic Distribution", GOLD, 20, "geographic_distribution.png"),
        ("fact_type", "Fact Type Distribution", PRIMARY, 10, "fact_type_distribution.png"),
    ]

    SKIP_VALUES = {"UNKNOWN", "not_reported", "not_applicable", "unclear", "", "Unknown"}

    for field, title, color, top_n, filename in charts:
        counts = Counter(
            r.get(field) or "Unknown"
            for r in rows
            if (r.get(field) or "Unknown") not in SKIP_VALUES
        )
        fig = bar_chart(dict(counts), f"{title} (n={total})", "Abstracts", color, top_n=top_n)
        path = args.out_dir / filename
        sha = save_chart(fig, path)
        manifest.append({"file": filename, "title": title, "sha256": sha})
        print(f"  {filename} — {sha[:12]}")

    manifest_path = args.out_dir / "charts_manifest.json"
    manifest_path.write_text(
        json.dumps({"charts": manifest, "generated_from_csv": args.master_csv.name}, indent=2),
        encoding="utf-8"
    )
    print(f"\nStage 5 COMPLETE. {len(manifest)} charts → {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
