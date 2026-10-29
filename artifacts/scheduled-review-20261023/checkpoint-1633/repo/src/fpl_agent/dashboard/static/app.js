// FPL agent dashboard. Plain DOM, no dependencies; every value is inserted as text, never HTML.
const TOKEN = document.querySelector('meta[name="dashboard-token"]').content;
const view = document.getElementById("view");
const title = document.getElementById("title");
const statusEl = document.getElementById("status");
const NAMES = {
  overview: "Overview", team: "My Team", recommendations: "AI Decisions", players: "Player Analytics",
  planning: "Gameweek Planner", activity: "Agent History", controls: "Agent Operations", health: "Data Health",
};
let pollTimer = null;
let renderSeq = 0;

// ---- tiny DOM helpers ------------------------------------------------------------------------
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") Object.assign(el.style, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const fmt = (v, d = 1) => (v === null || v === undefined ? "—" : Number(v).toFixed(d));
const dash = (v) => (v === null || v === undefined || v === "" ? "—" : String(v));
const when = (iso) => (iso ? new Date(iso).toLocaleString() : "—");
function span(hours) {
  if (hours < 1) return `${Math.round(hours * 60)} min`;
  if (hours < 48) return `${hours.toFixed(1)} h`;
  return `${(hours / 24).toFixed(1)} days`;
}
const ago = (hours) => (hours === null || hours === undefined ? "unknown" : `${span(hours)} ago`);
const pill = (text, kind = "") => h("span", { class: `pill ${kind}` }, text);
const card = (...kids) => h("section", { class: "card" }, ...kids);
const kpi = (label, value, sub) =>
  card(h("div", { class: "kpi" }, h("div", { class: "label" }, label),
    h("div", { class: "value" }, value), sub ? h("div", { class: "sub" }, sub) : null));
const note = (text) => h("p", { class: "note" }, text);
function table(cols, rows, empty = "Nothing recorded yet.") {
  if (!rows.length) return h("p", { class: "muted" }, empty);
  return h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, cols.map((c) => h("th", { class: c.num ? "num" : "", scope: "col" }, c.label)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, cols.map((c) => {
      const v = c.get(r);
      return h("td", { class: c.num ? "num" : "" }, v instanceof Node ? v : dash(v));
    }))))));
}
function stateBox(kind, head, text, action) {
  return h("div", { class: `state card ${kind}` }, h("strong", {}, head), text,
    action ? h("div", { class: "state-action" }, action) : null);
}
const decisionPill = (mode) =>
  pill(mode === "live-done" ? "applied & verified" : mode === "live" ? "live: outcome unknown" : "dry run",
    mode === "live-done" ? "good" : mode === "live" ? "warn" : "");

