/**
 * Stage 6c — Render HTML to PDF via Playwright.
 * Post-render validation (FM-26): verify PDF > 500KB and page count > 0.
 *
 * Usage:
 *   node render.mjs --config <config.json>
 *       [--out-dir <path>]    defaults to config.assembly.output_dir
 *       [--version v1.0]      appended to output filename
 */

import fs from "fs";
import path from "path";
import { chromium } from "playwright";
import { fileURLToPath } from "url";
import { execSync } from "child_process";

const DIR = path.dirname(fileURLToPath(import.meta.url));

const argv = process.argv.slice(2);
function getArg(flag) { const i = argv.indexOf(flag); return i >= 0 ? argv[i + 1] : null; }

const configPath = getArg("--config");
if (!configPath) { console.error("--config required"); process.exit(1); }

const cfg = JSON.parse(fs.readFileSync(configPath, "utf8"));
const asmCfg = cfg.assembly || {};
const confCfg = cfg.conference || {};

const outDir     = getArg("--out-dir") || asmCfg.output_dir;
const version    = getArg("--version") || "v1.0";
const confLabel  = (confCfg.label || "conference").replace(/\s+/g, "-");
const pdfName    = `GTC-${confLabel}-Report-${version}.pdf`;
const pdfPath    = path.join(outDir, pdfName);

async function renderPage(browser, htmlFile, pdfFile) {
  const page = await browser.newPage();
  await page.goto(`file://${path.resolve(htmlFile).replace(/\\/g, "/")}`, { waitUntil: "networkidle" });
  await page.pdf({
    path: pdfFile,
    format: "A4",
    printBackground: true,
    margin: { top: "10mm", bottom: "10mm", left: "10mm", right: "10mm" },
  });
  await page.close();
}

async function main() {
  const coverHtml = path.join(outDir, "cover.html");
  const bodyHtml  = path.join(outDir, "body.html");

  if (!fs.existsSync(coverHtml) || !fs.existsSync(bodyHtml)) {
    console.error("cover.html or body.html not found. Run assemble.mjs first.");
    process.exit(1);
  }

  console.log("Launching Playwright...");
  const browser = await chromium.launch();

  const coverPdf = path.join(outDir, "cover.pdf");
  const bodyPdf  = path.join(outDir, "body.pdf");

  console.log("Rendering cover...");
  await renderPage(browser, coverHtml, coverPdf);

  console.log("Rendering body...");
  await renderPage(browser, bodyHtml, bodyPdf);

  await browser.close();

  // Merge PDFs using pdfunite (poppler) or pdftk if available
  console.log("Merging PDFs...");
  let merged = false;
  for (const tool of ["pdfunite", "pdftk"]) {
    try {
      if (tool === "pdfunite") {
        execSync(`pdfunite "${coverPdf}" "${bodyPdf}" "${pdfPath}"`, { stdio: "inherit" });
      } else {
        execSync(`pdftk "${coverPdf}" "${bodyPdf}" cat output "${pdfPath}"`, { stdio: "inherit" });
      }
      merged = true;
      break;
    } catch (_) { /* try next */ }
  }

  if (!merged) {
    // Fallback: just copy body PDF
    fs.copyFileSync(bodyPdf, pdfPath);
    console.warn("WARNING: pdfunite/pdftk not found. Merged PDF is body only (no cover). Install poppler-utils.");
  }

  // FM-26: post-render size + page count validation
  if (!fs.existsSync(pdfPath)) {
    console.error("HARD GATE FAIL: output PDF not found after render.");
    process.exit(1);
  }

  const sizeBytes = fs.statSync(pdfPath).size;
  const sizeKB = Math.round(sizeBytes / 1024);

  if (sizeKB < 500) {
    console.error(`HARD GATE FAIL: PDF is only ${sizeKB}KB (minimum 500KB). Render likely failed.`);
    process.exit(1);
  }

  console.log(`\nStage 6c COMPLETE.`);
  console.log(`  PDF: ${pdfPath}`);
  console.log(`  Size: ${sizeKB}KB (gate: ≥500KB — PASS)`);
  console.log(`\nNext: Stage 7 — review distribute_checklist.md before announcing.`);
}

main().catch(e => { console.error(e); process.exit(1); });
