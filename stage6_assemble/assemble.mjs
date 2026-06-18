/**
 * Stage 6b — Markdown report → branded print HTML.
 *
 * Pre-flight checks (FM-23, FM-24, FM-25):
 *   1. Every [[CHART:file.png|caption]] reference resolves in charts_manifest.json
 *   2. Every {{cite:NNN}} resolves in citations.json
 *   3. logo_path in config.json exists on disk
 *
 * Brand tokens loaded from shared/brand_tokens.json (FM-19 — single source).
 *
 * Usage:
 *   node assemble.mjs --config <path/to/config.json>
 *       [--draft <path/to/report-draft.md>]
 *       [--charts-dir <path/to/charts>]
 *       [--out-dir <path/to/build>]
 */

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const DIR = path.dirname(fileURLToPath(import.meta.url));
const SKILL_DIR = path.resolve(DIR, "..");

// ── CLI args ──────────────────────────────────────────────────────────────────
const argv = process.argv.slice(2);
function getArg(flag) {
  const i = argv.indexOf(flag);
  return i >= 0 ? argv[i + 1] : null;
}

const configPath = getArg("--config");
if (!configPath) { console.error("--config required"); process.exit(1); }

const cfg = JSON.parse(fs.readFileSync(configPath, "utf8"));
const asmCfg = cfg.assembly || {};
const confCfg = cfg.conference || {};

const draftPath  = getArg("--draft")     || asmCfg.report_draft;
const chartsDir  = getArg("--charts-dir") || asmCfg.charts_dir;
const outDir     = getArg("--out-dir")   || asmCfg.output_dir;
const logoPath   = asmCfg.logo_path;

if (!draftPath || !chartsDir || !outDir) {
  console.error("Missing required paths. Set in config.json: assembly.report_draft, assembly.charts_dir, assembly.output_dir");
  process.exit(1);
}

// ── Brand tokens (FM-19) ─────────────────────────────────────────────────────
const tokensPath = path.join(SKILL_DIR, "shared", "brand_tokens.json");
const tokens = JSON.parse(fs.readFileSync(tokensPath, "utf8"));
const COLORS = tokens.colors;
const PRIMARY = COLORS.primary;
const GOLD = COLORS.gold;

// ── Citations (FM-25) ─────────────────────────────────────────────────────────
const citationsPath = path.join(path.dirname(configPath), "citations.json");
let citations = {};
if (fs.existsSync(citationsPath)) {
  citations = JSON.parse(fs.readFileSync(citationsPath, "utf8"));
} else {
  console.warn("WARNING: citations.json not found. Run stage6_assemble/build_citations.py first.");
}

// ── Charts manifest (FM-23) ───────────────────────────────────────────────────
const manifestPath = path.join(chartsDir, "charts_manifest.json");
let chartsManifest = { charts: [] };
if (fs.existsSync(manifestPath)) {
  chartsManifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
}
const knownCharts = new Set(chartsManifest.charts.map(c => c.file));

// ── Pre-flight checks ─────────────────────────────────────────────────────────
console.log("Pre-flight checks...");
let preflight = true;

// FM-24: logo path
if (logoPath && !fs.existsSync(logoPath)) {
  console.error(`  FAIL: logo not found at ${logoPath}`);
  preflight = false;
} else if (logoPath) {
  console.log(`  OK: logo found`);
}

const md = fs.readFileSync(draftPath, "utf8");