// ---- network -------------------------------------------------------------------------------
async function api(path, opts = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 15000);
  try {
    const res = await fetch(path, {
      ...opts, signal: ctrl.signal,
      headers: { "Content-Type": "application/json", "X-Dashboard-Token": TOKEN, ...(opts.headers || {}) },
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
    return body;
  } catch (e) {
    throw new Error(e.name === "AbortError" ? "the dashboard server did not answer in time" : e.message);
  } finally {
    clearTimeout(timer);
  }
}

function setStatus(meta) {
  statusEl.replaceChildren();
  if (!meta) return;
  const bits = [];
  if (meta.refresh_in_progress) bits.push(pill("job running: showing the last view", "warn"));
  if (meta.db_locked) bits.push(pill("database busy: showing the last view", "warn"));
  bits.push(h("span", {}, `data read ${when(meta.captured_at)}`));
  statusEl.append(...bits.flatMap((b, i) => (i ? [" · ", b] : [b])));
}

function guard(res) {
  // Shared missing / empty / error handling for read sections.
  const m = res.meta;
  setStatus(m);
  if (m.state === "missing")
    return stateBox("", "No database yet", "Run a refresh to load public FPL data, then a dry plan.",
      h("a", { href: "#controls" }, "Open Controls"));
  if (m.state === "error") return stateBox("error", "The database could not be read", m.message);
  if (m.state === "empty" || !res.data)
    return stateBox("", "No FPL data yet", m.message || "Run a refresh first.",
      h("a", { href: "#controls" }, "Open Controls"));
  return null;
}

// ---- sections ------------------------------------------------------------------------------
function barChart(items, getLabel, getValue, caption) {
  const known = items.filter((p) => Number.isFinite(getValue(p)));
  if (!known.length) return stateBox("", "Analysis awaits data", "Run a dry plan to generate clearly labelled projections.", h("a", { href: "#controls" }, "Configure your agent"));
  const max = Math.max(1, ...known.map(getValue));
  const bars = known.map((p) => {
    const fill = h("div", { class: "chart-fill" });
    fill.style.width = `${Math.max(0, getValue(p)) / max * 100}%`;
    return h("div", { class: "chart-row" }, h("span", { class: "chart-label", title: getLabel(p) }, getLabel(p)),
      h("div", { class: "chart-track", "aria-hidden": "true" }, fill), h("span", { class: "chart-value" }, fmt(getValue(p))));
  });
  return h("figure", { class: "chart", "aria-label": caption }, bars, h("figcaption", { class: "chart-caption" }, caption));
}
function insight(icon, heading, text, href, link) {
  return h("div", { class: "insight" }, h("span", { class: "insight-icon", "aria-hidden": "true" }, icon),
    h("div", {}, h("strong", {}, heading), note(text), href ? h("a", { href }, link) : null));
}
function pitchEmpty() {
  return h("div", { class: "pitch team-empty", "aria-label": "Football pitch: no stored lineup" },
    h("div", { class: "goal-area top-goal" }), h("div", { class: "goal-area bottom-goal" }),
    stateBox("", "Your next XI starts here", "Add your FPL team ID and run a dry plan to see the agent's proposed lineup.",
      h("a", { href: "#controls", class: "button primary" }, "Set up your team")));
}
async function overview() {
  const [res, playersRes, planningRes, jobs] = await Promise.all([
    api("/api/overview"), api("/api/players?limit=10&sort=points"), api("/api/planning"), api("/api/jobs")]);
  const stop = guard(res); if (stop) return stop;
  const d = res.data, latest = d.latest_decision;
  const kick = d.first_kickoff ? new Date(d.first_kickoff) : null;
  const left = kick ? (kick - Date.now()) / 36e5 : null;
  const fixture = planningRes.data?.weeks?.[0];
  const run = d.latest_run;
  const active = jobs.items.find((j) => ["running", "queued"].includes(j.status));
  return h("div", {},
    h("section", { class: "card hero" },
      h("div", {}, pill(`SEASON ${d.season}/${String(d.season + 1).slice(2)}  /  GAMEWEEK ${d.next_gw}`, "good"),
        h("h2", {}, "See the game. Find your edge."), h("p", {}, "Your squad, the numbers behind it, and the next move. One place to prepare for matchday."),
        h("a", { href: "#planning", class: "section-link" }, "Explore the gameweek \u2197")),
      h("div", { class: "hero-meta" },
        h("div", {}, h("small", {}, "FIRST KICKOFF"), h("strong", {}, kick ? kick.toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "Unavailable"),
          h("small", {}, left === null ? "No fixture time stored" : left > 0 ? `In ${span(left)}` : "Already started")),
        h("div", {}, h("small", {}, "AGENT WORKSPACE"), h("strong", {}, active ? "Job running" : run ? `Last run: ${run.status}` : "Ready for analysis"), h("small", {}, run ? `GW${dash(run.gw)} / ${run.mode} / ${run.state}` : "Local controls always run dry")))),
    h("div", { class: "grid" },
      kpi("Latest plan \u00b7 prediction", latest ? `${fmt(latest.xp)} xP` : "Not planned", latest ? `GW${latest.gw} \u00b7 ${when(latest.created_at)}` : "Run your first dry plan"),
      kpi("Player universe", d.counts.players.toLocaleString(), `GW${d.next_gw} official FPL snapshot`),
      kpi("Data freshness", ago(d.snapshot_age_hours), d.snapshot_stale ? pill("Refresh recommended", "warn") : pill("Within 6-hour window", "good")),
      kpi("Stored decisions", d.counts.decisions.toLocaleString(), `${d.counts.projection_runs} projection runs \u00b7 ${d.counts.reviews} reviews`)),
    h("div", { class: "overview-bottom" },
      card(h("div", { class: "split" }, h("h2", {}, "The points leaders"), h("a", { href: "#players", class: "section-link" }, "All players \u2197")),
        barChart((playersRes.data?.items || []).slice(0, 7), (p) => p.name, (p) => p.points, "Official points in stored match history; coverage may be incomplete.")),
      card(h("div", { class: "split" }, h("h2", {}, "Your next moves"), pill("AGENT BRIEF")),
        insight("01", latest ? `Latest decision: GW${latest.gw}` : "Build your first gameweek plan", latest ? latest.status : "Set a team ID to unlock a proposed XI, captaincy and transfers.", "#recommendations", "View AI decisions \u2192"),
        insight("02", d.snapshot_stale ? "Bring your data up to date" : "Your data is ready", `Last successful load: ${when(d.last_ingest_at)}.`, "#health", "Inspect data health \u2192"),
        insight("03", "Keep the deadline in view", "The exact FPL deadline is not stored. Check the official game before making changes.", "#planning", "View fixture calendar \u2192"))),
    h("div", { class: "overview-bottom" },
      card(h("div", { class: "split" }, h("h2", {}, `On the schedule${fixture ? ` \u00b7 GW${fixture.gw}` : ""}`), h("a", { href: "#planning", class: "section-link" }, "Full calendar \u2197")),
        table([{ label: "Home", get: (f) => f.home }, { label: "Away", get: (f) => f.away }, { label: "Kickoff / result", get: (f) => f.finished ? `FT ${f.score || ""}` : when(f.kickoff) }], fixture?.fixtures?.slice(0, 4) || [], "No fixtures stored for this gameweek.")),
      card(h("h2", {}, "Account snapshot"), table([{ label: "Metric", get: (r) => r[0] }, { label: "Availability", get: (r) => r[1] }],
        [["Gameweek points", "Not stored"], ["Overall rank", "Not stored"], ["Current team value", "Not stored"], ["Exact deadline", "Not stored"]]),
        note("The dashboard reads local records. Account statistics are unavailable in the stored data."))));
}

function playerCard(p, cap, vice) {
  return h("button", { class: "player", type: "button", onclick: () => openPlayer(p), "aria-label": `View ${p.name} statistics` },
    h("div", { class: "shirt", "aria-hidden": "true" }),
    p.player_id === cap ? h("span", { class: "arm", title: "captain", "aria-label": "captain" }, "C") :
      p.player_id === vice ? h("span", { class: "arm", title: "vice-captain", "aria-label": "vice-captain" }, "V") : null,
    h("div", { class: "nm", title: p.name }, p.name),
    h("div", { class: "meta" }, `${dash(p.team)} · £${fmt(p.price)}m`),
    h("div", {}, `${fmt(p.xp)} xP`),
    p.q10 !== null && p.q10 !== undefined ? h("div", { class: "meta" }, `${fmt(p.q10, 0)}–${fmt(p.q90, 0)} pts`) : null);
}

async function team() {
  const res = await api("/api/team");
  const stop = guard(res); if (stop) return stop;
  const d = res.data, p = d.planned;
  const notes = card(h("h2", {}, "About this squad"), note(d.public_squad_note), note(d.current_squad_note));
  if (!p) return h("div", {}, card(h("div", { class: "split" }, h("h2", {}, "Your matchday squad"), pill("NO PLAN STORED")), pitchEmpty()), notes);
  const capId = p.captain && p.captain.player_id, viceId = p.vice && p.vice.player_id;
  const lines = ["GKP", "DEF", "MID", "FWD"].map((pos) =>
    h("div", { class: "line", role: "group", "aria-label": pos },
      p.xi.filter((x) => x.position === pos).map((x) => playerCard(x, capId, viceId))));
  return h("div", {},
    card(h("div", { class: "split" }, h("h2", {}, `Planned lineup · GW${p.gw} · ${p.formation}`), decisionPill(p.mode)),
      h("p", { class: "note" }, `${p.status}. Made ${when(p.created_at)}. Entry: ${p.entry}.`),
      h("div", { class: "pitch" }, h("div", { class: "goal-area top-goal" }), h("div", { class: "goal-area bottom-goal" }), lines),
      h("h3", {}, "Bench (in order)"),
      h("div", { class: "line bench" }, p.bench.map((x) => playerCard(x, capId, viceId)))),
    h("div", { class: "grid" },
      kpi("Expected points", fmt(p.xp), `${fmt(p.xp_no_transfers)} without transfers`),
      kpi("Bank after", p.bank_after === null ? "—" : `£${fmt(p.bank_after)}m`),
      kpi("Chip", dash(p.chip), p.hits ? `${p.hits} hit(s)` : "no hits")),
    notes);
}

function decisionCard(d) {
  const moves = d.transfers_in.map((x, i) => ({ out: d.transfers_out[i], in: x }));
  return card(
    h("div", { class: "split" }, h("h2", {}, `GW${d.gw} · ${when(d.created_at)}`), decisionPill(d.mode)),
    h("p", { class: "note" }, d.status),
    h("div", { class: "row" },
      pill(`${fmt(d.xp)} xP`), pill(`${fmt(d.xp_no_transfers)} xP without moves`),
      pill(d.chip ? `chip: ${d.chip}` : "no chip"), pill(d.hits ? `${d.hits} hit(s)` : "no hits"),
      d.bank_after === null ? null : pill(`bank £${fmt(d.bank_after)}m`)),
    h("h3", {}, "Decision context"),
    note(d.xp == null || d.xp_no_transfers == null ? "Expected-points comparison unavailable." :
      `Stored plan projects ${fmt(d.xp - d.xp_no_transfers)} more points than the no-transfer lineup. These are predictions, not guaranteed returns.`),
    note("The proposed lineup is shown in My Team. Values below are stored model comparisons, not guarantees."),
    ...(d.field_view || []).map(note),
    ...(d.checks || []).map((c) => note(`Passed check: ${c}`)),
    ...Object.entries(d.chip_timing || {}).map(([chip, t]) => note(`${chip}: expires GW${t.expires_gw}; best stored scenario GW${t.best_gw}; projected gain forgone if used now: ${fmt(t.gain_forgone_if_used_now)} pts.`)),
    h("h3", {}, "Transfers"),
    table([{ label: "Out", get: (m) => m.out ? m.out.name : "—" },
      { label: "In", get: (m) => m.in.name },
      { label: "In xP", num: true, get: (m) => fmt(m.in.xp) },
      { label: "Range", num: true, get: (m) => (m.in.q10 === null ? "—" : `${fmt(m.in.q10, 0)}–${fmt(m.in.q90, 0)}`) }],
    moves, "No transfers: roll the free transfer."),
    h("h3", {}, "Captaincy"),
    h("p", {}, `Captain ${d.captain ? d.captain.name : "—"} · vice ${d.vice ? d.vice.name : "—"}`),
    d.captain_options.length ? note("Options: " + d.captain_options.map((o) => `${o.name} ${fmt(o.xp)}`).join(", ")) : null,
    d.next_moves.length ? h("div", {}, h("h3", {}, "Planned next weeks"),
      h("ul", { class: "list" }, d.next_moves.map((w) =>
        h("li", {}, `GW${w.gw}: ${w.in.length ? `${w.out.join(", ")} → ${w.in.join(", ")}` : "no moves"}`)))) : null,
    Object.keys(d.held_chips).length ? note("Chips held for later: " + Object.entries(d.held_chips).map(([c, g]) => `${c} (${JSON.stringify(g)})`).join(", ")) : null,
    d.warnings.length ? h("div", {}, h("h3", {}, "Warnings"), h("ul", { class: "list" }, d.warnings.map((w) => h("li", {}, w)))) : null,
    note(`Projection run: ${dash(d.projection_run)}. Entry: ${d.entry}.`));
}

async function recommendations() {
  const res = await api("/api/recommendations");
  const stop = guard(res); if (stop) return stop;
  const items = res.data.items;
  if (!items.length) return stateBox("", "No recommendations yet", "Run a dry plan from Controls.",
    h("a", { href: "#controls" }, "Open Controls"));
  return h("div", {}, items.map(decisionCard));
}

const playerState = { q: "", position: "", sort: "xp", limit: 25, offset: 0, compare: new Set() };
async function players() {
  const s = playerState;
  const qs = new URLSearchParams({ q: s.q, position: s.position, sort: s.sort, limit: s.limit, offset: s.offset });
  const res = await api(`/api/players?${qs}`);
  const stop = guard(res); if (stop) return stop;
  const d = res.data;
  const rerender = () => render("players", true);
  let debounce = null;
  const search = h("input", { type: "search", value: s.q, placeholder: "Name or club", "aria-label": "Search players",
    oninput: (e) => { clearTimeout(debounce); debounce = setTimeout(() => { s.q = e.target.value; s.offset = 0; if (location.hash === "#players") rerender(); }, 250); } });
  const select = (label, key, opts) => h("label", {}, label, h("select", { "aria-label": label,
    onchange: (e) => { s[key] = key === "limit" ? Number(e.target.value) : e.target.value; s.offset = 0; rerender(); } },
  opts.map(([v, t]) => h("option", { value: v, selected: String(s[key]) === String(v) }, t))));
  const cmp = h("div", { id: "compare" });
  const rows = table([
    { label: "Compare", get: (p) => h("input", { type: "checkbox", "aria-label": `compare ${p.name}`, checked: s.compare.has(p.player_id),
      onchange: (e) => { e.target.checked ? s.compare.add(p.player_id) : s.compare.delete(p.player_id); if (s.compare.size > 4) { s.compare.delete(p.player_id); e.target.checked = false; } showCompare(cmp); } }) },
    { label: "Player", get: (p) => h("span", { title: p.news || "" }, h("button", { class: "player-name", onclick: () => openPlayer(p) }, p.name), p.status !== "a" ? h("span", { class: "muted" }, ` (${p.status}${p.chance !== null ? ` ${p.chance}%` : ""})`) : null) },
    { label: "Club", get: (p) => p.team }, { label: "Pos", get: (p) => p.position },
    { label: "Price", num: true, get: (p) => `£${fmt(p.price)}m` },
    { label: "Owned", num: true, get: (p) => (p.owned === null ? "—" : `${fmt(p.owned)}%`) },
    { label: "Pts", num: true, get: (p) => dash(p.points) },
    { label: "Next xP", num: true, get: (p) => fmt(p.xp, 2) },
    { label: "Range", num: true, get: (p) => (p.q10 === null ? "—" : `${fmt(p.q10, 0)}–${fmt(p.q90, 0)}`) },
  ], d.items, "No players match.");
  const pages = Math.max(1, Math.ceil(d.total / d.limit)), page = Math.floor(d.offset / d.limit) + 1;
  showCompare(cmp);
  return h("div", {},
    card(h("div", { class: "toolbar" }, h("label", {}, "Search", search),
      select("Position", "position", [["", "All"], ["GKP", "GKP"], ["DEF", "DEF"], ["MID", "MID"], ["FWD", "FWD"]]),
      select("Sort by", "sort", [["xp", "Next GW xP"], ["price", "Price"], ["owned", "Ownership"], ["points", "Points"], ["name", "Name"]]),
      select("Per page", "limit", [[10, "10"], [25, "25"], [50, "50"], [100, "100"]])),
    rows,
    h("div", { class: "pager" }, h("span", { class: "muted" }, `${d.total} players · page ${page} of ${pages}`),
      h("button", { disabled: page <= 1, onclick: () => { s.offset = Math.max(0, s.offset - s.limit); rerender(); } }, "Previous"),
      h("button", { disabled: page >= pages, onclick: () => { s.offset += s.limit; rerender(); } }, "Next")),
    note(`Select 2-4 players to compare. Click a name for details and recent form. GW${d.gw} snapshot. xP and range (10th–90th percentile points) come from the latest stored projection; "—" means not projected or unknown.`)),
    cmp);
}

async function showCompare(box) {
  const ids = [...playerState.compare];
  box.replaceChildren();
  if (ids.length < 2) {
    if (ids.length) box.append(card(note("Pick at least two players (up to four) to compare.")));
    return;
  }
  try {
    const res = await api(`/api/players?ids=${ids.join(",")}`);
    if (ids.join() !== [...playerState.compare].join()) return;
    const ps = res.data.items;
    const rows = [["Club", (p) => p.team], ["Position", (p) => p.position], ["Price", (p) => `£${fmt(p.price)}m`],
      ["Status", (p) => `${p.status}${p.chance !== null ? ` (${p.chance}%)` : ""}`], ["Owned", (p) => `${fmt(p.owned)}%`],
      ["Points (stored history)", (p) => dash(p.points)], ["Minutes", (p) => dash(p.minutes)], ["Next GW xP", (p) => fmt(p.xp, 2)],
      ["Range", (p) => (p.q10 === null ? "—" : `${fmt(p.q10, 0)}–${fmt(p.q90, 0)}`)], ["Expected minutes", (p) => fmt(p.exp_minutes, 0)],
      ["News", (p) => p.news || "—"]];
    box.append(card(h("h2", {}, "Comparison"), barChart(ps, (p) => p.name, (p) => p.xp, "Next gameweek expected points (prediction)."), h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, ""), ps.map((p) => h("th", { scope: "col" }, p.name)))),
      h("tbody", {}, rows.map(([label, get]) => h("tr", {}, h("th", { scope: "row" }, label), ps.map((p) => h("td", {}, dash(get(p)))))))))));
  } catch (e) {
    box.append(stateBox("error", "Comparison failed", e.message));
  }
}

