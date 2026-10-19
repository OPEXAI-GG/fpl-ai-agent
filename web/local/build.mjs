// Builds the local dashboard into web/local/dist: dashboard.html (page, styles and script in one
// file), fplsim_local.py (the small server) and "FPL Sim.cmd" (double-click on Windows).
//   node local/build.mjs

import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";

import { build } from "esbuild";

import { CSS } from "../src/style.js";
import { FONTS, MARK } from "../src/views.js";

const here = new URL("./", import.meta.url);
const dist = new URL("dist/", here);
mkdirSync(dist, { recursive: true });

const js = await build({
  entryPoints: [new URL("app.js", here).pathname],
  bundle: true,
  format: "iife",
  minify: true,
  write: false,
  target: "es2020",
});

const EXTRA = `
.did { list-style: none; padding: 0; margin: 12px 0 0; display: grid; gap: 8px; }
.did li { padding: 10px 12px 10px 34px; border: 1px solid var(--line); border-radius: var(--r-sm); background: var(--ink); position: relative; }
.did li::before { position: absolute; left: 12px; top: 9px; font: 700 15px var(--mono); }
.did .ok::before { content: "✓"; color: var(--lime); }
.did .warn::before { content: "!"; color: var(--coral); }
.did .bad { border-color: rgba(255,122,89,.5); } .did .bad::before { content: "×"; color: var(--coral); }
.report { white-space: pre-wrap; font: 13px/1.6 var(--mono); color: var(--mute); margin: 12px 0 0; overflow-x: auto; }
details.box summary { font-size: 16px; }
`;

const page = `<!doctype html>
<html lang="en-GB"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>FPL Sim · my team</title><meta name="theme-color" content="#0b0e0c">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="${FONTS.replace(/&/g, "&amp;")}">
<style>${CSS}${EXTRA}</style></head>
<body><header class="nav"><div class="wrap"><a class="brand" href="/">${MARK}<span>FPL <b>Sim</b></span></a>
<nav class="nav-links"><a href="https://fantasy.premierleague.com/my-team" target="_blank" rel="noopener">Open FPL</a></nav></div></header>
<main class="dash"><div class="wrap" id="root"><div class="box empty"><h3>Loading your plan…</h3></div></div></main>
<script>${js.outputFiles[0].text}</script></body></html>`;

writeFileSync(new URL("dashboard.html", dist), page);
copyFileSync(new URL("fplsim_local.py", here), new URL("fplsim_local.py", dist));
copyFileSync(new URL("launcher.cmd", here), new URL("FPL Sim.cmd", dist));
console.log(`built ${new URL("dashboard.html", dist).pathname}`);
