# gtc-conference-intel — Procedure

8-stage pipeline for extracting structured intelligence from conference abstract PDFs.

---

## Before you start

1. Copy `config.example.json` to your run directory as `config.json`
2. Fill in all required fields (see comments in example)
3. Install dependencies: `pip install -r requirements.txt`

---

## Stage 0 — Configure [HUMAN GATE]

Edit `config.json`:
- `conference.name`, `year`, `label`
- `conference.pdf_path` — absolute path to the abstract PDF
- `conference.official_abstract_count` — from the conference website
- `conference.heading_regex` — test against first 20 pages to confirm it matches
- `notebooklm.notebook_id` — needed only for Stage 2 (NLM upload) and Stage 6 deep research
- `assembly.logo_path` — path to GTC logo PNG

Verify heading regex catches abstracts (open PDF, find first abstract heading, test regex):
```bash
python -c "import re; print(bool(re.match(r'your_regex', 'your heading line here')))"
```

Gate: all required config fields filled → proceed to Stage 1.

---

## Stage 1 — Split [AUTO + HARD GATE]

```bash
python stage1_split/split_pdf.py \
  <path/to/abstracts.pdf> \
  --config config.json \
  --out-dir runs/<conference-year>/chunks
```

**What it does:**
- Pass 1: Scans full PDF, detects N abstract headings, builds `known_gaps` list
- Pass 2: Creates chunk PDFs + `.txt` text extracts

**Hard gate (FM-01):** Chunk abstract IDs must equal detected_count exactly (100%).
**Hard gate (FM-04):** `detected_count >= 0.90 × official_abstract_count`.

If gate fails: check PDF quality, OCR, or adjust `heading_regex` in config.

Output: `runs/<conference-year>/chunks/`
- `*_split_manifest.csv` — one row per chunk
- `*_known_gaps.json` — abstract IDs with no parseable body
- `stage1_summary.json` — gate result
- `*.pdf` + `*.txt` per chunk

---

## Stage 2 — Ingest [AUTO + HARD GATE] *(optional — needed only for Stage 6 NLM deep research)*

Check session first:
```bash
python stage2_ingest/check_session.py --config config.json
```

If session check fails: re-auth via `inject-cookies.py` then retry.

Upload chunks:
```bash
python stage2_ingest/ingest_batches.py \
  --config config.json \
  --stage1-dir runs/<conference-year>/chunks
```

Resume from a specific chunk: `--start-chunk <N>`

**Hard gate (FM-06, FM-07, FM-08):** Any failed upload after 3 retries → abort.

---

## Stage 3 — Extract [AUTO + HARD GATE]

**Option A — Workflow tool (recommended for large runs, 100+ chunks):**

In a Claude Code session, run the Workflow tool with:
```
scriptPath: ~/.claude/skills/gtc-conference-intel/stage3_extract/extract_workflow.mjs
args: {
  stage1_dir: "runs/<conference-year>/chunks",
  out_dir: "runs/<conference-year>/extracted",
  config_path: "config.json",
  skill_dir: "~/.claude/skills/gtc-conference-intel"
}
```

**Option B — Single chunk (for testing/reruns):**
```bash
python stage3_extract/extract_facts.py \
  --chunk runs/<conference-year>/chunks/<chunk>.txt \
  --config config.json \
  --out-dir runs/<conference-year>/extracted
```

**Validate (FM-12):**
```bash
python stage3_extract/extract_facts.py \
  --validate-only \
  --config config.json \
  --stage1-dir runs/<conference-year>/chunks \
  --out-dir runs/<conference-year>/extracted
```

**Hard gate (FM-12):** `extracted_abstract_count == manifest_count - len(known_gaps)`. Any shortfall blocks.

Output: `runs/<conference-year>/extracted/` — `*_facts.jsonl` per chunk + `stage3_summary.json`

---

## Stage 4 — Consolidate [AUTO]

```bash
# Normalise
python stage4_consolidate/normalise.py \
  --in-dir runs/<conference-year>/extracted \
  --out-file runs/<conference-year>/tables/normalised_facts.jsonl

# Dedup + master CSV + checksum
python stage4_consolidate/consolidate.py \
  --in-file runs/<conference-year>/tables/normalised_facts.jsonl \
  --out-csv runs/<conference-year>/tables/master_facts.csv \
  --config config.json

# Coverage report (FM-15)
python stage4_consolidate/coverage_report.py \
  --master-csv runs/<conference-year>/tables/master_facts.csv \
  --stage1-summary runs/<conference-year>/chunks/stage1_summary.json \
  --config config.json
```

