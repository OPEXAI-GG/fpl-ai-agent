import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { html } from "../src/html.js";
import { form, GRACE_SECONDS, paidUntil, seasonEnd, Stripe } from "../src/stripe.js";
import { handle, makeDeps } from "../src/worker.js";
import { fakeD1 } from "./fake-d1.js";

const SITE = "https://fplsim.test";
let env, deps, stripeCalls, planned, events, subs;

function player(id, name, pos, xp = 5) {
  return { id, name, team: "ARS", pos, price: 6.5, xp };
}
const XI = [
  player(1, "Keeper", "GKP"),
  ...[2, 3, 4, 5].map((i) => player(i, `Def${i}`, "DEF")),
  ...[6, 7, 8, 9].map((i) => player(i, `Mid${i}`, "MID")),
  player(10, "Striker", "FWD", 8.1),
  player(11, "Forward", "FWD", 6),
];
const FULL = {
  kind: "full", gw: 9, action: "make transfers", chip: null, xp: 61.2, xp_no_transfers: 57.0, hits: 0,
  bank_after: 0.4, ft_after: 1, transfers: [{ out: player(20, "Sold", "MID"), in: player(9, "Mid9", "MID") }],
  captain: XI[9], vice: XI[10], captain_options: [{ ...XI[9], p_haul: 0.31 }], xi: XI,
  bench: [player(12, "Sub1", "DEF"), player(13, "Sub2", "MID"), player(14, "Sub3", "FWD"), player(15, "Gk2", "GKP")],
  held: { bboost: "better in GW12" }, warnings: ["kept for ownership"], field: [], checks: [],
  horizon: [{ gw: 10, chip: null, xp: 55, hits: 0, free_transfers: 1, captain: "Striker", transfers: [] }],
};
const FREE = { kind: "free", gw: 9, captain: XI[9], vice: XI[10], xp: 54.3, captain_options: [] };

function fpl(url) {
  const id = Number(url.match(/entry\/(\d+)/)?.[1]);
  if (id === 404) return new Response("{}", { status: 404 });
  return Response.json({ name: id === 666 ? "<script>x</script>" : "Sim City FC", player_first_name: "Ada", player_last_name: "L" });
}

beforeEach(() => {
  env = {
    DB: fakeD1(), SITE_URL: SITE, DEV_LOGIN: "1", ENGINE_TOKEN: "engine-secret", SEASON_END: "2099-05-31",
    STRIPE_SECRET_KEY: "sk_test",
  };
  stripeCalls = [];
  planned = 0;
  events = {};
  subs = {};
  const fakeStripe = {
    customer: async (u) => u.stripe_customer || "cus_1",
    checkout: async (a) => (stripeCalls.push(a), { url: "https://checkout.stripe.test/s1" }),
    portal: async (c) => (stripeCalls.push({ portal: c }), { url: "https://billing.stripe.test/p1" }),
    event: async (id) => {
      if (!events[id]) throw new Error("no such event");
      return events[id];
    },
    subscription: async (id) => subs[id],
  };
  deps = makeDeps({ fetch: async (url) => fpl(String(url)), stripe: () => fakeStripe, planNow: async () => planned++ });
});

const req = (path, init = {}) => new Request(SITE + path, init);
const post = (path, fields, headers = {}) =>
  req(path, { method: "POST", body: new URLSearchParams(fields), headers: { Origin: SITE, ...headers } });
const call = (r) => handle(r, env, deps);
const engine = (path, body, token = "engine-secret") =>
  call(req(path, { method: body ? "POST" : "GET", body: body && JSON.stringify(body), headers: { Authorization: `Bearer ${token}` } }));

async function signUp(email = "ada@example.com", manager = "123") {
  const r = await call(post("/start", { email, manager }));
  assert.equal(r.status, 200);
  const link = (await r.text()).match(/href="(https:\/\/fplsim\.test\/auth\?t=[^"]+)"/)[1];
  const a = await call(req(new URL(link.replace(/&amp;/g, "&")).pathname + new URL(link).search));
  assert.equal(a.status, 303);
  return a.headers.get("Set-Cookie").split(";")[0];
}
const asUser = (cookie, path, init = {}) => call(req(path, { ...init, headers: { Cookie: cookie, Origin: SITE, ...(init.headers || {}) } }));

