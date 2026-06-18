# GTC Conference Intel Pipeline

8-stage pipeline for turning a conference abstract PDF into a structured intelligence database and branded PDF report.

Built from the lessons of the ASGCT 2026 pipeline — every failure mode documented, gated, and fixed.

## What it solves

- **Count drift:** Abstract count always comes from the PDF splitter manifest, never from extraction row counts
- **Silent data loss:** Hard gates block the pipeline if extraction is incomplete
- **Schema drift:** Locked extraction prompt (`prompts/extract_v1.txt`), content-hashed per run
- **Visual inconsistency:** Single brand token source (`shared/brand_tokens.json`)
- **Missing citations:** `citations.json` built from master CSV; every `{{cite:NNN}}` validated before HTML build
- **Broken chart references:** Pre-flight check verifies all `[[CHART:file.png]]` exist before rendering

## Usage

See [PROCEDURE.md](PROCEDURE.md) for the full 8-stage walkthrough.
See [SKILL.md](SKILL.md) for the Claude Code skill invocation.

## Versioning

- Semver: `v2.0.0`
- Conference tags: `v2.0.0-asgct2027` per conference run
- Prompt version tracked in `schema_version` field of every extracted row

## Feedback loop

File a GitHub issue (labels: `schema`, `gate`, `extraction`, `entity`, `prompt`) → fix → PR → new tag.
No planning sessions — only issues.

## Requirements

```bash
pip install -r requirements.txt          # Python scripts
npm install playwright                    # Stage 6 render
```
