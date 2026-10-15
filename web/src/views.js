// Every page of the site, rendered on the server. Text uses British spelling and prices in GBP.

import { html, raw } from "./html.js";

const FONTS =
  "https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700;12..96,800" +
  "&family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@400;500;600&display=swap";

const MARK = raw(`<svg class="brand-mark" viewBox="0 0 28 28" aria-hidden="true">
  <rect x="1" y="1" width="26" height="26" rx="8" fill="#d4ff3a"/>
  <rect x="6" y="15" width="3" height="7" rx="1" fill="#10140a"/>
  <rect x="10.5" y="10" width="3" height="12" rx="1" fill="#10140a"/>
  <rect x="15" y="6" width="3" height="16" rx="1" fill="#10140a"/>
  <rect x="19.5" y="12" width="3" height="10" rx="1" fill="#10140a"/></svg>`);

export const DISCLAIMER =
  "FPL Sim is an independent tool. It is not affiliated with, endorsed by or connected to the " +
  "Premier League or Fantasy Premier League.";

export function layout({ title, description, body, user, site }) {
  const t = title ? `${title} · FPL Sim` : "FPL Sim · the FPL simulator that plans your team";
  const desc =
    description ||
    "FPL Sim simulates every Premier League match thousands of times, then plans your FPL " +
      "transfers, captain and chips for the next six gameweeks.";
  return html`<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${t}</title>
<meta name="description" content="${desc}">
<meta property="og:title" content="${t}">
<meta property="og:description" content="${desc}">
<meta property="og:type" content="website">
${site ? html`<meta property="og:url" content="${site}"><link rel="canonical" href="${site}">` : ""}
<meta name="theme-color" content="#0b0e0c">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="${FONTS}">
<link rel="stylesheet" href="/style.css">
</head>
<body>
<header class="nav"><div class="wrap">
  <a class="brand" href="/">${MARK}<span>FPL <b>Sim</b></span></a>
  <nav class="nav-links">
    <a class="hide-sm" href="/#how">How it works</a>
    <a class="hide-sm" href="/#pricing">Pricing</a>
    ${
      user
        ? html`<a class="btn btn-sm" href="/app">My plan</a>`
        : html`<a href="/login">Log in</a><a class="btn btn-sm btn-lime" href="/start">Get my free pick</a>`
    }
  </nav>
</div></header>
<main>${body}</main>
<footer><div class="wrap">
  <div><a class="brand" href="/" style="margin-bottom:12px">${MARK}<span>FPL <b>Sim</b></span></a>
    <p class="disclaimer">${DISCLAIMER} Advice only: FPL Sim never asks for your FPL password and
    never makes changes to your team.</p></div>
  <div><a href="/#pricing">Pricing</a><a href="/terms">Terms</a><a href="/privacy">Privacy</a>
    <a href="mailto:hello@fplsim.com">Contact</a></div>
</div></footer>
<script>
for (const el of document.querySelectorAll("[data-deadline]")) {
  const d = new Date(el.dataset.deadline);
  if (!isNaN(d)) el.textContent = d.toLocaleString(undefined, { weekday: "short", day: "numeric",
    month: "short", hour: "2-digit", minute: "2-digit" });
}
</script>
</body>
</html>`;
}

const fmt = (x, d = 1) => (typeof x === "number" ? x.toFixed(d) : "–");
const pct = (x) => (typeof x === "number" ? `${Math.round(x * 100)}%` : "–");

// An illustrative points distribution: a skewed curve with its mean at `xp` and a long tail.
function bars(xp = 6.2, haul = 0.22) {
  const out = [];
  for (let pts = 0; pts <= 24; pts++) {
    const k = Math.max(1.6, xp / 1.9);
    const theta = xp / k;
    const dens = pts === 0 ? 0.08 : (pts ** (k - 1) * Math.exp(-pts / theta)) / theta ** k;
    out.push({ pts, h: dens });
  }
  const max = Math.max(...out.map((b) => b.h));
  return out.map((b) => ({
    ...b,
    h: Math.max(4, Math.round((b.h / max) * 100)),
    cls: b.pts >= 10 ? "haul" : Math.abs(b.pts - Math.round(xp)) <= 1 ? "hi" : "",
  }));
}