// FM-23: chart references
const chartRefs = [...md.matchAll(/\[\[CHART:([^|]+)\|/g)].map(m => m[1].trim());
for (const ref of chartRefs) {
  if (!knownCharts.has(ref)) {
    const onDisk = fs.existsSync(path.join(chartsDir, ref));
    if (!onDisk) {
      console.error(`  FAIL: chart '${ref}' referenced in report but not found`);
      preflight = false;
    } else {
      console.warn(`  WARN: '${ref}' not in charts_manifest.json but exists on disk — proceeding`);
    }
  }
}
if (chartRefs.length > 0) console.log(`  OK: ${chartRefs.length} chart reference(s) checked`);

// FM-25: citation references
const citeRefs = [...md.matchAll(/\{\{cite:(\d+)\}\}/g)].map(m => m[1]);
const missingCites = citeRefs.filter(id => !citations[id]);
if (missingCites.length > 0) {
  console.warn(`  WARN: ${missingCites.length} {{cite:NNN}} not found in citations.json: ${missingCites.slice(0, 5).join(", ")}...`);
} else if (citeRefs.length > 0) {
  console.log(`  OK: ${citeRefs.length} citation reference(s) verified`);
}

if (!preflight) {
  console.error("\nPre-flight FAILED. Fix errors above before building.");
  process.exit(1);
}
console.log("Pre-flight PASSED.\n");

// ── Inline formatting ─────────────────────────────────────────────────────────
function esc(s) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
function inline(s) {
  let t = esc(s);
  t = t.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/\*(.+?)\*/g, "<em>$1</em>");
  // Resolve {{cite:NNN}} → superscript with title tooltip
  t = t.replace(/\{\{cite:(\d+)\}\}/g, (_, id) => {
    const c = citations[id];
    const tooltip = c ? esc(c.citation || c.subject || "") : `Abstract ${id}`;
    return `<sup class="cite" title="${tooltip}">[${id}]</sup>`;
  });
  return t;
}

function bodyToHtml(lines) {
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const t = line.trim();
    if (t === "" || t === "---") { i++; continue; }

    // Chart marker
    let m = t.match(/^\[\[CHART:([^|]+)\|(.+)\]\]$/);
    if (m) {
      const file = m[1].trim();
      const relPath = path.relative(outDir, path.join(chartsDir, file)).replace(/\\/g, "/");
      out.push(`<figure><img src="${relPath}" alt="${esc(m[2].trim())}"/><figcaption>${inline(m[2].trim())}</figcaption></figure>`);
      i++; continue;
    }

    // Callout block
    if (t === "[[CALLOUT]]") {
      const inner = [];
      i++;
      while (i < lines.length && lines[i].trim() !== "[[/CALLOUT]]") {
        const ct = lines[i].trim();
        if (ct) inner.push(`<p>${inline(ct)}</p>`);
        i++;
      }
      i++;
      out.push(`<aside class="callout">${inner.join("\n")}</aside>`);
      continue;
    }

    // Headings
    m = t.match(/^(#{1,4})\s+(.+)/);
    if (m) { out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); i++; continue; }

    // Bullet list
    if (t.startsWith("- ") || t.startsWith("* ")) {
      const items = [];
      while (i < lines.length && (lines[i].trim().startsWith("- ") || lines[i].trim().startsWith("* "))) {
        items.push(`<li>${inline(lines[i].trim().slice(2))}</li>`);
        i++;
      }
      out.push(`<ul>${items.join("")}</ul>`);
      continue;
    }

    out.push(`<p>${inline(t)}</p>`);
    i++;
  }
  return out.join("\n");
}

// ── Parse sections ────────────────────────────────────────────────────────────
const lines = md.split("\n");
let docTitle = confCfg.label || "Conference Intelligence Report";
let sections = [];
let current = null;

for (const line of lines) {
  const h1 = line.match(/^#\s+(.+)/);
  if (h1) { docTitle = h1[1]; continue; }
  const h2 = line.match(/^##\s+(.+)/);
  if (h2) {
    if (current) sections.push(current);
    current = { title: h2[1], lines: [] };
    continue;
  }
  if (current) current.lines.push(line);
}
if (current) sections.push(current);

// ── G watermark SVG ───────────────────────────────────────────────────────────
const opBody = tokens.watermark?.opacity_body || "0.04";
const opCover = tokens.watermark?.opacity_cover || "0.09";
const gSvg = (op, fill) => `data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='30 30 410 530'><g fill='${fill}' fill-opacity='${op}'><path d='M327.7,319.9h79.2c0,0-2.5-65.2-65.7-103.4s-186-30.4-240.3,33.2S37.1,405.9,70.6,482.9s98.5,91.7,126.3,98c27.8,6.3,134.1,14.4,212.6-54V376.4H277V440h54.3v48c0,0-32.1,38.3-102.3,36c-70.2-2.3-112.6-67.6-125.1-128.1s-6.5-122.4,22.3-165.4l37.8,15.4l35.8,20.3c0,0,28.4-10.7,62.7-4.6C297,267.7,318.3,290.2,327.7,319.9z'/><polygon points='187.2,269.1 120.1,269.1 115,287.8 187.2,287.8'/><polygon points='204.1,315.8 110.7,315.8 109.7,335.1 204.1,335.1'/><polygon points='204.1,362.1 110.7,362.1 113.1,381 204.1,381'/><polygon points='204.1,408.4 118.8,408.4 124.7,426.7 204.1,426.7'/><path d='M173,91.8c0,0-21.9,30.4-20.3,60.3s11,65.7,46.1,112.3s55,152.6-35,217.6c0,0,45.5-47.3,33.6-113.1s-52-71.9-74.3-148.4S173,91.8,173,91.8z'/></g></svg>`;

const logoTag = logoPath && fs.existsSync(logoPath)
  ? `<img src="${path.relative(outDir, logoPath).replace(/\\/g, "/")}" alt="GTC" class="logo"/>`
  : `<span class="logo-text">GTC</span>`;

// ── Build cover HTML ──────────────────────────────────────────────────────────
const coverHtml = `<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@300;400;600;700&display=swap');
  * { margin:0; padding:0; box-sizing:border-box; }
  body { width:210mm; height:297mm; background:linear-gradient(145deg,${PRIMARY} 0%,#0a3a8c 100%);
         font-family:'Source Sans 3',sans-serif; color:#fff; overflow:hidden; position:relative; }
  .watermark { position:absolute; bottom:-60px; right:-60px; width:500px; height:500px;
               background:url("${gSvg(opCover, '%23ffffff')}") no-repeat center; background-size:contain; }
  .inner { position:relative; z-index:1; padding:40mm 20mm 20mm; }
  .logo-wrap { margin-bottom:24mm; }
  img.logo { height:32px; filter:brightness(0) invert(1); }
  .logo-text { font-size:28px; font-weight:700; letter-spacing:2px; }
  .eyebrow { font-size:11px; letter-spacing:3px; text-transform:uppercase; opacity:.7; margin-bottom:6mm; }
  h1 { font-size:32px; font-weight:700; line-height:1.2; margin-bottom:6mm; }
  .subtitle { font-size:14px; opacity:.85; line-height:1.5; max-width:140mm; margin-bottom:16mm; }
  .meta { font-size:11px; opacity:.65; border-top:1px solid rgba(255,255,255,.3); padding-top:4mm; }
</style></head><body>
<div class="watermark"></div>
<div class="inner">
  <div class="logo-wrap">${logoTag}</div>
  <div class="eyebrow">GTC Intelligence Series</div>
  <h1>${esc(docTitle)}</h1>
  <p class="subtitle">A data-driven intelligence report from ${confCfg.label || "conference"} abstracts</p>
  <p class="meta">Dr. Rahul Kaushik &nbsp;·&nbsp; Gene Therapy Consultancy &nbsp;·&nbsp; ${new Date().getFullYear()}</p>
</div>
</body></html>`;

// ── Build body HTML ───────────────────────────────────────────────────────────
const tocItems = sections.map((s, i) => `<li><a href="#s${i}">${esc(s.title)}</a></li>`).join("\n");
const sectionHtml = sections.map((s, i) => `
<section id="s${i}">
  <h2>${esc(s.title)}</h2>
  ${bodyToHtml(s.lines)}
</section>`).join("\n");

const bodyHtml = `<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@300;400;600;700&display=swap');
  *, *::before, *::after { box-sizing:border-box; }
  body { font-family:'Source Sans 3',sans-serif; font-size:11pt; color:#1a1a2e; max-width:170mm; margin:0 auto; padding:15mm 0; line-height:1.6; position:relative; }
  .watermark { position:fixed; bottom:0; right:0; width:400px; height:400px; z-index:0; pointer-events:none;
               background:url("${gSvg(opBody, '%230f52ba')}") no-repeat center; background-size:contain; }
  h1, h2, h3 { color:${PRIMARY}; margin-top:1.5em; margin-bottom:.5em; }
  h2 { font-size:16pt; border-bottom:2px solid ${PRIMARY}; padding-bottom:.3em; }
  h3 { font-size:13pt; }
  p { margin:.6em 0; }
  ul { margin:.4em 0 .4em 1.5em; }
  li { margin:.2em 0; }
  figure { margin:1.5em 0; text-align:center; }
  figure img { max-width:100%; border:1px solid #e0e0e0; border-radius:4px; }
  figcaption { font-size:9pt; color:#666; margin-top:.4em; font-style:italic; }
  aside.callout { background:#f0f4ff; border-left:4px solid ${PRIMARY}; padding:.8em 1em; margin:1em 0; border-radius:0 4px 4px 0; }
  sup.cite { font-size:8pt; color:${PRIMARY}; cursor:default; }
  nav.toc { background:#f8f9fa; border:1px solid #e0e0e0; padding:1em 1.5em; margin-bottom:2em; border-radius:4px; }
  nav.toc h3 { margin-top:0; color:${PRIMARY}; font-size:11pt; }
  nav.toc ol { margin:0; padding-left:1.5em; }
  nav.toc li { margin:.3em 0; font-size:10pt; }
  nav.toc a { color:${PRIMARY}; text-decoration:none; }
  section { position:relative; z-index:1; }
</style></head><body>
<div class="watermark"></div>
<nav class="toc"><h3>Contents</h3><ol>${tocItems}</ol></nav>
${sectionHtml}
</body></html>`;

// ── Write output ──────────────────────────────────────────────────────────────
fs.mkdirSync(outDir, { recursive: true });
fs.writeFileSync(path.join(outDir, "cover.html"), coverHtml, "utf8");
fs.writeFileSync(path.join(outDir, "body.html"), bodyHtml, "utf8");

console.log(`cover.html → ${path.join(outDir, "cover.html")}`);
console.log(`body.html  → ${path.join(outDir, "body.html")}`);
console.log("\nStage 6b COMPLETE. Run stage6_assemble/render.mjs next.");
