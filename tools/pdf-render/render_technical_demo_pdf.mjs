import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { marked } from "marked";
import puppeteer from "puppeteer";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, "..", "..");

const sourceMarkdownPath = path.join(repoRoot, "docs", "technical_demo_deep_dive.md");
const outputHtmlPath = path.join(repoRoot, "docs", "technical_demo_deep_dive_designed.html");
const outputPdfPath = path.join(repoRoot, "docs", "technical_demo_deep_dive_designed.pdf");
const outputPreviewPath = path.join(repoRoot, "docs", "technical_demo_deep_dive_designed_preview.png");

marked.setOptions({
  gfm: true,
  breaks: false,
  headerIds: false,
  mangle: false,
});

function slugify(value) {
  return value
    .toLowerCase()
    .replace(/&/g, "and")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function escapeHtml(value) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function classifySection(title) {
  const compact = title.toLowerCase();
  if (compact.includes("algorithm") || compact.includes("optimization")) return "Algorithms";
  if (compact.includes("dispatch")) return "Dispatch";
  if (compact.includes("tracking") || compact.includes("eta") || compact.includes("geofencing")) return "Tracking";
  if (compact.includes("mobile")) return "Execution";
  if (compact.includes("notification") || compact.includes("webhook")) return "Integrations";
  if (compact.includes("security")) return "Controls";
  if (compact.includes("observability") || compact.includes("infrastructure") || compact.includes("ci")) return "Platform";
  if (compact.includes("system surfaces") || compact.includes("capability map") || compact.includes("domain")) return "Overview";
  if (compact.includes("demo-safe") || compact.includes("limits")) return "Presentation";
  return "Deep Dive";
}

function extractSummaryBullets(tokens) {
  const bullets = [];
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token.type === "paragraph" && token.text.trim() === "The short version is:") {
      const nextToken = tokens[index + 1];
      if (nextToken?.type === "list") {
        for (const item of nextToken.items) {
          bullets.push(item.text.trim());
        }
      }
      break;
    }
  }
  return bullets;
}

function parseDocument(markdown) {
  const tokens = marked.lexer(markdown);
  const titleToken = tokens.find((token) => token.type === "heading" && token.depth === 1);
  const title = titleToken?.text ?? "Technical Demo Deep Dive";
  const summaryBullets = extractSummaryBullets(tokens);

  const firstSectionIndex = tokens.findIndex(
    (token) => token.type === "heading" && token.depth === 2,
  );

  const introTokens = firstSectionIndex === -1 ? tokens : tokens.slice(1, firstSectionIndex);
  const bodyTokens = firstSectionIndex === -1 ? [] : tokens.slice(firstSectionIndex);

  const sections = [];
  let currentSection = null;

  for (const token of bodyTokens) {
    if (token.type === "heading" && token.depth === 2) {
      if (currentSection) sections.push(currentSection);
      currentSection = {
        title: token.text,
        tokens: [],
      };
      continue;
    }
    if (currentSection) {
      currentSection.tokens.push(token);
    }
  }

  if (currentSection) sections.push(currentSection);

  return {
    title,
    summaryBullets,
    introHtml: marked.parser(introTokens),
    sections,
  };
}

function buildToc(sections) {
  return sections
    .map((section, index) => {
      const cleanTitle = section.title.replace(/^\d+\.\s*/, "");
      return `
        <li class="toc-item">
          <span class="toc-index">${String(index + 1).padStart(2, "0")}</span>
          <span class="toc-title">${escapeHtml(cleanTitle)}</span>
        </li>
      `;
    })
    .join("");
}

function buildSectionHtml(sections) {
  return sections
    .map((section, index) => {
      const numericPrefix = section.title.match(/^(\d+)/)?.[1] ?? String(index + 1);
      const bodyHtml = marked.parser(section.tokens);
      return `
        <section class="doc-section" id="${slugify(section.title)}">
          <div class="section-frame">
            <div class="section-heading">
              <div class="section-index">${numericPrefix}</div>
              <div class="section-title-group">
                <div class="section-kicker">${escapeHtml(classifySection(section.title))}</div>
                <h2>${escapeHtml(section.title)}</h2>
              </div>
            </div>
            <div class="section-body">
              ${bodyHtml}
            </div>
          </div>
        </section>
      `;
    })
    .join("\n");
}