test("landing page sells the product and carries the disclaimer", async () => {
  const r = await call(req("/"));
  const body = await r.text();
  assert.equal(r.status, 200);
  assert.match(body, /simulated/);
  assert.match(body, /not affiliated with/);
  assert.match(body, /£29\.99/);
  assert.doesNotMatch(body, /best captains/); // no engine data yet
  assert.equal(r.headers.get("X-Frame-Options"), "DENY");
});

test("engine API needs the token, and its gameweek shows on the landing page", async () => {
  assert.equal((await engine("/api/engine/managers?gw=9", null, "wrong")).status, 401);
  assert.equal((await engine("/api/engine/managers?gw=9", null, "")).status, 401);
  const g = { gw: 9, deadline: "2026-10-17T10:00:00+00:00", captains: [{ ...XI[9], p_haul: 0.3, owned: 41.2 }] };
  assert.equal((await engine("/api/engine/gameweek", g)).status, 200);
  assert.equal((await engine("/api/engine/gameweek", { gw: "x" })).status, 400);
  const body = await (await call(req("/"))).text();
  assert.match(body, /best captains/);
  assert.match(body, /Striker/);
});

test("sign up, follow the emailed link, see the free pick, then the full plan once Pro", async () => {
  const cookie = await signUp();
  let r = await asUser(cookie, "/app");
  let body = await r.text();
  assert.match(body, /Sim City FC/);
  assert.match(body, /first plan is on its way/);

  let m = await (await engine("/api/engine/managers?gw=9")).json();
  assert.deepEqual(m.managers, [{ manager_id: 123, tier: "free", kind: null, planned_at: null }]);
  assert.equal((await engine("/api/engine/plans", { plans: [{ manager_id: 123, gw: 9, payload: FREE }] })).status, 200);
  assert.equal((await engine("/api/engine/plans", { plans: [{ manager_id: 123, gw: 9, payload: { kind: "?" } }] })).status, 400);
  body = await (await asUser(cookie, "/app")).text();
  assert.match(body, /Striker/);
  assert.match(body, /Unlock the full plan/);

  // pay for the season pass: the webhook is read back from Stripe before it counts
  r = await asUser(cookie, "/checkout", { method: "POST", body: new URLSearchParams({ plan: "season" }) });
  assert.equal(r.status, 303);
  assert.equal(r.headers.get("Location"), "https://checkout.stripe.test/s1");
  assert.equal(stripeCalls[0].kind, "season");
  events.evt_1 = {
    id: "evt_1", type: "checkout.session.completed",
    data: { object: { mode: "payment", payment_status: "paid", client_reference_id: "1", customer: "cus_1" } },
  };
  r = await call(req("/stripe/webhook", { method: "POST", body: JSON.stringify({ id: "evt_1", type: "forged" }) }));
  assert.equal(r.status, 200);
  assert.equal(planned, 1);
  r = await call(req("/stripe/webhook", { method: "POST", body: JSON.stringify({ id: "evt_1" }) }));
  assert.deepEqual(await r.json(), { ok: true, duplicate: true });
  assert.equal(planned, 1);

  m = await (await engine("/api/engine/managers?gw=9")).json();
  assert.equal(m.managers[0].tier, "pro");
  assert.equal(m.managers[0].kind, "free");
  body = await (await asUser(cookie, "/app")).text();
  assert.match(body, /full plan is being simulated/);

  await engine("/api/engine/plans", { plans: [{ manager_id: 123, gw: 9, payload: FULL }] });
  body = await (await asUser(cookie, "/app")).text();
  assert.match(body, /1 transfer/);
  assert.match(body, /Mid9/);
  assert.match(body, /Holding Bench Boost/);
  assert.match(body, /GW10/);
  assert.doesNotMatch(body, /Unlock the full plan/);
  // already Pro: checkout goes straight back
  r = await asUser(cookie, "/checkout", { method: "POST", body: new URLSearchParams({ plan: "monthly" }) });
  assert.equal(r.headers.get("Location"), "/app");
});