function simCard(top) {
  const name = top?.name || "Your captain";
  const xp = top?.xp ?? 6.4;
  const haul = top?.p_haul ?? 0.21;
  return html`<div class="sim" aria-label="Simulated points for ${name}">
    <div class="sim-head"><div><div class="sim-meta">Captain · 10,000 simulated gameweeks</div>
      <div class="sim-name">${name}${top?.team ? html` <span class="cap-sub">${top.team}</span>` : ""}</div></div>
      <span class="pill">GW sim</span></div>
    <div class="sim-bars">${bars(xp, haul).map(
      (b, i) => html`<span class="${b.cls}" style="height:${b.h}%;animation-delay:${i * 25}ms" title="${b.pts} pts"></span>`,
    )}</div>
    <div class="sim-axis"><span>0 pts</span><span>10</span><span>20+</span></div>
    <div class="sim-stats">
      <div class="stat"><b class="lime num">${fmt(xp)}</b><small>expected points</small></div>
      <div class="stat"><b class="coral num">${pct(haul)}</b><small>chance of 10+</small></div>
      <div class="stat"><b class="num">×2</b><small>with the armband</small></div>
    </div>
  </div>`;
}

function idForm(action = "/start") {
  return html`<form class="idform" action="${action}" method="get">
    <input type="text" name="manager" inputmode="numeric" pattern="[0-9]{1,9}" placeholder="Your FPL manager ID" aria-label="Your FPL manager ID" required>
    <button class="btn btn-lime" type="submit">Get my pick</button>
  </form>`;
}

export function priceCards(env, { user, pro } = {}) {
  const buy = (kind, label, cls) =>
    user
      ? pro
        ? html`<a class="btn btn-block" href="/app">You have Pro</a>`
        : html`<form method="post" action="/checkout"><input type="hidden" name="plan" value="${kind}"><button class="btn btn-block ${cls}">${label}</button></form>`
      : html`<a class="btn btn-block ${cls}" href="/start?plan=${kind}">${label}</a>`;
  return html`<div class="prices">
    <div class="price"><h3>Free</h3><div class="amount">£0</div><span class="hint">no card needed</span>
      <ul><li>This week's captain and vice-captain for your team</li><li>Your team's expected points</li>
      <li>The week's top captains across the game</li></ul>
      ${user ? html`<a class="btn btn-block" href="/app">Open my plan</a>` : html`<a class="btn btn-block" href="/start">Start free</a>`}</div>
    <div class="price best"><span class="tag">Best value</span><h3>Season pass</h3>
      <div class="amount">${env.PRICE_SEASON || "£29.99"} <small>to May</small></div><span class="hint">one payment, no renewal</span>
      <ul><li>Everything in Pro for the rest of the season</li><li>Pays for itself in about six months</li>
      <li>Nothing renews, nothing to cancel</li></ul>
      ${buy("season", "Get the season pass", "btn-lime")}</div>
    <div class="price"><h3>Pro monthly</h3><div class="amount">${env.PRICE_MONTHLY || "£4.99"} <small>/ month</small></div>
      <span class="hint">cancel any time</span>
      <ul><li>Transfers, hits and the six-week plan</li><li>When to play each chip, and why</li>
      <li>Full XI, bench order and captain options</li><li>Re-planned after team news, up to 45 minutes before the deadline</li></ul>
      ${buy("monthly", "Go Pro monthly", "")}</div>
  </div>`;
}

