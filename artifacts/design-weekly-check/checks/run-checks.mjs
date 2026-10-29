// Responsive and accessibility checks for the Weekly team check prototype.
// Run from the repo root:  node artifacts/design-weekly-check/checks/run-checks.mjs
// Uses the repo's existing Playwright + Chromium. Writes screenshots/ and checks/results.json.
// Automated checks do not establish WCAG conformance; see checks/manual-checks.md.
import { chromium } from "playwright";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import fs from "node:fs";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const shots = path.join(root, "screenshots");
fs.mkdirSync(shots, { recursive: true });
const base = pathToFileURL(path.join(root, "index.html")).href;

const WIDTHS = [360, 390, 768, 1440];
const STATES = [
  ["start", ""],
  ["confirm", "?team=1234567#confirm"],
  ["week", "?team=1234567&confirmed=1#week"],
  ["week-stale-range", "?team=1234567&confirmed=1&stale=1&range=1#week"],
  ["compare", "?team=1234567&confirmed=1#compare"],
  ["squad-pitch", "?team=1234567&confirmed=1#squad"],
  ["squad-list", "?team=1234567&confirmed=1&list=1#squad"],
  ["empty", "#empty"],
  ["preview", "#preview"],
];

// Runs in the page: overflow, small targets, small text, contrast, unnamed controls.
function audit() {
  const vis = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && !e.closest("[hidden]") && !e.closest("details:not([open]) > :not(summary)"); };
  const rgb = (c) => (c.match(/[\d.]+/g) || []).map(Number);
  const lum = ([r, g, b]) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const bgOf = (e) => { for (let n = e; n; n = n.parentElement) { const c = rgb(getComputedStyle(n).backgroundColor);
    if (c.length === 3 || (c.length === 4 && c[3] > 0.9)) return c.slice(0, 3); } return [18, 21, 24]; };
  const out = { overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth, smallTargets: [], smallText: [], lowContrast: [], unnamed: [] };
  for (const e of document.querySelectorAll("a, button, input, summary")) {
    if (!vis(e)) continue;
    const r = e.getBoundingClientRect();
    const name = (e.getAttribute("aria-label") || e.innerText || e.labels?.[0]?.innerText || "").trim();
    if (!name) out.unnamed.push(e.outerHTML.slice(0, 80));
    if (r.height < 44 || (r.width < 44 && e.tagName !== "INPUT"))
      out.smallTargets.push(`${e.tagName.toLowerCase()} "${name.slice(0, 30)}" ${Math.round(r.width)}x${Math.round(r.height)}`);
  }
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  while (walker.nextNode()) {
    const t = walker.currentNode; const e = t.parentElement;
    if (!t.textContent.trim() || seen.has(e) || !vis(e) || e.closest(".sr, .skip")) continue;
    seen.add(e);
    const s = getComputedStyle(e); const size = parseFloat(s.fontSize); const bold = parseInt(s.fontWeight) >= 700;
    if (size < 12) out.smallText.push(`${size}px "${t.textContent.trim().slice(0, 30)}"`);
    const a = lum(rgb(s.color).slice(0, 3)), b = lum(bgOf(e));
    const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    const need = size >= 24 || (size >= 18.66 && bold) ? 3 : 4.5;
    if (ratio < need) out.lowContrast.push(`${ratio.toFixed(2)} < ${need} "${t.textContent.trim().slice(0, 30)}"`);
    out.minContrast = Math.min(out.minContrast ?? 99, +ratio.toFixed(2));
  }
  return out;
}

const browser = await chromium.launch();
const results = { generated: new Date().toISOString(), states: {}, keyboard: {}, errors: [] };

for (const w of WIDTHS) {
  const page = await browser.newPage({ viewport: { width: w, height: w < 700 ? 800 : 900 } });
  page.on("pageerror", (e) => results.errors.push(`${w}: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") results.errors.push(`${w}: ${m.text()}`); });
  for (const [name, q] of STATES) {
    await page.goto(base + q);
    await page.waitForTimeout(150);
    results.states[`${name}@${w}`] = await page.evaluate(audit);
    await page.screenshot({ path: path.join(shots, `${name}-${w}.png`), fullPage: true });
  }
  // Failure states that need interaction.
  await page.goto(base);
  await page.fill("#team-id", "12ab");
  await page.press("#team-id", "Enter");
  results.states[`start-invalid@${w}`] = await page.evaluate(audit);
  await page.screenshot({ path: path.join(shots, `start-invalid-${w}.png`), fullPage: true });
  await page.fill("#team-id", "404");
  await page.press("#team-id", "Enter");
  await page.waitForSelector("#team-id-error:not([hidden])");
  await page.waitForTimeout(1000);
  await page.screenshot({ path: path.join(shots, `start-notfound-${w}.png`), fullPage: true });
  await page.close();
}

// Keyboard-only journey at 390px: ID -> confirm -> week -> compare. Records where focus lands.
{
  const page = await browser.newPage({ viewport: { width: 390, height: 800 } });
  const focus = () => page.evaluate(() => { const a = document.activeElement; return `${a.tagName.toLowerCase()}#${a.id} "${(a.innerText || a.value || "").trim().slice(0, 40)}"`; });
  const log = [];
  await page.goto(base);
  await page.keyboard.press("Tab"); log.push(["Tab 1", await focus()]);
  await page.keyboard.press("Enter"); log.push(["Enter on skip link", await focus()]);
  await page.focus("#team-id");
  await page.keyboard.type("1234567"); await page.keyboard.press("Enter");
  await page.waitForSelector("#screen-confirm:not([hidden])"); log.push(["after submit", await focus()]);
  let found = false;
  for (let i = 0; i < 8 && !found; i++) { await page.keyboard.press("Tab"); found = (await focus()).includes("confirm-yes"); }
  log.push(["tabbed to confirm button", String(found)]);
  await page.keyboard.press("Enter"); await page.waitForSelector("#screen-week:not([hidden])"); log.push(["after confirm", await focus()]);
  found = false;
  for (let i = 0; i < 20 && !found; i++) { await page.keyboard.press("Tab"); found = (await focus()).includes("Compare with keeping"); }
  log.push(["tabbed to compare link", String(found)]);
  await page.keyboard.press("Enter"); await page.waitForSelector("#screen-compare:not([hidden])"); log.push(["after compare", await focus()]);
  const ring = await page.evaluate(() => { document.querySelector(".back a").focus(); return getComputedStyle(document.activeElement).outlineStyle + " " + getComputedStyle(document.activeElement).outlineWidth; });
  log.push(["focus ring on back link", ring]);
  results.keyboard = log;
  await page.close();
}
await browser.close();

fs.writeFileSync(path.join(here, "results.json"), JSON.stringify(results, null, 2));
let problems = 0;
for (const [k, v] of Object.entries(results.states)) {
  const issues = [v.overflowX && `horizontal overflow (${v.scrollWidth}px)`, v.lowContrast.length && `${v.lowContrast.length} low-contrast`,
    v.smallText.length && `${v.smallText.length} text <12px`, v.unnamed.length && `${v.unnamed.length} unnamed controls`,
    v.smallTargets.length && `${v.smallTargets.length} targets <44px`].filter(Boolean);
  if (issues.length) { problems++; console.log(`${k}: ${issues.join(", ")}`); }
}
console.log("keyboard:", results.keyboard.map((x) => x.join(" -> ")).join("\n  "));
console.log(`errors: ${results.errors.length}; states with findings: ${problems}/${Object.keys(results.states).length}`);
