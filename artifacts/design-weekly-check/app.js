// Weekly team check prototype. Static, no network. All data below is made up.
"use strict";

const SQUAD = [
  // name, position, club, expected points next GW (made up), status
  ["P. Ilunga", "GK", "Eastmoor", 3.9, ""],
  ["S. Rademaker", "DEF", "North Quay", 4.4, ""],
  ["L. Achterberg", "DEF", "Holt Vale", 3.8, ""],
  ["O. Mensah-Clarke", "DEF", "Redfern", 4.1, ""],
  ["F. Doyle", "DEF", "Sandmere", 3.5, ""],
  ["M. Esteves", "MID", "North Quay", 6.8, "Captain"],
  ["T. Varga", "MID", "Holt Vale", 5.6, "Suggested buy"],
  ["A. Nakamura", "MID", "Redfern", 5.1, ""],
  ["B. Quilty", "MID", "Eastmoor", 4.7, ""],
  ["J. Halloran", "FWD", "Eastmoor", 6.1, "Vice-captain"],
  ["E. Sørensen", "FWD", "Sandmere", 4.9, ""],
  ["P. Sato", "GK", "Redfern", 0.8, "Bench goalkeeper"],
  ["K. Brennan-Moss", "DEF", "Holt Vale", 2.9, "Bench 1"],
  ["Christopher Abernathy-Wolstenholme", "MID", "Sandmere", 2.4, "Bench 2"],
  ["D. Lund", "FWD", "North Quay", 2.1, "Bench 3"],
];
const SOLD = ["R. Okafor-Lind", "MID", "Redfern", 2.2, "50% doubtful"];

const TIMES = {
  fresh: { checked: "Tue 28 Oct, 18:40", age: "2 hours ago" },
  stale: { checked: "Mon 27 Oct, 09:15", age: "35 hours ago" },
  deadline: "Sat 1 Nov, 11:00",
};

const state = { teamId: null, confirmed: false, stale: false, range: false, slow: false };
const $ = (s) => document.querySelector(s);
const screens = ["start", "confirm", "empty", "week", "compare", "squad", "discover", "plan", "preview"];
const needsTeam = ["confirm", "week", "compare", "squad"];

function el(tag, props = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") n.className = v; else if (k === "text") n.textContent = v; else n.setAttribute(k, v);
  }
  for (const k of kids) n.append(k);
  return n;
}