test("a monthly subscription turns Pro on and off with its status", async () => {
  const cookie = await signUp();
  const end = Math.floor(Date.now() / 1000) + 30 * 86400;
  subs.sub_1 = { id: "sub_1", customer: "cus_1", status: "active", items: { data: [{ current_period_end: end }] } };
  events.evt_a = {
    id: "evt_a", type: "checkout.session.completed",
    data: { object: { mode: "subscription", subscription: "sub_1", client_reference_id: "1", customer: "cus_1" } },
  };
  await call(req("/stripe/webhook", { method: "POST", body: JSON.stringify({ id: "evt_a" }) }));
  const u = env.DB.raw.prepare("SELECT * FROM users").get();
  assert.equal(u.sub_until, end + GRACE_SECONDS);
  assert.equal(u.stripe_subscription, "sub_1");
  let r = await asUser(cookie, "/billing", { method: "POST" });
  assert.equal(r.headers.get("Location"), "https://billing.stripe.test/p1");

  subs.sub_1 = { ...subs.sub_1, status: "canceled" };
  events.evt_b = { id: "evt_b", type: "customer.subscription.deleted", data: { object: { id: "sub_1" } } };
  await call(req("/stripe/webhook", { method: "POST", body: JSON.stringify({ id: "evt_b" }) }));
  assert.equal(env.DB.raw.prepare("SELECT sub_until FROM users").get().sub_until, 0);
  r = await call(req("/stripe/webhook", { method: "POST", body: JSON.stringify({ id: "evt_unknown" }) }));
  assert.equal(r.status, 400);
  r = await call(req("/stripe/webhook", { method: "POST", body: "nope" }));
  assert.equal(r.status, 400);
});