function captainTable(gw) {
  if (!gw?.captains?.length) return "";
  const top = Math.max(...gw.captains.map((c) => c.p_haul || 0), 0.01);
  return html`<section class="rule"><div class="wrap">
    <div class="section-head"><div><div class="kicker">Gameweek ${gw.gw} · deadline <span data-deadline="${gw.deadline}">${gw.deadline}</span></div>
      <h2>This week's best captains</h2></div>
      <p>Straight from the simulator: expected points and the chance each player hauls 10 or more.
      Updated through the week as team news lands.</p></div>
    <table class="table"><thead><tr><th></th><th>Player</th><th class="r">Expected</th><th class="opt">Chance of 10+</th><th class="r opt">Owned</th></tr></thead>
    <tbody>${gw.captains.map(
      (c, i) => html`<tr><td class="rank">${String(i + 1).padStart(2, "0")}</td>
      <td class="who"><b>${c.name}</b><small>${c.team} · ${c.pos}</small></td>
      <td class="r num">${fmt(c.xp)}</td>
      <td class="opt"><div style="display:flex;gap:10px;align-items:center"><div class="bar" style="flex:1"><i style="width:${Math.round(((c.p_haul || 0) / top) * 100)}%"></i></div><span class="num">${pct(c.p_haul)}</span></div></td>
      <td class="r num opt">${fmt(c.owned)}%</td></tr>`,
    )}</tbody></table>
  </div></section>`;
}

export function landing(env, { gw, user, pro }) {
  const body = html`
<section class="hero"><div class="wrap">
  <div>
    <span class="eyebrow">The FPL simulator</span>
    <h1>Every match, simulated <em>10,000 times.</em> Then your best move.</h1>
    <p class="lede">FPL Sim plays out each Premier League fixture thousands of times: minutes, goals,
    assists, clean sheets, bonus and cards. Then it plans your transfers, captain and chips for the
    next six gameweeks.</p>
    ${idForm()}
    <p class="hero-note">Free captain pick. No FPL password, ever. <a href="#find-id">Where's my ID?</a></p>
  </div>
  ${simCard(gw?.captains?.[0])}
</div></section>
${captainTable(gw)}
<section id="how" class="rule"><div class="wrap">
  <div class="section-head"><div><div class="kicker">How it works</div><h2>Distributions, not guesses.</h2></div>
    <p>Most tools give each player one number. FPL Sim gives you the whole range of what can happen,
    so you know which picks are safe and which are lottery tickets.</p></div>
  <div class="grid3">
    <div class="card"><div class="step-n">01</div><h3 style="margin-top:8px">Enter your manager ID</h3>
      <p>We read your public squad, bank, free transfers and chips from FPL. Nothing to install, no login to share.</p></div>
    <div class="card"><div class="step-n">02</div><h3 style="margin-top:8px">We simulate the next six weeks</h3>
      <p>Team strength, minutes, bookmaker odds and the latest team news feed a match simulator run thousands of times per fixture.</p></div>
    <div class="card"><div class="step-n">03</div><h3 style="margin-top:8px">You get the plan</h3>
      <p>Transfers worth a hit or not, the captain most likely to haul, the best week for each chip, and the bench order. You make the moves.</p></div>
  </div>
</div></section>
<section id="pricing" class="rule"><div class="wrap">
  <div class="section-head"><div><div class="kicker">Pricing</div><h2>Less than a pint a month.</h2></div>
    <p>Start with the free captain pick. Upgrade when you want the full plan.</p></div>
  ${priceCards(env, { user, pro })}
</div></section>
<section class="rule"><div class="wrap" style="max-width:860px">
  <div class="kicker">Questions</div><h2 style="margin-bottom:12px">Good to know</h2>
  <details id="find-id"><summary>Where do I find my FPL manager ID?</summary><p>Sign in to the
    Fantasy Premier League site, open the Points tab, and look at the address bar: the number after
    <span class="num">/entry/</span> is your ID.</p></details>
  <details><summary>Do you need my FPL password?</summary><p>No, and you should never give it to
    anyone. FPL Sim only reads the public information any manager can see about your team.</p></details>
  <details><summary>Does it make transfers for me?</summary><p>No. FPL Sim gives you advice; you
    make the moves yourself in the FPL app.</p></details>
  <details><summary>When is my plan updated?</summary><p>From about three days before each deadline,
    re-planned through the week, and again once the team news is in, up to 45 minutes before the
    deadline.</p></details>
  <details><summary>Can I cancel?</summary><p>Monthly Pro cancels in two clicks from your account
    and stays on until the end of the month you paid for. The season pass is a single payment that
    never renews.</p></details>
</div></section>`;
  return layout({ body, user, site: env.SITE_URL });
}

function flash(msg, kind = "") {
  return msg ? html`<div class="flash ${kind}" role="status">${msg}</div>` : "";
}