function show(name, focus = true) {
  if (!screens.includes(name)) name = "start";
  if (needsTeam.includes(name) && !state.teamId) name = "start";
  if (name !== "confirm" && needsTeam.includes(name) && !state.confirmed) name = state.teamId ? "confirm" : "start";
  for (const s of screens) $("#screen-" + s).hidden = s !== name;
  document.querySelectorAll("[data-nav]").forEach((a) => {
    if (a.dataset.nav === name || (name === "compare" && a.dataset.nav === "week")) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  const titles = { start: "Check your team", confirm: "Confirm team", empty: "No players yet", week: "This week",
    compare: "Compare options", squad: "Squad", discover: "Discover", plan: "Plan", preview: "Ideas we are testing" };
  document.title = titles[name] + " · Weekly team check (prototype)";
  if (name === "week") renderWeek();
  if (name === "compare") renderCompare();
  if (name === "squad") renderSquad();
  if (focus) { const h = $("#screen-" + name + " h1"); h.tabIndex = -1; h.focus(); }
  window.scrollTo(0, 0);
}

function route() {
  const name = location.hash.slice(1) || "start";
  // "#main" is the skip-link target, not a screen: keep the current screen.
  if (name === "main") { $("#main").focus(); return; }
  show(name);
}

// Skip link: move focus to the content without touching the hash, so the journey is not reset.
$(".skip").addEventListener("click", (e) => { e.preventDefault(); $("#main").focus(); });

function go(name) {
  if (location.hash === "#" + name) show(name); else location.hash = name;
}

// 1. Team ID
$("#id-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("#team-id");
  const err = $("#team-id-error");
  const v = input.value.trim();
  const fail = (msg) => { err.textContent = msg; err.hidden = false; input.setAttribute("aria-invalid", "true"); input.focus(); };
  err.hidden = true; input.removeAttribute("aria-invalid");
  if (!v) return fail("Enter your team ID. It is a number, for example 1234567.");
  if (!/^\d{1,10}$/.test(v)) return fail("A team ID contains only numbers. Remove any letters, spaces or symbols.");
  const btn = e.submitter || $("#id-form .btn");
  btn.disabled = true;
  $("#loading").hidden = false;
  $("#loading-text").textContent = state.slow ? "Finding your team… this is taking longer than usual." : "Finding your team…";
  setTimeout(() => {
    btn.disabled = false;
    $("#loading").hidden = true;
    if (v === "404") return fail("We could not find team 404. Check the number on your points page and try again.");
    state.teamId = v;
    if (v === "777") { state.teamId = null; return go("empty"); }
    state.confirmed = false;
    renderConfirm();
    go("confirm");
  }, state.slow ? 5000 : 900);
});

// 2. Confirm
function renderConfirm() {
  $("#confirm-id").textContent = state.teamId;
  const list = $("#confirm-list");
  list.replaceChildren();
  for (const p of [...SQUAD.filter((p) => p[0] !== "T. Varga"), SOLD]) {
    list.append(el("li", { text: `${p[0]} · ${p[1]}` }));
  }
}
$("#confirm-yes").addEventListener("click", () => { state.confirmed = true; go("week"); });
$("#confirm-no").addEventListener("click", () => { state.teamId = null; state.confirmed = false; go("start"); $("#team-id").select(); });
document.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => go(b.dataset.go)));

// 3. This week
const RANGE_SAMPLE = "−1.5 to +5.0";
function rangeNode(sampleText) {
  if (state.range) {
    return el("span", {}, el("strong", { text: sampleText + " points" }),
      el("span", { class: "sample-tag", text: "Sample range with made-up numbers, to test how ranges read." }));
  }
  return el("span", { class: "unavailable" }, el("strong", { text: "Uncertainty unavailable" }),
    el("small", { text: "We cannot yet measure how far this could be off. Treat it as an estimate." }));
}
function renderWeek() {
  const t = state.stale ? TIMES.stale : TIMES.fresh;
  const fresh = $("#fresh");
  fresh.className = "fresh" + (state.stale ? " stale" : "");
  fresh.replaceChildren(
    el("div", {}, el("span", { class: "dot", "aria-hidden": "true" }), "Player news checked ", el("strong", { text: t.checked }), ` (${t.age})`),
    el("div", {}, "Deadline ", el("strong", { text: TIMES.deadline }), " · sample times"),
  );
  $("#stale-alert").hidden = !state.stale;
  $("#range-text").replaceChildren(rangeNode(RANGE_SAMPLE));
}
$("#recheck").addEventListener("click", () => { $("#opt-stale").checked = false; state.stale = false; renderWeek(); $("#h-week").focus(); });

// 4. Compare
function renderCompare() {
  $(".cmp-range").replaceChildren(state.range ? "144.8–153.1 (sample)" : "Unavailable");
  $(".cmp-range-b").replaceChildren(state.range ? "142.6–151.0 (sample)" : "Unavailable");
}