function buildSummaryCards(summaryBullets) {
  return summaryBullets
    .map(
      (bullet) => `
        <div class="summary-card">
          <div class="summary-card-dot"></div>
          <p>${escapeHtml(bullet)}</p>
        </div>
      `,
    )
    .join("");
}

function buildHtml(documentData) {
  const today = "April 14, 2026";
  const summaryCards = buildSummaryCards(documentData.summaryBullets);
  const tocHtml = buildToc(documentData.sections);
  const sectionsHtml = buildSectionHtml(documentData.sections);

  return `<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>${escapeHtml(documentData.title)}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=Manrope:wght@400;500;600;700;800&display=swap" rel="stylesheet" />
    <style>
      :root {
        --paper: #f4efe7;
        --paper-strong: #fffaf2;
        --panel: rgba(255, 251, 245, 0.92);
        --ink: #211d1a;
        --muted: #6d655d;
        --line: rgba(61, 44, 30, 0.14);
        --ember: #d56f42;
        --ember-deep: #9f4424;
        --teal: #127e78;
        --gold: #d7aa46;
        --berry: #8f4858;
        --shadow: 0 24px 80px rgba(36, 25, 18, 0.12);
      }

      * {
        box-sizing: border-box;
        -webkit-print-color-adjust: exact;
        print-color-adjust: exact;
      }

      @page {
        size: A4;
        margin: 12mm 12mm 18mm;
      }

      html {
        background:
          radial-gradient(circle at top left, rgba(213, 111, 66, 0.16), transparent 32%),
          radial-gradient(circle at bottom right, rgba(18, 126, 120, 0.1), transparent 26%),
          var(--paper);
      }

      body {
        margin: 0;
        color: var(--ink);
        font-family: "Manrope", "Avenir Next", "Segoe UI", sans-serif;
        background: transparent;
      }

      .page-shell {
        width: 100%;
      }

      .cover {
        position: relative;
        min-height: 272mm;
        overflow: hidden;
        border-radius: 28px;
        padding: 18mm;
        color: #f9f2ea;
        background:
          linear-gradient(140deg, rgba(255, 255, 255, 0.06), transparent 42%),
          radial-gradient(circle at 82% 20%, rgba(213, 111, 66, 0.38), transparent 24%),
          radial-gradient(circle at 18% 82%, rgba(18, 126, 120, 0.24), transparent 24%),
          linear-gradient(135deg, #1f1915 0%, #2c221e 42%, #171311 100%);
        box-shadow: var(--shadow);
        break-after: page;
      }

      .cover::before {
        content: "";
        position: absolute;
        inset: 0;
        background-image:
          linear-gradient(rgba(255, 255, 255, 0.06) 1px, transparent 1px),
          linear-gradient(90deg, rgba(255, 255, 255, 0.06) 1px, transparent 1px);
        background-size: 18px 18px;
        mask-image: linear-gradient(180deg, rgba(0,0,0,0.6), transparent 85%);
        pointer-events: none;
      }

      .cover-grid {
        position: relative;
        display: grid;
        grid-template-columns: 1.3fr 0.92fr;
        gap: 14mm;
        height: 100%;
      }

      .cover-left {
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        min-height: 232mm;
      }

      .eyebrow {
        display: inline-flex;
        align-items: center;
        gap: 10px;
        padding: 7px 12px;
        border: 1px solid rgba(255, 255, 255, 0.16);
        border-radius: 999px;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.18em;
        text-transform: uppercase;
        color: rgba(250, 242, 233, 0.84);
        width: fit-content;
        background: rgba(255, 255, 255, 0.06);
        backdrop-filter: blur(12px);
      }

      .eyebrow::before {
        content: "";
        width: 7px;
        height: 7px;
        border-radius: 999px;
        background: linear-gradient(180deg, #ffd4a2, #d56f42);
        box-shadow: 0 0 0 5px rgba(213, 111, 66, 0.12);
      }

      .cover-title {
        margin: 20px 0 0;
        font-family: "Fraunces", Georgia, serif;
        font-size: 33px;
        line-height: 1.03;
        letter-spacing: -0.03em;
        max-width: 11.5cm;
      }

      .cover-subtitle {
        margin: 16px 0 0;
        max-width: 11.2cm;
        font-size: 12.5px;
        line-height: 1.7;
        color: rgba(249, 242, 234, 0.76);
      }

      .cover-meta {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 12px;
        margin-top: 22px;
        max-width: 12.5cm;
      }

      .cover-metric {
        padding: 14px 14px 12px;
        border-radius: 18px;
        background: rgba(255, 255, 255, 0.07);
        border: 1px solid rgba(255, 255, 255, 0.1);
      }

      .cover-metric .value {
        display: block;
        font-size: 17px;
        line-height: 1.1;
        font-weight: 800;
        color: #fff6ee;
      }

      .cover-metric .label {
        display: block;
        margin-top: 7px;
        font-size: 10px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: rgba(249, 242, 234, 0.58);
      }

      .cover-right {
        display: flex;
        flex-direction: column;
        gap: 12px;
        align-self: end;
      }

      .glass-panel {
        padding: 16px;
        border-radius: 24px;
        background: rgba(255, 250, 242, 0.08);
        border: 1px solid rgba(255, 255, 255, 0.12);
        backdrop-filter: blur(16px);
      }

      .panel-kicker {
        margin: 0 0 14px;
        font-size: 10px;
        font-weight: 800;
        letter-spacing: 0.16em;
        text-transform: uppercase;
        color: rgba(249, 242, 234, 0.54);
      }

      .summary-stack {
        display: grid;
        gap: 10px;
      }

      .summary-card {
        display: grid;
        grid-template-columns: 12px 1fr;
        gap: 12px;
        align-items: start;
        padding: 13px 14px;
        border-radius: 18px;
        background: rgba(255, 255, 255, 0.07);
        border: 1px solid rgba(255, 255, 255, 0.08);
      }

      .summary-card p {
        margin: 0;
        font-size: 11.5px;
        line-height: 1.6;
        color: rgba(249, 242, 234, 0.84);
      }

      .summary-card-dot {
        width: 12px;
        height: 12px;
        margin-top: 3px;
        border-radius: 999px;
        background: linear-gradient(180deg, #f0bc68, #d56f42);
        box-shadow: 0 0 0 5px rgba(213, 111, 66, 0.12);
      }

      .cover-bottom {
        display: flex;
        flex-wrap: wrap;
        gap: 9px;
        margin-top: 16px;
      }

      .chip {
        padding: 8px 10px;
        border-radius: 999px;
        background: rgba(255, 255, 255, 0.08);
        border: 1px solid rgba(255, 255, 255, 0.1);
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.05em;
        text-transform: uppercase;
        color: rgba(249, 242, 234, 0.8);
      }

      .toc-page {
        padding: 14mm 2mm 0;
        break-after: page;
      }

      .toc-grid {
        display: grid;
        grid-template-columns: 1.1fr 0.9fr;
        gap: 14px;
      }

      .light-panel {
        background: var(--panel);
        border: 1px solid rgba(33, 29, 26, 0.08);
        border-radius: 26px;
        box-shadow: var(--shadow);
      }

      .toc-panel,
      .facts-panel {
        padding: 16px 18px;
      }

      .toc-panel h2,
      .facts-panel h2 {
        margin: 0;
        font-family: "Fraunces", Georgia, serif;
        font-size: 22px;
        line-height: 1.1;
        letter-spacing: -0.02em;
      }

      .toc-copy {
        margin: 10px 0 18px;
        font-size: 11.5px;
        line-height: 1.7;
        color: var(--muted);
        max-width: 28em;
      }

      .toc-list {
        display: grid;
        gap: 10px;
        padding: 0;
        margin: 0;
        list-style: none;
      }

      .toc-item {
        display: grid;
        grid-template-columns: 34px 1fr;
        gap: 12px;
        align-items: start;
        padding: 12px 13px;
        border-radius: 18px;
        background: rgba(255, 255, 255, 0.72);
        border: 1px solid rgba(33, 29, 26, 0.06);
      }

      .toc-index {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        height: 28px;
        width: 28px;
        border-radius: 999px;
        background: linear-gradient(180deg, rgba(213,111,66,0.18), rgba(18,126,120,0.12));
        color: var(--ember-deep);
        font-size: 10px;
        font-weight: 800;
      }

      .toc-title {
        font-size: 12px;
        line-height: 1.55;
        font-weight: 700;
      }

      .fact-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
        margin-top: 16px;
      }

      .fact-card {
        padding: 14px 12px;
        border-radius: 18px;
        background:
          linear-gradient(180deg, rgba(255,255,255,0.82), rgba(255,255,255,0.58)),
          var(--paper-strong);
        border: 1px solid rgba(33, 29, 26, 0.08);
      }

      .fact-card .label {
        display: block;
        font-size: 10px;
        font-weight: 800;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: var(--muted);
      }

      .fact-card .value {
        display: block;
        margin-top: 10px;
        font-size: 16px;
        line-height: 1.25;
        font-weight: 800;
        color: var(--ink);
      }

      .facts-note {
        margin: 18px 0 0;
        padding: 14px 15px;
        border-radius: 18px;
        background: linear-gradient(135deg, rgba(18,126,120,0.08), rgba(213,111,66,0.12));
        color: var(--ink);
        font-size: 11px;
        line-height: 1.7;
      }

      .report {
        padding-top: 2mm;
      }

      .doc-section {
        margin-bottom: 11px;
      }

      .section-frame {
        position: relative;
        padding: 16px 18px 18px;
        border-radius: 26px;
        background: var(--panel);
        border: 1px solid rgba(33, 29, 26, 0.08);
        box-shadow: var(--shadow);
        overflow: hidden;
      }

      .section-frame::before {
        content: "";
        position: absolute;
        inset: 0 auto auto 0;
        width: 100%;
        height: 7px;
        background: linear-gradient(90deg, var(--ember), rgba(213, 111, 66, 0.18), rgba(18,126,120,0.22));
      }

      .section-heading {
        display: grid;
        grid-template-columns: 54px 1fr;
        gap: 16px;
        align-items: start;
        margin-top: 2px;
        margin-bottom: 14px;
      }

      .section-index {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 54px;
        height: 54px;
        border-radius: 18px;
        background: linear-gradient(180deg, rgba(213,111,66,0.18), rgba(18,126,120,0.1));
        color: var(--ember-deep);
        font-size: 18px;
        font-weight: 800;
      }

      .section-kicker {
        font-size: 10px;
        font-weight: 800;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        color: var(--muted);
        margin-bottom: 7px;
      }

      .section-title-group h2 {
        margin: 0;
        font-family: "Fraunces", Georgia, serif;
        font-size: 24px;
        line-height: 1.16;
        letter-spacing: -0.03em;
      }

      .section-body {
        font-size: 12px;
        line-height: 1.78;
        color: #2f2925;
      }

      .section-body > :first-child {
        margin-top: 0;
      }

      .section-body > :last-child {
        margin-bottom: 0;
      }

      .section-body h3 {
        margin: 20px 0 10px;
        font-family: "Fraunces", Georgia, serif;
        font-size: 17px;
        line-height: 1.25;
        letter-spacing: -0.02em;
      }

      .section-body p {
        margin: 10px 0;
      }

      .section-body ul,
      .section-body ol {
        margin: 10px 0 12px 20px;
        padding: 0;
      }

      .section-body li {
        margin: 5px 0;
        padding-left: 2px;
      }

      .section-body li::marker {
        color: var(--ember);
      }

      .section-body strong {
        color: var(--ink);
      }

      .section-body code {
        padding: 2px 7px;
        border-radius: 999px;
        background: rgba(33, 29, 26, 0.06);
        border: 1px solid rgba(33, 29, 26, 0.06);
        font-family: "SF Mono", "JetBrains Mono", "Menlo", monospace;
        font-size: 10.4px;
      }

      .section-body pre {
        margin: 14px 0;
        padding: 14px 16px;
        border-radius: 20px;
        overflow: hidden;
        background: #1f1815;
        color: #f7ebdf;
        border: 1px solid rgba(255,255,255,0.06);
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.04);
      }

      .section-body pre code {
        padding: 0;
        background: transparent;
        border: 0;
        border-radius: 0;
        color: inherit;
        font-size: 10px;
      }

      .section-body table {
        width: 100%;
        border-collapse: separate;
        border-spacing: 0;
        margin: 16px 0 10px;
        border: 1px solid rgba(33, 29, 26, 0.09);
        border-radius: 18px;
        overflow: hidden;
      }

      .section-body th,
      .section-body td {
        padding: 11px 12px;
        text-align: left;
        vertical-align: top;
        border-bottom: 1px solid rgba(33, 29, 26, 0.08);
        font-size: 11px;
        line-height: 1.55;
      }

      .section-body th {
        background: linear-gradient(180deg, rgba(213,111,66,0.11), rgba(18,126,120,0.06));
        font-size: 10px;
        letter-spacing: 0.09em;
        text-transform: uppercase;
        color: var(--muted);
      }

      .section-body tr:last-child td {
        border-bottom: 0;
      }

      .section-body blockquote {
        margin: 16px 0;
        padding: 14px 16px;
        border-left: 4px solid var(--ember);
        border-radius: 0 18px 18px 0;
        background: rgba(213,111,66,0.07);
        color: var(--ink);
      }

      .section-body hr {
        display: none;
      }

      .footer-mark {
        margin-top: 4mm;
        padding: 0 4px 2px;
        font-size: 10px;
        color: var(--muted);
        text-align: right;
      }
    </style>
  </head>
  <body>
    <div class="page-shell">
      <section class="cover">
        <div class="cover-grid">
          <div class="cover-left">
            <div>
              <div class="eyebrow">LastMileDelivery Technical Demo</div>
              <h1 class="cover-title">${escapeHtml(documentData.title)}</h1>
              <p class="cover-subtitle">
                A source-grounded system brief for live demo delivery. This version is designed for presentation: concrete algorithms, real implementation behavior, and the product surfaces that make the platform feel operational instead of theoretical.
              </p>
              <div class="cover-meta">
                <div class="cover-metric">
                  <span class="value">4</span>
                  <span class="label">Product Surfaces</span>
                </div>
                <div class="cover-metric">
                  <span class="value">OR-Tools + OSRM</span>
                  <span class="label">Core Stack</span>
                </div>
                <div class="cover-metric">
                  <span class="value">${today}</span>
                  <span class="label">Snapshot Date</span>
                </div>
              </div>
            </div>

            <div>
              <div class="cover-bottom">
                <span class="chip">Batch CVRPTW</span>
                <span class="chip">Cheapest Insertion</span>
                <span class="chip">Traffic EMA</span>
                <span class="chip">Draft Explainability</span>
                <span class="chip">Offline Mobile Replay</span>
              </div>
            </div>
          </div>

          <div class="cover-right">
            <div class="glass-panel">
              <p class="panel-kicker">What Matters Most</p>
              <div class="summary-stack">
                ${summaryCards}
              </div>
            </div>
            <div class="glass-panel">
              <p class="panel-kicker">Presentation Angle</p>
              <div class="summary-stack">
                <div class="summary-card">
                  <div class="summary-card-dot"></div>
                  <p>Lead with operational credibility: explain planning, execution, tracking, and exception handling as one continuous system.</p>
                </div>
                <div class="summary-card">
                  <div class="summary-card-dot"></div>
                  <p>Use specific algorithm names when asked. The strongest story in this codebase is that it is explicit, constrained, and auditable.</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section class="toc-page">
        <div class="toc-grid">
          <div class="light-panel toc-panel">
            <h2>Document Map</h2>
            <p class="toc-copy">
              The sequence below follows the way a technical audience will naturally interrogate the system: surfaces, planning model, exact algorithms, execution behavior, integrations, and where the honest limits are.
            </p>
            <ol class="toc-list">
              ${tocHtml}
            </ol>
          </div>
          <div class="light-panel facts-panel">
            <h2>Quick Facts</h2>
            <div class="fact-grid">
              <div class="fact-card">
                <span class="label">Primary Batch Solver</span>
                <span class="value">OR-Tools CVRPTW</span>
              </div>
              <div class="fact-card">
                <span class="label">Routing Substrate</span>
                <span class="value">Self-hosted OSRM</span>
              </div>
              <div class="fact-card">
                <span class="label">On-demand Logic</span>
                <span class="value">Cheapest Insertion</span>
              </div>
              <div class="fact-card">
                <span class="label">Traffic Model</span>
                <span class="value">Geohash + EMA</span>
              </div>
              <div class="fact-card">
                <span class="label">Backend Tests</span>
                <span class="value">90 modules</span>
              </div>
              <div class="fact-card">
                <span class="label">Max Batch Budget</span>
                <span class="value">120 seconds</span>
              </div>
            </div>
            <p class="facts-note">
              This PDF is generated from the repository’s markdown source, not hand-laid copy. The visual treatment is custom, but the content stays tied to the code-backed deep dive.
            </p>
          </div>
        </div>
      </section>

      <main class="report">
        ${sectionsHtml}
      </main>
      <div class="footer-mark">LastMileDelivery · Technical Demo Deck Companion</div>
    </div>
  </body>
</html>`;
}