export function startPage(env, { manager = "", email = "", error, plan }) {
  const body = html`<div class="narrow"><div class="panel">
    <span class="eyebrow">Free captain pick</span>
    <h1 style="margin-top:14px">Get your plan</h1>
    <p>We email you a sign-in link, so there's no password to remember.</p>
    ${flash(error, "bad")}
    <form method="post" action="/start">
      <div class="field"><label for="m">FPL manager ID</label>
        <input id="m" type="text" name="manager" inputmode="numeric" pattern="[0-9]{1,9}" value="${manager}" required>
        <span class="hint">The number after /entry/ in the address of your FPL Points page.</span></div>
      <div class="field"><label for="e">Email</label>
        <input id="e" type="email" name="email" autocomplete="email" value="${email}" required></div>
      ${plan ? html`<input type="hidden" name="plan" value="${plan}">` : ""}
      <button class="btn btn-lime btn-block" type="submit">Email me my sign-in link</button>
    </form>
    <p class="hint" style="margin-top:16px">Already have an account? <a href="/login">Log in</a>.
    By continuing you agree to the <a href="/terms">terms</a> and <a href="/privacy">privacy policy</a>.</p>
  </div></div>`;
  return layout({ title: "Get your plan", body });
}

export function loginPage(env, { email = "", error } = {}) {
  const body = html`<div class="narrow"><div class="panel">
    <h1>Log in</h1><p>Enter your email and we'll send you a sign-in link.</p>
    ${flash(error, "bad")}
    <form method="post" action="/login">
      <div class="field"><label for="e">Email</label>
        <input id="e" type="email" name="email" autocomplete="email" value="${email}" required></div>
      <button class="btn btn-lime btn-block" type="submit">Email me a link</button>
    </form>
    <p class="hint" style="margin-top:16px">New here? <a href="/start">Get your free pick</a>.</p>
  </div></div>`;
  return layout({ title: "Log in", body });
}

export function checkEmail(env, { email, devLink }) {
  const body = html`<div class="narrow"><div class="panel">
    <span class="eyebrow">Check your inbox</span>
    <h1 style="margin-top:14px">Link sent</h1>
    <p>We sent a sign-in link to <b>${email}</b>. It works once and for 30 minutes.</p>
    ${devLink ? html`<div class="flash">Test mode, no email sent: <a href="${devLink}">sign in here</a>.</div>` : ""}
    <p class="hint">Nothing arrived? Check spam, or <a href="/login">send another</a>.</p>
  </div></div>`;
  return layout({ title: "Check your email", body });
}

export function message(env, { title, text, user, status }) {
  const body = html`<div class="narrow"><div class="panel"><h1>${title}</h1><p>${text}</p>
    <a class="btn" href="${user ? "/app" : "/"}">${user ? "Back to my plan" : "Back home"}</a></div></div>`;
  return layout({ title, body, user, status });
}

// ---- the dashboard ------------------------------------------------------------------------------
const ORDER = ["GKP", "DEF", "MID", "FWD"];

function chip(p, { captain, vice } = {}) {
  return html`<div class="chip">${captain === p.id ? html`<span class="badge">C</span>` : vice === p.id ? html`<span class="badge v">V</span>` : ""}
    <b title="${p.name}">${p.name}</b><small>${p.team} · <span class="num">${fmt(p.xp)}</span></small></div>`;
}

function pitch(plan) {
  const ids = { captain: plan.captain?.id, vice: plan.vice?.id };
  return html`<div class="pitch">${ORDER.map((pos) => {
    const row = plan.xi.filter((p) => p.pos === pos);
    return row.length ? html`<div class="line">${row.map((p) => chip(p, ids))}</div>` : "";
  })}</div>
  <div class="box-k" style="margin-top:14px">Bench, in order</div>
  <div class="bench">${plan.bench.map((p) => chip(p))}</div>`;
}