const plannerState = { gw: "", club: "" };
async function planning() {
  const res = await api("/api/planning");
  const stop = guard(res); if (stop) return stop;
  const d = res.data;
  const max = Math.max(1, ...d.projection.map((r) => r.xi_xp || 0));
  const clubs = [...new Set(d.weeks.flatMap((w) => w.fixtures.flatMap((f) => [f.home, f.away])))].filter(Boolean).sort();
  const filter = (label, key, options) => h("label", {}, label, h("select", {
    "aria-label": label, onchange: (e) => { plannerState[key] = e.target.value; render("planning", true); }
  }, options.map(([value, text]) => h("option", { value, selected: String(plannerState[key]) === String(value) }, text))));
  const weeks = d.weeks.filter((w) => !plannerState.gw || String(w.gw) === plannerState.gw)
    .map((w) => ({...w, fixtures: w.fixtures.filter((f) => !plannerState.club || f.home === plannerState.club || f.away === plannerState.club)}))
    .filter((w) => w.fixtures.length);
  return h("div", {},
    card(h("h2", {}, "Projection horizon"),
      d.projection.length ? h("div", { class: "table-wrap" }, h("table", { class: "bars" },
        h("thead", {}, h("tr", {}, h("th", {}, "GW"), h("th", { class: "num" }, "Players"), h("th", { class: "num" }, "Latest XI xP"), h("th", {}, ""))),
        h("tbody", {}, d.projection.map((r) => h("tr", {}, h("td", {}, `GW${r.gw}`), h("td", { class: "num" }, r.players),
          h("td", { class: "num" }, fmt(r.xi_xp)), h("td", {}, h("div", { class: "bar", style: { width: `${((r.xi_xp || 0) / max) * 100}%` } }))))))) :
        h("p", { class: "muted" }, "No projection stored yet: run a dry plan."),
      note(d.projection_note), note("All xP values are model predictions, not actual points.")),
    d.decision ? card(h("h2", {}, "Planned moves"),
      d.decision.next_moves.length ? h("ul", { class: "list" }, d.decision.next_moves.map((w) =>
        h("li", {}, `GW${w.gw}: ${w.in.length ? `${w.out.join(", ")} → ${w.in.join(", ")}` : "no moves"}`))) :
        h("p", { class: "muted" }, "No later moves stored with the latest plan."),
      Object.keys(d.decision.held_chips).length ? note("Chips held: " + Object.entries(d.decision.held_chips).map(([c, g]) => `${c} (${JSON.stringify(g)})`).join(", ")) : null) : null,
    card(h("h2", {}, "Fixture calendar"), h("div", { class: "toolbar" },
      filter("Gameweek", "gw", [["", "All upcoming"], ...d.weeks.map((w) => [w.gw, `GW${w.gw}`])]),
      filter("Club", "club", [["", "All clubs"], ...clubs.map((c) => [c, c])])),
      note("Official scheduled fixtures. Fixture difficulty ratings are not stored locally.")),
    !weeks.length ? stateBox("", "No matching fixtures", "Choose another club or gameweek.") : null,
    h("div", { class: "cols" }, weeks.map((w) => card(h("h2", {}, `GW${w.gw} fixtures`),
      table([{ label: "Home", get: (f) => f.home }, { label: "Away", get: (f) => f.away },
        { label: "Kickoff", get: (f) => (f.finished ? `FT ${f.score || ""}` : when(f.kickoff)) }], w.fixtures)))),
    d.unscheduled.length ? card(h("h2", {}, "Not yet assigned to a gameweek"),
      table([{ label: "Home", get: (f) => f.home }, { label: "Away", get: (f) => f.away }, { label: "Kickoff", get: (f) => when(f.kickoff) }], d.unscheduled),
      note("Postponed or unscheduled fixtures, as FPL lists them.")) : null);
}

