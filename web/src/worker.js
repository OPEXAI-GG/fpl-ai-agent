// FPL Sim: the website on a Cloudflare Worker with a D1 database.
//
// Pages: / (landing), /start and /login (emailed sign-in link), /app (this week's plan),
// /checkout and /billing (Stripe), /terms, /privacy.
// Stripe: /stripe/webhook (each event is read back from Stripe before it is trusted).
// Engine: /api/engine/* with `Authorization: Bearer ENGINE_TOKEN` (fpl_agent/site.py).

import { cleanEmail, EMAIL_RE, now, RESEND_GAP_SECONDS, readCookie, same, sessionCookie } from "./auth.js";
import * as db from "./db.js";
import { sendLink } from "./email.js";
import { lookupManager, MANAGER_RE } from "./fpl.js";
import { paidUntil, seasonEnd, Stripe } from "./stripe.js";
import { CSS } from "./style.js";
import * as views from "./views.js";

const HTML = { "Content-Type": "text/html; charset=utf-8" };
const SECURITY = {
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "same-origin",
  "X-Frame-Options": "DENY",
  "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
};

const page = (markup, status = 200, headers = {}) =>
  new Response(markup.toString(), { status, headers: { ...HTML, ...SECURITY, ...headers } });
const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const redirect = (to, headers = {}) =>
  new Response(null, { status: 303, headers: { Location: to, ...headers } });

const siteUrl = (env, req) => (env.SITE_URL || new URL(req.url).origin).replace(/\/$/, "");

async function formOf(req) {
  const f = await req.formData();
  return Object.fromEntries([...f.entries()].map(([k, v]) => [k, String(v)]));
}

// A form post must come from this site (cookies are SameSite=Lax, this is a second check).
function sameOrigin(req, env) {
  const origin = req.headers.get("Origin");
  return !origin || origin === new URL(req.url).origin || origin === siteUrl(env, req);
}

// ---- sign-in -----------------------------------------------------------------------------------
async function emailLink(env, req, email, deps) {
  const t = await db.newLink(env.DB, email);
  const link = `${siteUrl(env, req)}/auth?t=${encodeURIComponent(t)}`;
  if (env.RESEND_API_KEY) {
    await sendLink(env, email, link, deps.fetch);
    return null;
  }
  if (env.DEV_LOGIN === "1") return link; // local and test setups only: the link is shown
  throw new Error("RESEND_API_KEY is not set");
}

async function start(req, env, deps) {
  const f = await formOf(req);
  const email = cleanEmail(f.email);
  const manager = String(f.manager || "").trim();
  const plan = f.plan === "monthly" || f.plan === "season" ? f.plan : undefined;
  const again = (error) => page(views.startPage(env, { manager, email, error, plan }), 400);
  if (!EMAIL_RE.test(email)) return again("That email address doesn't look right.");
  if (!MANAGER_RE.test(manager)) return again("Your manager ID is a number, up to nine digits.");
  const m = await lookupManager(Number(manager), deps.fetch);
  if (!m) return again("FPL has no team with that manager ID. Check the number and try again.");
  const user = await db.ensureUser(env.DB, email);
  if (!user.manager_id) await db.setManager(env.DB, user.id, m);
  return sendAndShow(req, env, deps, email);
}

async function sendAndShow(req, env, deps, email) {
  if (await db.recentLink(env.DB, email, RESEND_GAP_SECONDS))
    return page(views.checkEmail(env, { email }));
  try {
    const devLink = await emailLink(env, req, email, deps);
    return page(views.checkEmail(env, { email, devLink }));
  } catch (err) {
    console.error("sign-in email failed", err?.message);
    return page(
      views.message(env, { title: "Email failed", text: "We couldn't send your sign-in link. Please try again in a minute." }),
      502,
    );
  }
}

async function login(req, env, deps) {
  const email = cleanEmail((await formOf(req)).email);
  if (!EMAIL_RE.test(email))
    return page(views.loginPage(env, { email, error: "That email address doesn't look right." }), 400);
  // the same answer whether or not the email has an account (no account discovery)
  if (!(await db.userByEmail(env.DB, email))) return page(views.checkEmail(env, { email }));
  return sendAndShow(req, env, deps, email);
}

async function auth(req, env) {
  const t = new URL(req.url).searchParams.get("t") || "";
  const email = t && (await db.useLink(env.DB, t));
  if (!email)
    return page(
      views.message(env, { title: "Link expired", text: "That sign-in link has been used or has expired. Ask for a new one from the log in page." }),
      400,
    );
  const user = await db.ensureUser(env.DB, email);
  const s = await db.newSession(env.DB, user.id);
  return redirect("/app", { "Set-Cookie": sessionCookie(s) });
}