function captainBox(plan) {
  return html`<div class="box"><div class="box-k">Captain</div>
    <div class="cap" style="margin:14px 0 18px"><div class="armband">C</div><div>
      <div class="cap-name">${plan.captain.name}</div>
      <div class="cap-sub">${plan.captain.team} · ${fmt(plan.captain.xp)} expected · ×2 = ${fmt(plan.captain.xp * 2)}</div></div></div>
    <div class="cap"><div class="armband v">V</div><div><div style="font-weight:600">${plan.vice.name}</div>
      <div class="cap-sub">${plan.vice.team} · ${fmt(plan.vice.xp)} expected</div></div></div>
    ${
      plan.captain_options?.length
        ? html`<div class="box-k" style="margin-top:22px">Options</div>
      <table class="table" style="margin-top:6px"><tbody>${plan.captain_options.map(
        (o) => html`<tr><td class="who"><b>${o.name}</b><small>${o.team}</small></td><td class="r num">${fmt(o.xp)}</td><td class="r num" style="color:var(--coral)">${pct(o.p_haul)}</td></tr>`,
      )}</tbody></table>`
        : ""
    }</div>`;
}

const CHIPS = { wildcard: "Wildcard", freehit: "Free Hit", bboost: "Bench Boost", "3xc": "Triple Captain" };
const chipName = (c) => CHIPS[c] || c;

function fullPlan(plan) {
  const moves = plan.transfers?.length
    ? html`<div class="moves">${plan.transfers.map(
        (t) => html`<div class="move"><span class="out">${t.out.name}</span><span class="arrow">→</span><span class="in">${t.in.name} <small class="cap-sub">£${fmt(t.in.price)}m</small></span></div>`,
      )}</div>`
    : html`<p style="color:var(--mute);margin:0">No transfers this week: roll the free transfer.</p>`;
  const notes = [
    ...Object.entries(plan.held || {}).map(([c, why]) => `Holding ${chipName(c)}: ${why}`),
    ...(plan.warnings || []),
    ...(plan.field || []),
  ];
  return html`
  <div class="dgrid">
    <div class="box"><div class="box-k">This week</div>
      <div style="display:flex;align-items:baseline;gap:14px;margin:10px 0 18px;flex-wrap:wrap">
        <div class="big">${plan.chip ? `Play ${chipName(plan.chip)}` : plan.transfers?.length ? `${plan.transfers.length} transfer${plan.transfers.length > 1 ? "s" : ""}` : "Roll"}</div>
        <span class="cap-sub">${fmt(plan.xp)} expected${plan.hits ? html` · <span style="color:var(--coral)">−${plan.hits * 4} hit</span>` : ""} · ${
          plan.xp_no_transfers ? html`${(plan.xp - plan.xp_no_transfers >= 0 ? "+" : "") + fmt(plan.xp - plan.xp_no_transfers)} vs no moves` : ""
        }</span></div>
      ${moves}
      <div class="box-k" style="margin:22px 0 10px">Your XI</div>
      ${pitch(plan)}
    </div>
    <div style="display:grid;gap:16px;align-content:start">
      ${captainBox(plan)}
      <div class="box"><div class="box-k">Bank after moves</div><div class="big num" style="font-size:36px;margin-top:8px">£${fmt(plan.bank_after)}m</div>
        <div class="cap-sub" style="margin-top:6px">${plan.ft_after} free transfer${plan.ft_after === 1 ? "" : "s"} next week</div></div>
    </div>
  </div>
  ${
    plan.horizon?.length
      ? html`<div class="box" style="margin-top:16px"><h3>The next weeks</h3><div class="weeks">${plan.horizon.map(
          (w) => html`<div class="week"><span class="gw">GW${w.gw}</span><span class="what">${
            w.chip ? html`<b>${chipName(w.chip)}</b> · ` : ""
          }${w.transfers.length ? w.transfers.map((t, i) => html`${i ? ", " : ""}${t.out.name} → <b>${t.in.name}</b>`) : "Roll the transfer"} · captain <b>${w.captain}</b></span><span class="num">${fmt(w.xp)}</span></div>`,
        )}</div><p class="hint" style="margin-top:12px">Later weeks are re-planned every run, so they change as the news does.</p></div>`
      : ""
  }
  ${notes.length ? html`<div class="box" style="margin-top:16px"><h3>Worth knowing</h3><ul class="notes">${notes.map((n) => html`<li>${n.charAt(0).toUpperCase() + n.slice(1)}</li>`)}</ul></div>` : ""}`;
}

