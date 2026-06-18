/**
 * Stage 3 — Parallel fact extraction via Claude Code Workflow tool.
 *
 * Each agent reads a chunk .txt file directly and extracts facts using the
 * v1 schema. No Python subprocess → claude CLI needed: the agents ARE Claude.
 * After all chunks complete, runs the FM-12 gate via extract_facts.py --validate-only.
 *
 * Invocation: Workflow tool with this scriptPath.
 * No args needed — paths are hardcoded for the ASGCT-2026 run.
 */
export const meta = {
  name: "gtc-conference-intel-extract",
  description: "Stage 3: parallel fact extraction from ASGCT 2026 abstract chunks",
  phases: [
    { title: "Setup", detail: "Find chunk files, create output directory" },
    { title: "Extract", detail: "One agent per chunk — read text, extract facts, write JSONL" },
    { title: "Gate", detail: "FM-12: extracted abstract IDs == manifest - known_gaps" },
  ],
};

// ── Config ────────────────────────────────────────────────────────────────
const SKILL    = "C:/Users/Me/.claude/skills/gtc-conference-intel";
const S1_DIR   = `${SKILL}/runs/asgct-2026/chunks`;
const OUT_DIR  = `${SKILL}/runs/asgct-2026/extracted`;
const CFG_PATH = `${SKILL}/runs/asgct-2026/config.json`;

// ── Embedded schema (from prompts/extract_v1.txt — DO NOT alter mid-run) ─
const SCHEMA_RULES = `
SCHEMA version: v1

Required (always fill or use "UNKNOWN"):
  abstract_id   string  — abstract number from the programme (e.g. "1234")
  source_id     string  — the .txt chunk filename (not the full path)
  source_type   "pdf_abstract"
  fact_type     finding | event | announcement | datapoint | claim
  subject       string  — who/what the fact is about (company, gene, therapy, institution)
  what          string  — 1-2 sentences: what happened or was found
  evidence_quote string — verbatim ≤25-word substring from the source text. MUST be a literal quote. If none found, set confidence = "low".
  citation      string  — "Abstract <id>. <title>. ASGCT 2026."
  confidence    high | medium | low
  schema_version "v1"

Conditional (required only when fact_type == "datapoint"):
  quant_value   number
  quant_unit    string  — e.g. "patients", "vg/kg", "% response rate"
  quant_context string  — what the number means

Optional (null if absent):
  modality      gene_therapy | gene_editing | cell_therapy | mRNA | other | null
  disease       string
  organisation  string
  geography     string

RULES:
1. One JSON object per distinct factual claim. Multiple facts from one abstract = multiple objects.
2. Never merge facts from different abstracts.
3. evidence_quote MUST be a verbatim substring. If invented, set confidence = "low".
4. For fact_type == "datapoint": quant_value, quant_unit, quant_context are all required.
5. Unknown required string fields → "UNKNOWN". Unknown optional fields → null.
6. Do NOT count abstracts. Only extract facts.
`.trim();

// ── Step 1: Setup ─────────────────────────────────────────────────────────
phase("Setup");
const setup = await agent(
  `Two tasks:
   1. List all files matching pattern *_chunk_*.txt inside: ${S1_DIR}
      Return absolute paths with forward slashes.
   2. Ensure the output directory exists: ${OUT_DIR}
      Create it (mkdir -p) if missing.
   Return JSON: { "files": ["<path1>", ...], "out_dir_ready": true }`,
  {
    schema: {
      type: "object",
      properties: {
        files:         { type: "array", items: { type: "string" } },
        out_dir_ready: { type: "boolean" },
      },
      required: ["files"],
    },
  }
);

if (!setup?.files?.length) {
  log("ERROR: No chunk .txt files found — did Stage 1 complete?");
  return { gate: "HARD_FAIL", reason: "No chunks found in " + S1_DIR };
}
log(`Found ${setup.files.length} chunks. Launching extraction (cap: 16 concurrent)...`);

// ── Step 2: Extract facts in parallel ─────────────────────────────────────
phase("Extract");

const results = await pipeline(
  setup.files,
  (chunkPath) => {
    const chunkName = chunkPath.replace(/\\/g, "/").split("/").pop();
    const outFile   = `${OUT_DIR}/${chunkName.replace(".txt", "_facts.jsonl")}`;

    return agent(
      `You are a structured fact-extraction agent for ASGCT 2026 conference abstracts.

TASK
----
1. Read the file: ${chunkPath}
2. Find every abstract in the file (they start with a number followed by an ALL-CAPS title).
3. For each abstract, extract 1–5 key factual claims as JSON objects.
4. Write all facts to: ${outFile}
   Format: one JSON object per line (JSONL), UTF-8. Create parent directory if needed.
5. Return a compact summary.

${SCHEMA_RULES}

IMPORTANT: For every fact, set source_id = "${chunkName}" (the filename, not full path).`,
      {
        label: `extract:${chunkName}`,
        phase: "Extract",
        schema: {
          type: "object",
          properties: {
            success:            { type: "boolean" },
            facts_count:        { type: "number" },
            abstract_ids_found: { type: "array", items: { type: "string" } },
            error:              { type: ["string", "null"] },
          },
          required: ["success", "facts_count"],
        },
      }
    );
  }
);

const succeeded = results.filter(Boolean).filter((r) => r.success).length;
const failed    = results.filter(Boolean).filter((r) => !r.success).length;
const skipped   = results.filter((r) => r === null).length;
log(`Extraction done: ${succeeded} ok / ${failed} failed / ${skipped} skipped (of ${setup.files.length})`);

// ── Step 3: FM-12 gate ────────────────────────────────────────────────────
phase("Gate");
const gate = await agent(
  `Run this command and return its JSON output verbatim:

   python "${SKILL}/stage3_extract/extract_facts.py" \\
     --validate-only \\
     --config "${CFG_PATH}" \\
     --stage1-dir "${S1_DIR}" \\
     --out-dir "${OUT_DIR}"

   Return the parsed JSON from stdout.`,
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
  return { gate: "HARD_FAIL", reason: gate?.gate_reason, succeeded, failed, skipped };
}

log(`Stage 3 COMPLETE — Gate: PASS. ${gate.total_facts} facts from ${gate.extracted_count}/${gate.expected_count} abstracts.`);
return {
  gate:            "PASS",
  total_facts:     gate.total_facts,
  extracted_count: gate.extracted_count,
  expected_count:  gate.expected_count,
  succeeded,
  failed,
  skipped,
};
