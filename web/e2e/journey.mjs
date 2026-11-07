// Browser journey: lookup -> dashboard -> this week's plan, plus every shared screen, at desktop
// and phone sizes. Checks keyboard use, horizontal overflow, text contrast, console errors and
// failed API calls, and saves screenshots.
//
//   BASE_URL=http://localhost:3000 ENTRY=<a team id> OUT=./e2e-out node e2e/journey.mjs
//
// It needs the demo running (scripts/web_demo.sh). ENTRY is any public FPL team ID; the plan
// step waits for that team's plan (preliminary first, then final) up to PLAN_WAIT seconds.
import { createRequire } from "node:module";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

const require = createRequire(import.meta.url);
const { chromium, devices } = require("playwright");

const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const ENTRY = process.env.ENTRY;
const OUT = process.env.OUT ?? "e2e-out";
const PLAN_WAIT = Number(process.env.PLAN_WAIT ?? 900);
if (!ENTRY) {
  console.error("Set ENTRY to a public FPL team ID.");
  process.exit(2);
}
mkdirSync(OUT, { recursive: true });

const results = { checks: [], console: [], failed: [], timings: {} };
const check = (name, ok, detail = "") => {
  results.checks.push({ name, ok, detail });
  console.log(`${ok ? "ok  " : "FAIL"} ${name}${detail ? `: ${detail}` : ""}`);
};

// WCAG contrast of visible text against the nearest opaque background.
const contrastScript = () => {
  const parse = (c) => {
    const m = c.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const [r, g, b, a = 1] = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
    return { r, g, b, a };
  };
  const lum = ({ r, g, b }) => {
    const f = (v) => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const bgOf = (el) => {
    for (let e = el; e; e = e.parentElement) {
      const c = parse(getComputedStyle(e).backgroundColor);
      if (c && c.a >= 0.95) return c;
      if (getComputedStyle(e).backgroundImage !== "none") return null; // gradients/stripes: skip
    }
    return { r: 255, g: 255, b: 255, a: 1 };
  };
  const bad = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  while (walker.nextNode()) {
    const el = walker.currentNode.parentElement;
    if (!el || seen.has(el) || !walker.currentNode.textContent.trim()) continue;
    seen.add(el);
    const st = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    if (st.visibility === "hidden" || st.display === "none" || rect.width === 0) continue;
    if (el.closest(".sr-only,[aria-hidden=true]")) continue;
    const fg = parse(st.color);
    const bg = bgOf(el);
    if (!fg || !bg) continue;
    const a = fg.a ?? 1;
    const mix = { r: fg.r * a + bg.r * (1 - a), g: fg.g * a + bg.g * (1 - a), b: fg.b * a + bg.b * (1 - a) };
    const [l1, l2] = [lum(mix), lum(bg)].sort((x, y) => y - x);
    const ratio = (l1 + 0.05) / (l2 + 0.05);
    const size = parseFloat(st.fontSize);
    const large = size >= 24 || (size >= 18.66 && Number(st.fontWeight) >= 700);
    if (ratio < (large ? 3 : 4.5)) bad.push({ text: el.textContent.trim().slice(0, 40), ratio: ratio.toFixed(2) });
  }
  return bad;
};

async function audit(page, label) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check(`${label}: no horizontal page scroll`, overflow <= 1, `${overflow}px`);
  const bad = await page.evaluate(contrastScript);
  check(`${label}: text contrast`, bad.length === 0, bad.slice(0, 3).map((b) => `${b.text} (${b.ratio})`).join("; "));
}

async function shot(page, name) {
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: true });
}