// ---- payments ----------------------------------------------------------------------------------
async function checkout(req, env, user, deps) {
  const kind = (await formOf(req)).plan;
  if (kind !== "monthly" && kind !== "season") return redirect("/app");
  if (db.isPro(user)) return redirect("/app");
  const stripe = deps.stripe(env);
  const customer = await stripe.customer(user);
  if (customer !== user.stripe_customer) await db.setBilling(env.DB, user.id, { stripe_customer: customer });
  const s = await stripe.checkout({
    kind,
    customer,
    userId: user.id,
    site: siteUrl(env, req),
    tax: env.STRIPE_TAX === "on",
  });
  return redirect(s.url);
}

async function billing(req, env, user, deps) {
  if (!user.stripe_customer) return redirect("/app");
  const s = await deps.stripe(env).portal(user.stripe_customer, `${siteUrl(env, req)}/app`);
  return redirect(s.url);
}

async function applySubscription(env, stripe, sub, userId) {
  const user = userId ? await db.userById(env.DB, userId) : await db.userByCustomer(env.DB, sub.customer);
  if (!user) return;
  await db.setBilling(env.DB, user.id, {
    stripe_customer: sub.customer,
    stripe_subscription: sub.id,
    sub_until: paidUntil(sub),
  });
}

export async function handleEvent(env, stripe, ev, deps) {
  const o = ev.data?.object || {};
  if (ev.type === "checkout.session.completed" || ev.type === "checkout.session.async_payment_succeeded") {
    const userId = Number(o.client_reference_id || o.metadata?.user_id);
    if (!userId) return;
    if (o.mode === "payment" && o.payment_status === "paid") {
      await db.setBilling(env.DB, userId, {
        stripe_customer: o.customer,
        pass_until: seasonEnd(env.SEASON_END || "2027-05-31"),
      });
    } else if (o.mode === "subscription" && o.subscription) {
      await applySubscription(env, stripe, await stripe.subscription(o.subscription), userId);
    } else return;
    await deps.planNow(env); // a new Pro gets the full plan without waiting for the next run
  } else if (ev.type.startsWith("customer.subscription.")) {
    await applySubscription(env, stripe, await stripe.subscription(o.id), null);
  } else if (ev.type === "invoice.paid" && o.subscription) {
    const id = typeof o.subscription === "string" ? o.subscription : o.subscription.id;
    await applySubscription(env, stripe, await stripe.subscription(id), null);
  }
}

async function webhook(req, env, deps) {
  let id;
  try {
    id = (await req.json()).id;
  } catch {
    return json({ error: "bad body" }, 400);
  }
  if (typeof id !== "string" || !id.startsWith("evt_")) return json({ error: "bad event" }, 400);
  if (await db.eventDone(env.DB, id)) return json({ ok: true, duplicate: true });
  const stripe = deps.stripe(env);
  let ev;
  try {
    ev = await stripe.event(id); // the copy Stripe holds, not what was posted
  } catch {
    return json({ error: "unknown event" }, 400);
  }
  await handleEvent(env, stripe, ev, deps);
  await db.markEvent(env.DB, id);
  return json({ ok: true });
}