async function activity() {
  const [res, jobs] = await Promise.all([api("/api/activity"), api("/api/jobs")]);
  const stop = guard(res);
  const jobCard = card(h("h2", {}, "Dashboard jobs"), table([
    { label: "Started", get: (j) => when(j.started_at || j.created_at) }, { label: "Action", get: (j) => j.action },
    { label: "Status", get: (j) => jobPill(j.status) }, { label: "Exit", num: true, get: (j) => dash(j.exit_code) },
    { label: "Note", get: (j) => j.message }], jobs.items, "No dashboard jobs yet."));
  if (stop) return h("div", {}, stop, jobCard);
  const d = res.data;
  return h("div", {}, jobCard,
    card(h("h2", {}, "Agent execution history"), table([
      { label: "Started", get: (r) => when(r.started_at) }, { label: "GW", get: (r) => r.gw },
      { label: "Entry", get: (r) => r.entry_id }, { label: "Mode", get: (r) => r.mode },
      { label: "Status", get: (r) => pill(r.status, r.exit_code ? "bad" : "") },
      { label: "State", get: (r) => r.state }, { label: "Exit", get: (r) => r.exit_code },
      { label: "Delivered", get: (r) => r.delivered == null ? "Unknown" : r.delivered ? "Yes" : "No" },
      { label: "Reasons", get: (r) => (r.reasons || []).join("; ") || "None recorded" },
      { label: "Stages", get: (r) => Object.entries(r.stage_seconds || {}).map(([k, v]) => `${k}: ${fmt(v, 2)}s`).join(" / ") },
    ], d.runs || [], "No agent runs recorded. Older decisions may predate run tracking.")),
    card(h("h2", {}, "AI scout calls"), table([
      { label: "When", get: (r) => when(r.called_at) }, { label: "GW", get: (r) => r.gw },
      { label: "Outcome", get: (r) => r.outcome }, { label: "Model", get: (r) => r.model },
      { label: "Sent / cached", get: (r) => `${r.sent} / ${r.cached}` },
      { label: "Accepted / rejected", get: (r) => `${r.accepted} / ${r.rejected}` },
      { label: "Input / output tokens", get: (r) => `${dash(r.input_tokens)} / ${dash(r.output_tokens)}` },
      { label: "Latency (ms)", get: (r) => r.latency_ms },
    ], d.scout_calls || [], "No AI scout calls recorded. No key or eligible news may mean none are needed.")),
    card(h("h2", {}, "Decisions"), table([
      { label: "Made", get: (r) => when(r.created_at) }, { label: "GW", get: (r) => r.gw },
      { label: "Outcome", get: (r) => decisionPill(r.mode) }], d.decisions),
    note("Older rows have no entry or execution record: their outcome is unknown unless marked verified.")),
    card(h("h2", {}, "Reviews"), table([
      { label: "Made", get: (r) => when(r.created_at) }, { label: "GW", get: (r) => r.gw }, { label: "Kind", get: (r) => r.kind },
      { label: "Summary", get: (r) => Object.entries(r.summary).map(([k, v]) => `${k}: ${typeof v === "number" ? fmt(v, 2) : v}`).join(" · ") }], d.reviews)),
    card(h("h2", {}, "Lessons"), table([
      { label: "GW", get: (r) => r.gw }, { label: "Kind", get: (r) => r.kind }, { label: "Points", num: true, get: (r) => fmt(r.points) },
      { label: "Lesson", get: (r) => r.lesson || r.what }], d.lessons)),
    card(h("h2", {}, "Data loads"), table([
      { label: "When", get: (r) => when(r.run_at) }, { label: "Source", get: (r) => r.source }, { label: "Table", get: (r) => r.table_name },
      { label: "Rows", num: true, get: (r) => r.rows }, { label: "Note", get: (r) => r.note }], d.ingest)));
}

