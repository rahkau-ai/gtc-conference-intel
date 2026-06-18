/**
 * Stage 3 — Parallel fact extraction via Claude Code Workflow tool.
 *
 * Reads all chunk .txt files from Stage 1 output → spawns one agent per chunk
 * (capped at 16 concurrent) → each agent calls extract_facts.py → writes .jsonl.
 * After all chunks, runs the FM-12 hard gate.
 *
 * Invocation:
 *   From a Claude Code session: provide this file path to the Workflow tool.
 *   Args: { stage1_dir, out_dir, config_path, skill_dir }
 */
export const meta = {
  name: "gtc-conference-intel-extract",
  description: "Parallel fact extraction from conference abstract chunks",
  phases: [
    { title: "Extract", detail: "One agent per chunk — extract facts to JSONL" },
    { title: "Gate", detail: "FM-12 coverage check: extracted == manifest - known_gaps" },
  ],
};

const { stage1_dir, out_dir, config_path, skill_dir } = args;

const SKILL = skill_dir || "C:/Users/Me/.claude/skills/gtc-conference-intel";
const CONFIG = config_path;

// Find all chunk .txt files
phase("Extract");
const findResult = await agent(
  `List all .txt files in ${stage1_dir} that match the pattern *_chunk_*.txt. Return a JSON array of absolute file paths.`,
  { schema: { type: "object", properties: { files: { type: "array", items: { type: "string" } } }, required: ["files"] } }
);

if (!findResult || !findResult.files || findResult.files.length === 0) {
  log("No chunk .txt files found. Did Stage 1 complete?");
  return { gate: "HARD_FAIL", reason: "No chunk .txt files found in " + stage1_dir };
}

log(`Found ${findResult.files.length} chunks. Extracting facts in parallel (cap: 16)...`);

const results = await pipeline(
  findResult.files,
  (chunkPath) => agent(
    `Run: python "${SKILL}/stage3_extract/extract_facts.py" --chunk "${chunkPath}" --config "${CONFIG}" --out-dir "${out_dir}"
Report: did it succeed (exit 0)? How many facts were extracted? Any errors?`,
    { label: `extract:${chunkPath.split(/[\\/]/).pop()}`, phase: "Extract",
      schema: { type: "object", properties: { success: { type: "boolean" }, facts_count: { type: "number" }, error: { type: "string" } }, required: ["success"] } }
  )
);

const succeeded = results.filter(Boolean).filter(r => r.success).length;
const failed = results.filter(Boolean).filter(r => !r.success).length;
log(`Extraction complete: ${succeeded} succeeded, ${failed} failed`);

// FM-12 gate
phase("Gate");
const gateResult = await agent(
  `Run: python "${SKILL}/stage3_extract/extract_facts.py" --validate-only --config "${CONFIG}" --stage1-dir "${stage1_dir}" --out-dir "${out_dir}"
Report the gate status (PASS or HARD_FAIL), extracted_count, expected_count, and total_facts.`,
  { label: "gate:coverage-check", phase: "Gate",
    schema: { type: "object", properties: { gate: { type: "string" }, extracted_count: { type: "number" }, expected_count: { type: "number" }, total_facts: { type: "number" }, gate_reason: { type: "string" } }, required: ["gate"] } }
);

if (!gateResult || gateResult.gate === "HARD_FAIL") {
  log(`HARD GATE FAIL: ${gateResult?.gate_reason}`);
  return { gate: "HARD_FAIL", reason: gateResult?.gate_reason, succeeded, failed };
}

log(`Gate PASS: ${gateResult.total_facts} facts from ${gateResult.extracted_count} abstracts (expected ${gateResult.expected_count})`);
return { gate: "PASS", total_facts: gateResult.total_facts, extracted_count: gateResult.extracted_count, succeeded, failed };