// Starts the site-sync workflow so a new subscriber's full plan is made now (optional).
export async function dispatchPlan(env, fetcher = fetch) {
  if (!env.GITHUB_DISPATCH_TOKEN || !env.GITHUB_REPO) return;
  try {
    await fetcher(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/site.yml/dispatches`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_DISPATCH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "fplsim",
      },
      body: JSON.stringify({ ref: env.GITHUB_REF || "master", inputs: { force: "false" } }),
    });
  } catch (err) {
    console.error("plan dispatch failed", err?.message);
  }
}

// ---- the engine's API --------------------------------------------------------------------------
async function engine(req, env, path) {
  const bearer = (req.headers.get("Authorization") || "").replace(/^Bearer /, "");
  if (!same(bearer, env.ENGINE_TOKEN)) return json({ error: "unauthorised" }, 401);
  const url = new URL(req.url);
  if (path === "/api/engine/managers" && req.method === "GET") {
    const gw = Number(url.searchParams.get("gw") || 0);
    return json({ managers: await db.managers(env.DB, gw) });
  }
  if (path === "/api/engine/gameweek" && req.method === "POST") {
    const g = await req.json();
    if (!Number.isInteger(g.gw) || typeof g.deadline !== "string") return json({ error: "bad gameweek" }, 400);
    await db.saveGameweek(env.DB, g);
    return json({ ok: true });
  }
  if (path === "/api/engine/plans" && req.method === "POST") {
    const { plans } = await req.json();
    const ok = Array.isArray(plans) && plans.every(
      (p) => Number.isInteger(p.manager_id) && Number.isInteger(p.gw) && ["free", "full", "error"].includes(p.payload?.kind),
    );
    if (!ok) return json({ error: "bad plans" }, 400);
    await db.savePlans(env.DB, plans);
    return json({ ok: true, saved: plans.length });
  }
  return json({ error: "not found" }, 404);
}

// ---- routing -----------------------------------------------------------------------------------
async function route(req, env, deps) {
  const url = new URL(req.url);
  const path = url.pathname.replace(/\/+$/, "") || "/";
  const method = req.method;

  if (path === "/style.css")
    return new Response(CSS, { headers: { "Content-Type": "text/css", "Cache-Control": "public, max-age=3600" } });
  if (path === "/favicon.svg")
    return new Response(views.FAVICON, { headers: { "Content-Type": "image/svg+xml", "Cache-Control": "public, max-age=86400" } });
  if (path === "/robots.txt") return new Response(`User-agent: *\nDisallow: /app\nDisallow: /api\nSitemap: ${siteUrl(env, req)}/sitemap.xml\n`);
  if (path === "/sitemap.xml") {
    const base = siteUrl(env, req);
    const urls = ["/", "/start", "/terms", "/privacy"].map((p) => `<url><loc>${base}${p}</loc></url>`).join("");
    return new Response(`<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${urls}</urlset>`, {
      headers: { "Content-Type": "application/xml" },
    });
  }
  if (path === "/healthz") return json({ ok: true });
  if (path.startsWith("/api/engine/")) return engine(req, env, path);
  if (path === "/stripe/webhook" && method === "POST") return webhook(req, env, deps);

  if (method === "POST" && !sameOrigin(req, env)) return json({ error: "cross-site form" }, 403);
  const cookie = readCookie(req);
  const user = await db.sessionUser(env.DB, cookie);
  const pro = db.isPro(user);

  if (path === "/" && method === "GET") return page(views.landing(env, { gw: await db.latestGameweek(env.DB), user, pro }));
  if (path === "/terms") return page(views.terms(env, { user }));
  if (path === "/privacy") return page(views.privacy(env, { user }));
  if (path === "/start" && method === "GET") {
    if (user) return redirect("/app");
    const manager = (url.searchParams.get("manager") || "").trim();
    const plan = url.searchParams.get("plan") || undefined;
    return page(views.startPage(env, { manager, plan }));
  }
  if (path === "/start" && method === "POST") return start(req, env, deps);
  if (path === "/login" && method === "GET") return user ? redirect("/app") : page(views.loginPage(env));
  if (path === "/login" && method === "POST") return login(req, env, deps);
  if (path === "/auth" && method === "GET") return auth(req, env);

  if (!user) return redirect("/login");
  if (path === "/logout" && method === "POST") {
    await db.endSession(env.DB, cookie);
    return redirect("/", { "Set-Cookie": sessionCookie("", 0) });
  }
  if (path === "/app" && method === "GET") {
    const plan = user.manager_id ? await db.planFor(env.DB, user.manager_id) : null;
    const gw = await db.latestGameweek(env.DB);
    const notice = url.searchParams.get("paid")
      ? "Payment received. Welcome to Pro."
      : url.searchParams.get("saved")
        ? "Team saved."
        : null;
    return page(views.dashboard(env, { user, pro, plan, gw, notice }));
  }
  if (path === "/app/team" && method === "GET") return page(views.teamPage(env, { user }));
  if (path === "/app/manager" && method === "POST") {
    const manager = String((await formOf(req)).manager || "").trim();
    const fail = (error) => page(views.teamPage(env, { user, error }), 400);
    if (!MANAGER_RE.test(manager)) return fail("Your manager ID is a number, up to nine digits.");
    const m = await lookupManager(Number(manager), deps.fetch);
    if (!m) return fail("FPL has no team with that manager ID.");
    await db.setManager(env.DB, user.id, m);
    return redirect("/app?saved=1");
  }
  if (path === "/checkout" && method === "POST") return checkout(req, env, user, deps);
  if (path === "/billing" && method === "POST") return billing(req, env, user, deps);
  return page(views.message(env, { title: "Not found", text: "There's nothing at this address.", user }), 404);
}

export function makeDeps(overrides = {}) {
  return {
    fetch: (...a) => fetch(...a),
    stripe: (env) => new Stripe(env.STRIPE_SECRET_KEY),
    planNow: (env) => dispatchPlan(env),
    ...overrides,
  };
}

export async function handle(req, env, deps = makeDeps()) {
  try {
    return await route(req, env, deps);
  } catch (err) {
    console.error("request failed", err?.stack || err);
    return page(views.message(env, { title: "Something went wrong", text: "Please try again. If it keeps happening, email hello@fplsim.com." }), 500);
  }
}

export default {
  fetch: (req, env) => handle(req, env),
};

export { now };