const jobPill = (s) => pill(s, s === "succeeded" ? "good" : s === "running" || s === "queued" ? "warn" : s === "failed" || s === "interrupted" ? "bad" : "");

async function controls() {
  const [prefs, jobs] = await Promise.all([api("/api/preferences"), api("/api/jobs")]);
  setStatus(null);
  const msg = h("p", { class: "note", role: "status", "aria-live": "polite" });
  const field = (label, key, attrs) => h("label", {}, label, h("input", { name: key, value: prefs[key] ?? "", inputmode: "numeric", ...attrs }));
  const form = h("form", { class: "toolbar", onsubmit: async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(form).entries());
    try {
      const saved = await api("/api/preferences", { method: "POST", body: JSON.stringify(body) });
      Object.assign(prefs, saved);
      const planButton = view.querySelector('[data-action="plan"]');
      if (planButton) { planButton.disabled = jobs.busy || !saved.entry_id; planButton.title = saved.entry_id ? "" : "Set your FPL team id first"; }
      msg.textContent = "Saved for this dashboard. Dry plan uses these settings.";
    }
    catch (err) { msg.textContent = `Not saved: ${err.message}`; }
  } },
  field("FPL team id", "entry_id", { placeholder: "e.g. 1234567", pattern: "[0-9]*" }),
  field("Season (start year)", "season", { required: true }),
  field("Horizon (GWs, 1–8)", "horizon", { required: true }),
  field("Simulations (50–5000)", "samples", { required: true }),
  h("button", { type: "submit", disabled: jobs.busy }, "Save settings"));
  const running = jobs.items.find((j) => j.status === "running" || j.status === "queued");
  const start = async (action, confirmText) => {
    if (!confirm(confirmText)) return;
    try { await api("/api/jobs", { method: "POST", body: JSON.stringify({ action }) }); render("controls", true); }
    catch (e) { msg.textContent = `Not started: ${e.message}`; }
  };
  const btn = (label, action, text, why) => h("button", { class: "primary", "data-action": action, disabled: Boolean(running) || Boolean(why), title: why || null,
    onclick: () => start(action, text) }, label);
  const last = jobs.items[0];
  if (running) pollTimer = setTimeout(() => { if (location.hash === "#controls") render("controls", true); }, 2000);
  return h("div", {},
    card(h("h2", {}, "Settings"), form, msg,
      note("Stored only for this dashboard (data_cache/dashboard/preferences.json). No secrets; GitHub Actions settings are not changed.")),
    card(h("h2", {}, "Actions"),
      h("div", { class: "row" },
        btn("Refresh data", "refresh", "Download public FPL data (prices, news, fixtures, points) into the local database?"),
        btn("Dry plan", "plan", "Run a dry plan? It reads public FPL data and your public team, and never signs in or changes anything on FPL.",
          prefs.entry_id ? null : "Set your FPL team id first"),
        btn("Review", "review", "Score the latest finished gameweek?")),
      !prefs.entry_id ? note("Set and save your FPL team ID above to enable Dry plan.") : null,
      note("Jobs always run dry: FPL sign-in, notifications and GitHub credentials are removed. There is no live-transfer button.")),
    last ? card(h("div", { class: "split" }, h("h2", {}, `Latest job: ${last.action}`), jobPill(last.status)),
      h("p", { class: "note" }, `Started ${when(last.started_at || last.created_at)}${last.ended_at ? ` · ended ${when(last.ended_at)}` : ""}${last.message ? ` · ${last.message}` : ""}`),
      running ? h("button", { onclick: async () => { try { await api(`/api/jobs/${running.id}/cancel`, { method: "POST", body: "{}" }); } catch (e) { msg.textContent = e.message; } render("controls", true); } }, "Cancel") : null,
      h("pre", { class: "log", tabindex: "0", "aria-label": "job output" }, last.output || "(no output yet)")) : null);
}

