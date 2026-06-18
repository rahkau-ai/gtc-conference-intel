# gtc-conference-intel

**Trigger:** "run conference intel", "extract from abstracts", "gtc-conference-intel", "process conference PDF"

**What it does:** 8-stage pipeline — split conference abstract PDF → extract structured facts → master CSV → charts → branded PDF report. All critical quality gates are built in (count gates, schema gates, coverage warnings, citation validation).

**Full procedure:** Read `PROCEDURE.md` in this directory before executing.

## Quick start

```bash
# 1. Copy and fill config
cp config.example.json my-run/config.json
# edit config.json

# 2. Split PDF (Stage 1)
python stage1_split/split_pdf.py abstracts.pdf --config my-run/config.json --out-dir my-run/chunks

# 3. Extract facts (Stage 3 — via Workflow tool for large runs)
# Run stage3_extract/extract_workflow.mjs via Claude Code Workflow tool

# 4. Consolidate (Stage 4)
python stage4_consolidate/normalise.py --in-dir my-run/extracted --out-file my-run/tables/normalised.jsonl
python stage4_consolidate/consolidate.py --in-file my-run/tables/normalised.jsonl --out-csv my-run/tables/master.csv --config my-run/config.json
python stage4_consolidate/coverage_report.py --master-csv my-run/tables/master.csv --stage1-summary my-run/chunks/stage1_summary.json --config my-run/config.json

# 5. Charts (Stage 5)
python stage5_analyse/generate_charts.py --master-csv my-run/tables/master.csv --out-dir my-run/charts

# 6. Report (Stage 6)
python stage6_assemble/build_citations.py --master-csv my-run/tables/master.csv --out-file my-run/tables/citations.json
node stage6_assemble/assemble.mjs --config my-run/config.json --out-dir my-run/report-build
node stage6_assemble/render.mjs --config my-run/config.json --out-dir my-run/report-build

# 7. Distribute (Stage 7)
# Follow stage7_distribute/distribute_checklist.md
```

## Key design principles

- **Count from manifest, never from extraction** (the ASGCT 2026 bug fix)
- **Two-pass split** — Pass 1 detects N, Pass 2 must reproduce N exactly
- **Locked extraction prompt** (`prompts/extract_v1.txt`) — content-hashed per run
- **Every stage writes `stage{N}_summary.json`** — gates read this before proceeding
- **Single brand source** (`shared/brand_tokens.json`) — no more color drift
- **Feedback via GitHub issues** — no planning sessions to improve the pipeline