**Soft gate (FM-15):** If gap% > 10%, pipeline emits SOFT_WARNING (exit code 2).
To sign off: edit `coverage_report.json`, set `"signed_off_by": "your name"`, then proceed.

**Hard gate (FM-15):** If gap% > 30%, pipeline exits with error.

Output: `runs/<conference-year>/tables/master_facts.csv` + `master_facts.csv.sha256`

---

## Stage 5 — Analyse [AUTO]

```bash
python stage5_analyse/generate_charts.py \
  --master-csv runs/<conference-year>/tables/master_facts.csv \
  --out-dir runs/<conference-year>/charts \
  --skill-dir ~/.claude/skills/gtc-conference-intel
```

Charts are content-hashed (FM-22). `charts_manifest.json` is consumed by assemble.mjs.

Optional: run Streamlit dashboard:
```bash
streamlit run stage5_analyse/dashboard.py -- \
  --master-csv runs/<conference-year>/tables/master_facts.csv
```

---

## Stage 6 — Assemble [HUMAN GATE]

**Step 6a — Build citations.json (FM-25):**
```bash
python stage6_assemble/build_citations.py \
  --master-csv runs/<conference-year>/tables/master_facts.csv \
  --out-file runs/<conference-year>/tables/citations.json
```

**Step 6b — Write/review report draft:**
Write your report in Markdown at the path set in `config.assembly.report_draft`.
Use `[[CHART:filename.png|caption]]` to embed charts.
Use `{{cite:abstract_id}}` for inline citations.

**Step 6c — Assemble HTML (pre-flight checks run automatically):**
```bash
node stage6_assemble/assemble.mjs \
  --config config.json \
  --out-dir runs/<conference-year>/report-build
```

Pre-flight checks (FM-23, FM-24, FM-25): all chart files + cite IDs must resolve. Pipeline aborts on failure.

**Step 6d — Render PDF (FM-26):**
```bash
node stage6_assemble/render.mjs \
  --config config.json \
  --out-dir runs/<conference-year>/report-build \
  --version v1.0
```

Post-render gate: PDF > 500KB.

Human gate: review the PDF before proceeding to distribution.

---

## Stage 7 — Distribute [HUMAN GATE]

Follow `stage7_distribute/distribute_checklist.md` — every checkbox before announcing.

Key checks (FM-28, FM-29, FM-30):
- Drive file permissions: "Anyone with link can view"
- Env var `DRIVE_FILE_<CONFERENCE>_<YEAR>` set in Netlify
- Smoke test from incognito window
- Test form submission → confirmation email received

---

## Resume from a specific stage

```bash
# Stage 1 re-run with a different regex
python stage1_split/split_pdf.py ... --config config.json

# Stage 2 resume from chunk 120
python stage2_ingest/ingest_batches.py ... --start-chunk 120

# Stage 3 re-run one specific chunk
python stage3_extract/extract_facts.py --chunk <chunk>.txt --config config.json --out-dir ...

# Stage 3 validation only
python stage3_extract/extract_facts.py --validate-only ...
```

---

## Improving the pipeline (feedback loop)

After a run, if you spot any issue:
1. File a GitHub issue at github.com/rahkau-ai/gtc-conference-intel with label:
   `schema` · `gate` · `extraction` · `entity` · `prompt`
2. Include: abstract_id, source_id, what was wrong, what it should be
3. Fix = update locked prompt / schema / gate script → PR → merge → new version tag
4. Re-extract only flagged rows: `python stage3_extract/extract_facts.py --chunk <specific_chunk>.txt ...`
5. Close issue when fixed rows verify correctly

No planning sessions for improvements — only GitHub issues.

---

## Versioning

- Pipeline version: semver (e.g. `v2.0.0`)
- Conference tags: `v2.0.0-asgct2027` on the exact commit used for each conference
- `prompts/extract_v1.txt` is the locked extraction prompt — do not edit mid-run