async function health() {
  const res = await api("/api/health");
  const d = res.data || {};
  const settings = d.settings ? card(h("h2", {}, "Settings (presence only)"),
    table([{ label: "Setting", get: (r) => r[0] }, { label: "State", get: (r) => r[1] }],
      [["AGENT_MODE (for scheduled runs)", d.settings.agent_mode], ["AGENT_ENABLED", d.settings.agent_enabled ? "true" : "false (kill switch on)"],
        ...Object.entries(d.settings.keys).map(([k, v]) => [k, v ? "set" : "not set"]), ["Dashboard jobs", d.settings.dashboard_jobs]])) : null;
  const stop = guard(res);
  if (stop) return h("div", {}, stop, settings);
  return h("div", {},
    h("div", { class: "grid" },
      kpi("FPL snapshot", ago(d.snapshot_age_hours), d.snapshot_stale ? pill(`stale (over ${d.stale_after_hours} h)`, "warn") : pill("fresh", "good")),
      kpi("Official points through", d.points_history_through_gw ? `GW${d.points_history_through_gw}` : "none")),
    card(h("h2", {}, "Last successful load per source"), table([
      { label: "Source", get: (r) => r.source }, { label: "Table", get: (r) => r.table_name },
      { label: "When", get: (r) => `${when(r.last_success_at)} (${ago(r.age_hours)})` },
      { label: "Rows", num: true, get: (r) => r.rows }, { label: "Note", get: (r) => r.note }], d.sources),
    note(d.source_note), note(d.api_note)),
    card(h("h2", {}, "Incremental load state"), table([
      { label: "Source", get: (r) => r.source }, { label: "Status", get: (r) => r.status },
      { label: "Items", num: true, get: (r) => r.n }, { label: "Last", get: (r) => when(r.last) }], d.ingest_state)),
    card(h("h2", {}, "News verification"),
      note("Coverage: City official news and BBC Football. Evidence is advisory; UCL availability does not confirm Premier League fitness. Failed, stale and undated sources never clear an injury."),
      table([{ label: "Source", get: (r) => r.source },
        { label: "Article / check", get: (r) => r.title || r.url },
        { label: "State", get: (r) => pill(r.state, r.state === "failed" ? "bad" : r.state === "fresh" ? "good" : "warn") },
        { label: "Published", get: (r) => r.published_at ? when(r.published_at) : "not verified" },
        { label: "Checked", get: (r) => when(r.observed_at) },
        { label: "Detail", get: (r) => r.error || r.url }], d.news || [], "No news checks stored. Run news-check locally; no external verification can be claimed yet.")),
    settings,
    card(h("h2", {}, "Tables"), table([{ label: "Table", get: (r) => r.table }, { label: "Rows", num: true, get: (r) => r.rows }], d.tables)));
}