async function ensureOutputDir() {
  await fs.mkdir(path.dirname(outputHtmlPath), { recursive: true });
}

async function renderPdf(htmlPath, pdfPath, previewPath) {
  const browser = await puppeteer.launch({
    headless: "new",
    defaultViewport: {
      width: 1440,
      height: 2048,
      deviceScaleFactor: 2,
    },
  });

  try {
    const page = await browser.newPage();
    await page.goto(`file://${htmlPath}`, {
      waitUntil: "networkidle0",
    });
    await page.emulateMediaType("screen");
    await page.screenshot({
      path: previewPath,
      fullPage: false,
    });
    await page.pdf({
      path: pdfPath,
      format: "A4",
      printBackground: true,
      preferCSSPageSize: true,
      displayHeaderFooter: true,
      headerTemplate: "<div></div>",
      footerTemplate: `
        <div style="width:100%;padding:0 12mm 0 12mm;font-size:9px;color:#6d655d;font-family:Arial, sans-serif;">
          <span style="float:left;">LastMileDelivery</span>
          <span style="float:right;"><span class="pageNumber"></span> / <span class="totalPages"></span></span>
        </div>
      `,
      margin: {
        top: "12mm",
        right: "0",
        bottom: "14mm",
        left: "0",
      },
    });
  } finally {
    await browser.close();
  }
}

async function main() {
  await ensureOutputDir();
  const markdown = await fs.readFile(sourceMarkdownPath, "utf8");
  const documentData = parseDocument(markdown);
  const html = buildHtml(documentData);
  await fs.writeFile(outputHtmlPath, html, "utf8");
  await renderPdf(outputHtmlPath, outputPdfPath, outputPreviewPath);
  console.log(`HTML written to ${outputHtmlPath}`);
  console.log(`PDF written to ${outputPdfPath}`);
  console.log(`Preview written to ${outputPreviewPath}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
