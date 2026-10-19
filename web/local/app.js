// The local dashboard's page script: loads /api/plan from fplsim_local.py and draws your plan
// with the same views as the FPL Sim website's Pro page.

import { html } from "../src/html.js";
import { fullPlan } from "../src/views.js";

const REPO = "https://github.com/OPEXAI-GG/fpl-ai-agent";
const $ = (id) => document.getElementById(id);

function ago(iso) {
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (!isFinite(s)) return "";
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} minutes ago`;
  if (s < 129600) return `${Math.round(s / 3600)} hours ago`;
  return `${Math.round(s / 86400)} days ago`;
}

function until(iso) {
  const s = (Date.parse(iso) - Date.now()) / 1000;
  if (!isFinite(s)) return "";
  if (s <= 0) return "passed";
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}

const when = (iso) =>
  new Date(iso).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

const cap = (t) => String(t).charAt(0).toUpperCase() + String(t).slice(1);

function outcomeBox(o, mode) {
  if (!o) return "";
  const rows = [
    ...(o.done || []).map((t) => html`<li class="ok">${cap(t)}</li>`),
    ...(o.blocked || []).map((t) => html`<li class="warn">Stopped by a safety rail: ${t}</li>`),
    ...(o.errors || []).map((t) => html`<li class="bad">${cap(t)}</li>`),
  ];
  return html`<div class="box" style="margin-bottom:16px"><div class="box-k">${mode === "live" ? "What the agent did on your team" : "What the agent would do (dry run)"}</div>
    ${rows.length ? html`<ul class="did">${rows}</ul>` : html`<p class="cap-sub" style="margin:10px 0 0">Nothing to change this run.</p>`}</div>`;
}

export function page(data) {
  const p = data.plan;
  const next = data.next;
  const mode = p?.mode || "dry";
  const gw = p?.decision?.gw;
  const stale = next && gw && next.gw > gw;
  const head = html`<div class="dash-head"><div>
      <span class="pill ${mode === "live" ? "pro" : ""}">${mode === "live" ? "Live on your team" : "Dry run"}</span>
      <h1>${gw ? `Gameweek ${gw}` : "Your plan"}</h1>
      <div class="cap-sub">${p?.created_at || data.updated_at ? html`planned ${ago(p?.created_at || data.updated_at)}` : ""}${
        next ? html` · GW${next.gw} deadline ${when(next.deadline)} (${until(next.deadline)})` : ""
      }</div></div>
      <div class="row-actions"><button class="btn btn-sm" id="refresh">Refresh</button>
        <a class="btn btn-sm" href="${REPO}/actions/workflows/plan.yml" target="_blank" rel="noopener">Re-plan on GitHub</a></div></div>`;
  if (!p) {
    return html`${head}<div class="box empty"><h3>No plan yet</h3><p>${data.error || "The agent has not published a plan. It appears after the next plan run."}</p></div>`;
  }
  const notes = (p.notes || []).filter((n) => !/^(odds|scout|ownership|learning):/.test(n));
  return html`${head}
    ${stale ? html`<div class="flash">This is the GW${gw} plan. The agent plans GW${next.gw} once its deadline is 26 hours away, then re-plans every 2 hours until 45 minutes before it.</div>` : ""}
    ${data.error ? html`<div class="flash bad">${data.error} Showing the last plan that loaded.</div>` : ""}
    ${outcomeBox(p.outcome, mode)}
    ${p.view ? fullPlan(p.view) : html`<div class="box"><div class="box-k">The plan</div><pre class="report">${data.report || ""}</pre></div>`}
    ${notes.length ? html`<div class="box" style="margin-top:16px"><h3>Agent notes</h3><ul class="notes">${notes.map((n) => html`<li>${cap(n)}</li>`)}</ul></div>` : ""}
    ${p.view && data.report ? html`<details class="box" style="margin-top:16px"><summary>Full report</summary><pre class="report">${data.report}</pre></details>` : ""}`;
}

async function load() {
  const btn = $("refresh");
  if (btn) btn.textContent = "Refreshing…";
  try {
    const res = await fetch("/api/plan", { cache: "no-store" });
    const data = await res.json();
    $("root").innerHTML = page(data).toString();
  } catch {
    $("root").innerHTML = html`<div class="box empty"><h3>The dashboard server stopped</h3><p>Close this tab and open FPL Sim again.</p></div>`.toString();
  }
  $("refresh")?.addEventListener("click", load);
}

if (typeof document !== "undefined") {
  load();
  setInterval(load, 5 * 60 * 1000);
}