async function openPlayer(p) {
  const previous = document.activeElement;
  // Keep the decision-linked projections on pitch cards; enrich only factual stats.
  try {
    const res = await api(`/api/players?ids=${p.player_id}`);
    const factual = res.data?.items?.[0];
    if (factual) p = { ...factual, ...p };
  } catch { /* The cached player card still opens if enrichment fails. */ }
  const close = () => { panel.remove(); document.removeEventListener("keydown", keydown); previous?.focus(); };
  const keydown = (e) => {
    if (e.key === "Escape") close();
    if (e.key === "Tab") {
      const buttons = [...panel.querySelectorAll("button,a[href],input,select")];
      const first = buttons[0], last = buttons.at(-1);
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  };
  const body = card(h("div", { class: "split" }, h("h2", { id: "player-detail-title" }, p.name), h("button", { onclick: close, "aria-label": "Close player details" }, "Close")),
    note(`${dash(p.team)} \u00b7 ${dash(p.position)} \u00b7 \u00a3${fmt(p.price)}m`),
    h("div", { class: "grid" }, kpi("Projected points", fmt(p.xp), "Prediction, not a guaranteed score"), kpi("10th-90th percentile", p.q10 == null ? "Unavailable" : `${fmt(p.q10)} - ${fmt(p.q90)}`, "Stored projection range")),
    table([{ label: "Statistic", get: (r) => r[0] }, { label: "Value", get: (r) => r[1] }],
      [["Official points (stored history)", dash(p.points)], ["Minutes (stored history)", dash(p.minutes)], ["Ownership", p.owned == null ? "Unavailable" : `${fmt(p.owned)}%`], ["Expected minutes (prediction)", fmt(p.exp_minutes, 0)]]),
    h("h3", {}, "Recent gameweeks"),
    p.history?.length ? barChart([...p.history].reverse(), (r) => `GW${r.gw}`, (r) => r.points, "Official points, aggregated per gameweek from stored fixture history.") : note("No recent gameweek history stored."),
    note(`Official FPL form: ${p.form == null ? "Unavailable" : fmt(p.form)}. Fixture difficulty ratings are not stored locally.`),
    note(p.news || "No player news stored."));
  const panel = h("div", { class: "detail-panel", role: "dialog", "aria-modal": "true", "aria-labelledby": "player-detail-title", onclick: (e) => { if (e.target === panel) close(); } }, body);
  document.body.append(panel);
  document.addEventListener("keydown", keydown);
  panel.querySelector("button").focus();
}
const SUBTITLES = {
  overview: "Every decision starts with a clearer picture.", team: "Your matchday XI. Every role, every point of difference.",
  recommendations: "The agent's stored decisions, projections and risk warnings.", players: "Find the value. Compare the numbers. Build your shortlist.",
  planning: "Look beyond the next deadline.", activity: "A transparent record of local jobs and stored decisions.",
  controls: "Prepare, run and review. You stay in control.", health: "Know what is fresh, what is missing, and what needs attention.",
};
const SECTIONS = { overview, team, recommendations, players, planning, activity, controls, health };

async function render(name, keepFocus = false) {
  clearTimeout(pollTimer);
  const seq = ++renderSeq;
  if (!SECTIONS[name]) name = "overview";
  title.textContent = NAMES[name];
  document.getElementById("crumb").textContent = NAMES[name];
  document.getElementById("subtitle").textContent = SUBTITLES[name];
  document.title = `${NAMES[name]} · FPL Agent`;
  for (const a of document.querySelectorAll(".nav a"))
    a.toggleAttribute("aria-current", a.dataset.section === name) && a.setAttribute("aria-current", "page");
  if (!keepFocus) view.replaceChildren(h("div", { class: "grid" }, [1, 2, 3].map(() => h("div", { class: "skeleton" }))));
  view.setAttribute("aria-busy", "true");
  try {
    const el = await SECTIONS[name]();
    if (seq !== renderSeq) return;
    const active = document.activeElement && document.activeElement.getAttribute("aria-label");
    view.replaceChildren(el);
    if (keepFocus && active) {
      const again = view.querySelector(`[aria-label="${CSS.escape(active)}"]`);
      if (again) { again.focus(); if (again.setSelectionRange && again.value) again.setSelectionRange(again.value.length, again.value.length); }
    }
  } catch (e) {
    if (seq !== renderSeq) return;
    view.replaceChildren(stateBox("error", "Could not load this section", e.message,
      h("button", { onclick: () => render(name) }, "Try again")));
  } finally {
    if (seq === renderSeq) view.setAttribute("aria-busy", "false");
  }
}

function route(focus) {
  const name = location.hash.slice(1) || "overview";
  render(name).then(() => { if (focus) document.getElementById("main").focus({ preventScroll: true }); window.scrollTo(0, 0); });
}
window.addEventListener("hashchange", () => route(true));
route(false);