function teaser(env) {
  const fake = ["Out → In", "Out → In"];
  return html`<div class="box locked" style="margin-top:16px">
    <div class="blur"><h3>Transfers and the six-week plan</h3><div class="moves">${fake.map(
      () => html`<div class="move"><span class="out">Player One</span><span class="arrow">→</span><span class="in">Player Two</span></div>`,
    )}</div><div class="weeks" style="margin-top:12px">${[1, 2, 3].map(
      (i) => html`<div class="week"><span class="gw">GW+${i}</span><span class="what">Roll the transfer · captain <b>Player</b></span><span class="num">58.2</span></div>`,
    )}</div></div>
    <div class="over"><div><span class="pill pro">Pro</span><h3 style="margin-top:12px;font-size:26px">Unlock the full plan</h3>
      <p>Transfers, hits worth taking, the week to play each chip, your best XI and bench order.</p>
      <div class="row-actions" style="justify-content:center">
        <form method="post" action="/checkout"><input type="hidden" name="plan" value="season"><button class="btn btn-lime">Season pass ${env.PRICE_SEASON || "£29.99"}</button></form>
        <form method="post" action="/checkout"><input type="hidden" name="plan" value="monthly"><button class="btn">${env.PRICE_MONTHLY || "£4.99"} a month</button></form>
      </div></div></div>
  </div>`;
}

function freePlan(env, plan) {
  return html`<div class="dgrid">
    ${captainBox(plan)}
    <div class="box"><div class="box-k">Your team this week</div>
      <div class="big num" style="margin:12px 0 6px">${fmt(plan.xp)}</div>
      <div class="cap-sub">expected points from your best XI, captain doubled</div>
      <ul class="notes" style="margin-top:22px"><li>Picked from your current squad, before any transfers</li>
      <li>Updated through the week as team news lands</li>
      <li>Pro adds the transfers, chips and bench order that could raise it</li></ul></div>
  </div>
  ${teaser(env)}`;
}

export function managerForm({ manager = "", error } = {}) {
  return html`<div class="box" style="max-width:520px"><h3>Add your FPL team</h3>
    <p style="color:var(--mute);margin-top:0">Your manager ID is the number after /entry/ in the
    address of your FPL Points page.</p>${flash(error, "bad")}
    <form method="post" action="/app/manager"><div class="field"><label for="m">FPL manager ID</label>
      <input id="m" type="text" name="manager" inputmode="numeric" pattern="[0-9]{1,9}" value="${manager}" required></div>
      <button class="btn btn-lime">Save</button></form></div>`;
}

export function dashboard(env, { user, pro, plan, gw, notice, error }) {
  let main;
  if (!user.manager_id) main = managerForm({ error });
  else if (!plan)
    main = html`<div class="box empty"><h3>Your first plan is on its way</h3><p>The simulator runs
      from about three days before each deadline. Your ${pro ? "full plan" : "captain pick"} appears
      here after its next run, and we keep it fresh until the deadline.</p></div>${pro ? "" : teaser(env)}`;
  else if (plan.payload.kind === "error")
    main = html`<div class="box empty"><h3>We couldn't read your team</h3><p>${plan.payload.reason}</p></div>${managerForm({ error })}`;
  else if (plan.payload.kind === "full" && pro) main = fullPlan(plan.payload);
  else
    main = html`${pro ? html`<div class="flash good">You're on Pro. Your full plan is being simulated and replaces this pick after the next run.</div>` : ""}${freePlan(env, plan.payload)}`;
  const team = user.team_name || (user.manager_id ? `Team ${user.manager_id}` : "Welcome");
  const body = html`<div class="dash"><div class="wrap">
    ${flash(notice, "good")}${user.manager_id && error ? "" : ""}
    <div class="dash-head"><div><span class="pill ${pro ? "pro" : ""}">${pro ? "Pro" : "Free"}</span>
      <h1>${team}</h1>
      <div class="cap-sub">${plan ? html`Gameweek ${plan.gw} plan` : ""}${gw?.deadline ? html` · deadline <span data-deadline="${gw.deadline}">${gw.deadline}</span>` : ""}</div></div>
      <div class="row-actions">
        ${user.stripe_subscription || (user.stripe_customer && user.sub_until) ? html`<form method="post" action="/billing"><button class="btn btn-sm">Billing</button></form>` : ""}
        ${user.manager_id ? html`<a class="btn btn-sm" href="/app/team">Change team</a>` : ""}
        <form method="post" action="/logout"><button class="btn btn-sm">Log out</button></form>
      </div></div>
    ${main}
  </div></div>`;
  return layout({ title: "My plan", body, user });
}