test("sign-in links work once, and login does not reveal who has an account", async () => {
  const r = await call(post("/start", { email: "Bo@Example.com ", manager: "77" }));
  const link = (await r.text()).match(/auth\?t=([^"]+)"/)[1];
  assert.equal((await call(req(`/auth?t=${link}`))).status, 303);
  assert.equal((await call(req(`/auth?t=${link}`))).status, 400);
  const unknown = await (await call(post("/login", { email: "nobody@example.com" }))).text();
  assert.match(unknown, /Link sent/);
  assert.doesNotMatch(unknown, /auth\?t=/);
  // a second link within a minute is not sent
  const again = await (await call(post("/login", { email: "bo@example.com" }))).text();
  assert.doesNotMatch(again, /auth\?t=/);
  assert.equal(env.DB.raw.prepare("SELECT email FROM users").get().email, "bo@example.com");
});

test("bad input is explained, and FPL's answer is checked", async () => {
  let body = await (await call(post("/start", { email: "not-an-email", manager: "1" }))).text();
  assert.match(body, /doesn&#39;t look right/);
  body = await (await call(post("/start", { email: "a@b.co", manager: "12ab" }))).text();
  assert.match(body, /is a number/);
  body = await (await call(post("/start", { email: "a@b.co", manager: "404" }))).text();
  assert.match(body, /no team with that manager ID/);
  assert.equal(env.DB.raw.prepare("SELECT count(*) AS n FROM users").get().n, 0);
});

test("team names from FPL are escaped, and the team can be changed", async () => {
  const cookie = await signUp("x@example.com", "666");
  let body = await (await asUser(cookie, "/app")).text();
  assert.doesNotMatch(body, /<script>x<\/script>/);
  assert.match(body, /&lt;script&gt;/);
  let r = await asUser(cookie, "/app/manager", { method: "POST", body: new URLSearchParams({ manager: "404" }) });
  assert.equal(r.status, 400);
  r = await asUser(cookie, "/app/manager", { method: "POST", body: new URLSearchParams({ manager: "555" }) });
  assert.equal(r.headers.get("Location"), "/app?saved=1");
  body = await (await asUser(cookie, "/app?saved=1")).text();
  assert.match(body, /Team saved/);
  assert.match(body, /Sim City FC/);
});

test("pages behind sign-in redirect, cross-site posts are refused, logout ends the session", async () => {
  assert.equal((await call(req("/app"))).headers.get("Location"), "/login");
  const cookie = await signUp();
  const cross = await asUser(cookie, "/checkout", { method: "POST", body: new URLSearchParams({ plan: "season" }), headers: { Origin: "https://evil.test" } });
  assert.equal(cross.status, 403);
  const out = await asUser(cookie, "/logout", { method: "POST" });
  assert.match(out.headers.get("Set-Cookie"), /Max-Age=0/);
  assert.equal((await asUser(cookie, "/app")).headers.get("Location"), "/login");
});

test("static and legal pages", async () => {
  for (const p of ["/terms", "/privacy", "/login", "/start?manager=12"]) assert.equal((await call(req(p))).status, 200, p);
  assert.equal((await call(req("/nope"))).status, 303); // signed out: to the login page
  assert.match(await (await call(req("/style.css"))).text(), /--lime/);
  assert.match(await (await call(req("/sitemap.xml"))).text(), /fplsim\.test\/terms/);
  assert.match(await (await call(req("/robots.txt"))).text(), /Disallow: \/app/);
});

test("without an email provider, and outside test mode, sign-in says it failed", async () => {
  delete env.DEV_LOGIN;
  const r = await call(post("/start", { email: "c@example.com", manager: "5" }));
  assert.equal(r.status, 502);
});

test("Stripe helpers: form encoding, period end, season end, event read back", async () => {
  assert.equal(
    form({ a: 1, line_items: [{ price: "p", quantity: 1 }], skip: undefined, m: { k: "v" } }).toString(),
    "a=1&line_items%5B0%5D%5Bprice%5D=p&line_items%5B0%5D%5Bquantity%5D=1&m%5Bk%5D=v",
  );
  assert.equal(paidUntil({ status: "active", current_period_end: 100 }), 100 + GRACE_SECONDS);
  assert.equal(paidUntil({ status: "incomplete_expired", current_period_end: 100 }), 0);
  assert.equal(seasonEnd("2027-05-31"), Date.UTC(2027, 4, 31, 23, 59, 59) / 1000);
  const seen = [];
  const s = new Stripe("sk_x", async (url, init) => {
    seen.push({ url, init });
    if (url.includes("/prices")) return Response.json({ data: [{ id: "price_1" }] });
    if (url.includes("/configurations")) return Response.json({ data: [{ id: "bpc_1" }] });
    if (url.includes("/events/bad")) return Response.json({ error: { message: "No such event" } }, { status: 404 });
    return Response.json({ id: "x", url: "https://stripe.test/x" });
  });
  await s.checkout({ kind: "monthly", customer: "cus_1", userId: 3, site: SITE, tax: true });
  const body = new URLSearchParams(seen.at(-1).init.body);
  assert.equal(body.get("mode"), "subscription");
  assert.equal(body.get("line_items[0][price]"), "price_1");
  assert.equal(body.get("automatic_tax[enabled]"), "true");
  assert.equal(seen.at(-1).init.headers.Authorization, "Bearer sk_x");
  assert.match(seen[0].url, /lookup_keys%5B0%5D=fplsim_pro_monthly/);
  await s.portal("cus_1", SITE);
  assert.equal(new URLSearchParams(seen.at(-1).init.body).get("configuration"), "bpc_1");
  await assert.rejects(s.event("bad"), /No such event/);
  assert.equal(await s.customer({ stripe_customer: "cus_9" }), "cus_9");
});

test("html escapes values but keeps nested markup", () => {
  assert.equal(html`<p>${"<b>"}${html`<i>${"&"}</i>`}${[1, 2]}${null}</p>`.toString(), "<p>&lt;b&gt;<i>&amp;</i>12</p>");
});