// 5. Squad
function shirt(club) {
  const hue = { "North Quay": "#86cfa0", Eastmoor: "#9fb7ff", "Holt Vale": "#f0c46a", Redfern: "#ff9d93", Sandmere: "#d6c2ff" }[club] || "#ccc";
  const ns = "http://www.w3.org/2000/svg";
  const s = document.createElementNS(ns, "svg");
  s.setAttribute("viewBox", "0 0 34 30"); s.setAttribute("class", "shirt"); s.setAttribute("aria-hidden", "true");
  const p = document.createElementNS(ns, "path");
  p.setAttribute("d", "M11 2 4 6 1 13l6 2v13h20V15l6-2-3-7-7-4c-1 3-3 4-6 4s-5-1-6-4z");
  p.setAttribute("fill", hue); p.setAttribute("stroke", "#121518"); p.setAttribute("stroke-width", "1.2");
  s.append(p);
  return s;
}
function renderSquad() {
  const pitch = $("#pitch");
  pitch.replaceChildren();
  // Each line is a labelled list so screen readers hear positions; the bench is an ordered list.
  const starters = SQUAD.slice(0, 11);
  const lines = { GK: "Goalkeeper", DEF: "Defenders", MID: "Midfielders", FWD: "Forwards" };
  for (const [pos, label] of Object.entries(lines)) {
    const row = el("ul", { class: "row", "aria-label": label });
    for (const p of starters.filter((p) => p[1] === pos)) row.append(playerCard(p, ""));
    pitch.append(row);
  }
  pitch.append(el("p", { class: "bench-label", id: "bench-label", text: "Bench, in order of substitution" }));
  const bench = el("ol", { class: "row bench-row", "aria-labelledby": "bench-label" });
  for (const p of SQUAD.slice(11)) bench.append(playerCard(p, p[4] === "Bench goalkeeper" ? "Bench goalkeeper: " : `Substitute ${p[4].slice(-1)}: `));
  pitch.append(bench);
  const body = $("#list-body");
  body.replaceChildren();
  for (const p of SQUAD) {
    body.append(el("tr", {}, el("td", { text: p[0] }), el("td", { text: p[1] }), el("td", { text: p[2] }),
      el("td", { text: p[3].toFixed(1) }), el("td", { text: p[4] || "—" })));
  }
}
function playerCard(p, prefix) {
  const tag = p[4] === "Captain" ? " (C)" : p[4] === "Vice-captain" ? " (V)" : "";
  const role = p[4] === "Captain" ? ", captain" : p[4] === "Vice-captain" ? ", vice-captain" : "";
  const card = el("li", { class: "pl", title: p[0] });
  // Pitch shows the surname only so it fits on phones; screen readers get the full name and role.
  const short = p[0].includes(". ") ? p[0].split(". ")[1] : p[0].split(" ").pop();
  const visible = el("span", { "aria-hidden": "true" }, el("span", { class: "n", text: short + tag }), el("span", { class: "x", text: p[3].toFixed(1) + " pts" }));
  card.append(shirt(p[2]), visible,
    el("span", { class: "sr", text: `${prefix}${p[0]}${role}, ${p[2]}, ${p[3].toFixed(1)} expected points` }));
  return card;
}
function setView(list) {
  $("#view-list").setAttribute("aria-pressed", String(list));
  $("#view-pitch").setAttribute("aria-pressed", String(!list));
  $("#list").hidden = !list;
  $("#pitch").hidden = list;
}
$("#view-list").addEventListener("click", () => setView(true));
$("#view-pitch").addEventListener("click", () => setView(false));

// Facilitator
$("#opt-stale").addEventListener("change", (e) => { state.stale = e.target.checked; route(); });
$("#opt-range").addEventListener("change", (e) => { state.range = e.target.checked; route(); });
$("#opt-slow").addEventListener("change", (e) => { state.slow = e.target.checked; });
$("#restart").addEventListener("click", () => { state.teamId = null; state.confirmed = false; $("#team-id").value = ""; go("start"); });

// Test hook: ?team=1234567 skips straight to a confirmed team (used by checks/run-checks.mjs).
const qp = new URLSearchParams(location.search);
if (qp.get("team")) { state.teamId = qp.get("team"); state.confirmed = qp.get("confirmed") === "1"; renderConfirm(); }
if (qp.get("stale") === "1") { state.stale = true; $("#opt-stale").checked = true; }
if (qp.get("range") === "1") { state.range = true; $("#opt-range").checked = true; }
if (qp.get("list") === "1") setView(true);

window.addEventListener("hashchange", route);
show(location.hash.slice(1) === "main" ? "start" : location.hash.slice(1) || "start", false);
