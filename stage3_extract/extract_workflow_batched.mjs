/**
 * Stage 3 — Batched fact extraction: 10 agents × ~10 chunks each.
 *
 * Use this if extract_workflow.mjs hits session limits.
 * Each agent processes a batch of chunk files sequentially, keeping
 * concurrent API calls to ≤10 (vs 94 in the unbatched version).
 *
 * Re-run safe: agents check for existing *_facts.jsonl before re-extracting.
 */
export const meta = {
  name: "gtc-conference-intel-extract-batched",
  description: "Stage 3 batched: 10 agents × 9-10 chunks each (low concurrency)",
  phases: [
    { title: "Setup",   detail: "Find chunk files" },
    { title: "Extract", detail: "10 agents × batch of 9-10 chunks" },
    { title: "Gate",    detail: "FM-12 coverage check" },
  ],
};

const SKILL    = "C:/Users/Me/.claude/skills/gtc-conference-intel";
const S1_DIR   = `${SKILL}/runs/asgct-2026/chunks`;
const OUT_DIR  = `${SKILL}/runs/asgct-2026/extracted`;
const CFG_PATH = `${SKILL}/runs/asgct-2026/config.json`;

const SCHEMA_RULES = `
SCHEMA version: v1

Required (always fill or use "UNKNOWN"):
  abstract_id   — abstract number from the programme (e.g. "1234")
  source_id     — the .txt chunk filename (not the full path)
  source_type   — "pdf_abstract"
  fact_type     — finding | event | announcement | datapoint | claim
  subject       — who/what the fact is about (company, gene, therapy, institution)
  what          — 1-2 sentences: what happened or was found
  evidence_quote— verbatim ≤25-word substring from the source. Must be a literal quote. If none, set confidence="low".
  citation      — "Abstract <id>. <title>. ASGCT 2026."
  confidence    — high | medium | low
  schema_version— "v1"

Conditional (required only when fact_type == "datapoint"):
  quant_value, quant_unit, quant_context

Optional (null if absent):
  modality      — gene_therapy | gene_editing | cell_therapy | mRNA | other | null
  disease, organisation, geography

RULES:
1. One JSON object per distinct factual claim.
2. Never merge facts from different abstracts.
3. evidence_quote MUST be verbatim. If invented, set confidence="low".
4. For datapoint facts: quant_value, quant_unit, quant_context all required.
5. Unknown required fields → "UNKNOWN". Unknown optional fields → null.
`.trim();

// ── Setup ─────────────────────────────────────────────────────────────────
phase("Setup");
const setup = await agent(
  `List all files matching *_chunk_*.txt in: ${S1_DIR}
   Also ensure output directory exists: ${OUT_DIR}
   Return { "files": ["<abs-path>", ...], "out_dir_ready": true }`,
  { schema: { type: "object", properties: { files: { type: "array", items: { type: "string" } } }, required: ["files"] } }
);

if (!setup?.files?.length) {
  log("ERROR: No chunk .txt files found");
  return { gate: "HARD_FAIL", reason: "No chunks in " + S1_DIR };
}

// Divide into batches of ~10 chunks
const BATCH_SIZE = 10;
const batches = [];
for (let i = 0; i < setup.files.length; i += BATCH_SIZE) {
  batches.push(setup.files.slice(i, i + BATCH_SIZE));
}
log(`${setup.files.length} chunks → ${batches.length} batches of up to ${BATCH_SIZE}. Launching...`);

// ── Extract (batched) ─────────────────────────────────────────────────────
phase("Extract");

const batchResults = await pipeline(
  batches,
  (batch, _, batchIdx) => {
    const batchLabel = `batch-${String(batchIdx + 1).padStart(2, "0")}`;

    // Build per-file instructions
    const fileList = batch
      .map((p) => {
        const name = p.replace(/\\/g, "/").split("/").pop();
        return `  File: ${p}\n  Output: ${OUT_DIR}/${name.replace(".txt", "_facts.jsonl")}`;
      })
      .join("\n\n");

    return agent(
      `You are a structured fact-extraction agent for ASGCT 2026 (gene therapy conference).
Process each file below IN ORDER. For each:
1. Check if the output JSONL already exists — if yes, skip (idempotent re-run).
2. Read the source .txt file.
3. Find every abstract (lines starting with a number followed by an uppercase title).
4. Extract 1–5 key factual claims per abstract as JSON objects.
5. Write all facts for this file as JSONL (one JSON object per line, UTF-8) to the output path.

${SCHEMA_RULES}

Set source_id = the .txt filename (not the full path) for every fact you extract.

FILES TO PROCESS (${batch.length} files):
${fileList}

After processing all files, return:
  total_facts: total facts written across all files
  files_processed: count of files processed (not skipped)
  files_skipped: count already-existing JSONL files skipped
  errors: list of any file-level errors`,
      {
        label: `${batchLabel}:${batch.length}chunks`,
        phase: "Extract",
        schema: {
          type: "object",
          properties: {
            success:         { type: "boolean" },
            total_facts:     { type: "number" },
            files_processed: { type: "number" },
            files_skipped:   { type: "number" },
            errors:          { type: "array", items: { type: "string" } },
          },
          required: ["success", "total_facts"],
        },
      }
    );
  }
);

const totalFacts    = batchResults.filter(Boolean).reduce((s, r) => s + (r.total_facts || 0), 0);
const totalSkipped  = batchResults.filter(Boolean).reduce((s, r) => s + (r.files_skipped || 0), 0);
const batchErrors   = batchResults.filter(Boolean).flatMap((r) => r.errors || []);
log(`Batched extraction done: ${totalFacts} facts, ${totalSkipped} skipped, ${batchErrors.length} errors`);

// ── FM-12 Gate ─────────────────────────────────────────────────────────────
phase("Gate");
const gate = await agent(
  `Run: python "${SKILL}/stage3_extract/extract_facts.py" --validate-only --config "${CFG_PATH}" --stage1-dir "${S1_DIR}" --out-dir "${OUT_DIR}"
Return the parsed JSON output.`,
  {
    label: "gate:fm-12",
    phase: "Gate",
    schema: {
      type: "object",
      properties: {
        gate:            { type: "string" },
        extracted_count: { type: ["number", "null"] },
        expected_count:  { type: ["number", "null"] },
        total_facts:     { type: ["number", "null"] },
        gate_reason:     { type: ["string", "null"] },
      },
      required: ["gate"],
    },
  }
);

if (!gate || gate.gate === "HARD_FAIL") {
  log(`HARD GATE FAIL: ${gate?.gate_reason}`);
  return { gate: "HARD_FAIL", reason: gate?.gate_reason, totalFacts, batchErrors };
}

log(`Stage 3 COMPLETE — Gate PASS. ${gate.total_facts} facts / ${gate.extracted_count} abstracts (expected ${gate.expected_count})`);
return {
  gate:            "PASS",
  total_facts:     gate.total_facts,
  extracted_count: gate.extracted_count,
  expected_count:  gate.expected_count,
  batches_run:     batches.length,
  batch_errors:    batchErrors,
};