async function journey(browser, kind, ctxOpts) {
  const ctx = await browser.newContext(ctxOpts);
  const page = await ctx.newPage();
  page.on("console", (m) => {
    if (m.type() === "error") results.console.push({ kind, page: page.url(), text: m.text() });
  });
  page.on("response", (r) => {
    if (r.url().includes("/api/") && r.status() >= 400) {
      results.failed.push({ kind, url: r.url().replace(BASE, ""), status: r.status() });
    }
  });

  // landing, keyboard only: skip link first, then the lookup field
  let t = Date.now();
  await page.goto(BASE, { waitUntil: "networkidle" });
  results.timings[`${kind}_landing_ms`] = Date.now() - t;
  await page.keyboard.press("Tab");
  const skip = await page.evaluate(() => document.activeElement?.textContent?.trim());
  check(`${kind}: first Tab reaches the skip link`, /skip/i.test(skip ?? ""), skip ?? "");
  await shot(page, `${kind}-01-landing`);
  await audit(page, `${kind} landing`);

  const input = page.getByLabel("Your FPL team ID or team link").first();
  await input.focus();
  await input.fill("not a team");
  await page.keyboard.press("Enter");
  const err = page.locator("form p[role=alert]");
  await err.waitFor({ timeout: 10000 });
  check(`${kind}: invalid input is explained`, (await err.textContent()).length > 10, await err.textContent());
  await input.fill(ENTRY);
  t = Date.now();
  await page.keyboard.press("Enter");
  const open = page.getByRole("button", { name: /open the dashboard/i });
  await open.waitFor({ timeout: 20000 });
  results.timings[`${kind}_lookup_ms`] = Date.now() - t;
  await shot(page, `${kind}-02-match`);
  await open.focus();
  await page.keyboard.press("Enter");

  // dashboard
  t = Date.now();
  await page.waitForURL(`**/team/${ENTRY}`);
  await page.getByRole("heading", { name: "Squad" }).waitFor({ timeout: 30000 });
  results.timings[`${kind}_dashboard_ms`] = Date.now() - t;
  check(`${kind}: dashboard shows the pitch`, (await page.locator("figure").count()) > 0);
  check(`${kind}: free transfers labelled as an estimate`, (await page.getByText(/estimated|estimate/i).count()) > 0);
  await shot(page, `${kind}-03-dashboard`);
  await audit(page, `${kind} dashboard`);

  // this week's plan: preliminary may come first; wait for the final stage
  t = Date.now();
  await page.getByRole("link", { name: /this week's plan/i }).first().click();
  await page.waitForURL(`**/team/${ENTRY}/plan`);
  const final = page.getByText(/^Final$/).first();
  const deadline = Date.now() + PLAN_WAIT * 1000;
  let sawPreliminary = false;
  while (Date.now() < deadline) {
    if (await final.isVisible().catch(() => false)) break;
    if (await page.getByText(/^Preliminary$/).first().isVisible().catch(() => false)) sawPreliminary = true;
    await page.waitForTimeout(2500);
  }
  results.timings[`${kind}_plan_ms`] = Date.now() - t;
  check(`${kind}: plan reaches the final stage`, await final.isVisible(), sawPreliminary ? "preliminary shown first" : "");
  // one lineup when rolling (nothing changes), roll and proposed side by side otherwise, or the
  // rails notice when the proposed lineup would be rejected
  check(`${kind}: a lineup, or the rails notice, is shown`,
    (await page.getByRole("heading", { name: /^Lineup/ }).count()) + (await page.getByText(/rails/i).count()) > 0);
  await shot(page, `${kind}-04-plan`);
  await audit(page, `${kind} plan`);

  // shared screens
  for (const [name, url, ready] of [
    ["05-fixtures", "/fixtures", /fixtures/i],
    ["06-players", "/players", /players/i],
    ["07-model", "/model", /How the forecasts have done/i],
    ["08-settings", "/settings", /Settings/i],
    ["09-sample", "/team/sample", /Sample XI/i],
  ]) {
    t = Date.now();
    await page.goto(BASE + url, { waitUntil: "networkidle" });
    await page.getByRole("heading", { name: ready }).first().waitFor({ timeout: 30000 });
    results.timings[`${kind}_${name}_ms`] = Date.now() - t;
    await shot(page, `${kind}-${name}`);
    await audit(page, `${kind} ${url}`);
  }
  check(`${kind}: sample team is labelled`, (await page.getByText(/sample/i).count()) > 1);

  // cached reload of the dashboard
  t = Date.now();
  await page.goto(`${BASE}/team/${ENTRY}`, { waitUntil: "networkidle" });
  await page.getByRole("heading", { name: "Squad" }).waitFor({ timeout: 30000 });
  results.timings[`${kind}_dashboard_cached_ms`] = Date.now() - t;
  await ctx.close();
}

const browser = await chromium.launch();
try {
  await journey(browser, "desktop", { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
  await journey(browser, "mobile", { ...devices["iPhone 13"] });
} finally {
  await browser.close();
}
const expected = (f) => f.url.startsWith("/api/lookup") && [400].includes(f.status); // the invalid-input step
const unexpected = results.failed.filter((f) => !expected(f));
// the browser logs the deliberate invalid lookup's 400 as a console error: that one is expected
const consoleErrors = results.console.filter((c) => !(c.page === `${BASE}/` && /status of 400/.test(c.text)));
check("no console errors", consoleErrors.length === 0, JSON.stringify(consoleErrors.slice(0, 3)));
check("no unexpected failed API calls", unexpected.length === 0, JSON.stringify(unexpected.slice(0, 3)));
writeFileSync(path.join(OUT, "results.json"), JSON.stringify(results, null, 2));
const failed = results.checks.filter((c) => !c.ok).length;
console.log(`\n${results.checks.length - failed}/${results.checks.length} checks passed; screenshots in ${OUT}`);
process.exit(failed ? 1 : 0);