export function teamPage(env, { user, error }) {
  const body = html`<div class="dash"><div class="wrap"><div class="dash-head"><div><h1>Change team</h1></div>
    <a class="btn btn-sm" href="/app">Back</a></div>${managerForm({ manager: user.manager_id || "", error })}</div></div>`;
  return layout({ title: "Change team", body, user });
}

// ---- legal ---------------------------------------------------------------------------------------
export function terms(env, { user }) {
  const body = html`<article class="prose"><h1>Terms</h1>
  <p>Last updated 15 October 2026. These terms are a starting draft and should be reviewed before launch.</p>
  <h2>What FPL Sim is</h2><p>FPL Sim gives advice for Fantasy Premier League: suggested transfers,
  captains, chips and line-ups, based on computer simulations. It is advice only. You decide what to
  do with your team, and you make every change yourself. Simulations are estimates and can be wrong;
  we do not promise any number of points or any rank.</p>
  <h2>Your account</h2><p>You sign in with a link sent to your email. You give us your FPL manager
  ID, which we use only to read the public information about your team. Never give us, or anyone,
  your FPL password.</p>
  <h2>Paying</h2><p>Pro monthly renews every month until you cancel, which you can do at any time
  from Billing in your account; Pro stays on until the end of the period you paid for. The season
  pass is a single payment that gives Pro until the date shown at checkout and does not renew.
  Prices include VAT where it applies. Payments are handled by Stripe.</p>
  <h2>Cancelling within 14 days</h2><p>Your plan is a digital service that starts straight away.
  When you pay, you ask us to start at once and agree that you lose the right to cancel within 14
  days once your first plan has been shown. If no plan has been shown yet, email us within 14 days
  for a full refund.</p>
  <h2>Fair use</h2><p>Plans are for your own use. Don't resell them or scrape the site.</p>
  <h2>Not official</h2><p>${DISCLAIMER}</p>
  <h2>Contact</h2><p>hello@fplsim.com</p></article>`;
  return layout({ title: "Terms", body, user });
}

export function privacy(env, { user }) {
  const body = html`<article class="prose"><h1>Privacy</h1>
  <p>Last updated 15 October 2026. This policy is a starting draft and should be reviewed before launch.</p>
  <h2>What we keep</h2><ul><li>Your email address, to sign you in and send your plans.</li>
  <li>Your FPL manager ID and team name, to make your plan.</li>
  <li>Whether you have Pro, and the Stripe customer reference for your payments. We never see or
  store your card details; Stripe does.</li></ul>
  <h2>Why</h2><p>To provide the service you asked for (the contract between us), and to meet our
  legal duties, such as keeping payment records.</p>
  <h2>Who else sees it</h2><p>Cloudflare hosts the site and database, Stripe takes payments, and
  Resend sends our emails. We don't sell your data and we don't use advertising trackers.</p>
  <h2>Cookies</h2><p>One cookie that keeps you signed in. Nothing else.</p>
  <h2>Your rights</h2><p>You can ask for a copy of your data, or for it to be deleted, by emailing
  hello@fplsim.com. You can also complain to the UK Information Commissioner's Office.</p></article>`;
  return layout({ title: "Privacy", body, user });
}

export const FAVICON = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 28 28"><rect x="1" y="1" width="26" height="26" rx="8" fill="#d4ff3a"/><rect x="6" y="15" width="3" height="7" rx="1" fill="#10140a"/><rect x="10.5" y="10" width="3" height="12" rx="1" fill="#10140a"/><rect x="15" y="6" width="3" height="16" rx="1" fill="#10140a"/><rect x="19.5" y="12" width="3" height="10" rx="1" fill="#10140a"/></svg>`;
