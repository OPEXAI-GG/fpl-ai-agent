import assert from "node:assert/strict";
import { test } from "node:test";

import { chunks, handle, verify } from "../src/worker.js";

const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
const publicKey = Buffer.from(await crypto.subtle.exportKey("raw", pair.publicKey)).toString("hex");
const env = {
  DISCORD_PUBLIC_KEY: publicKey,
  DISCORD_APP_ID: "app1",
  ALLOWED_USER_IDS: "owner",
  GITHUB_REPO: "me/repo",
  GITHUB_TOKEN: "gh-token",
};

async function signed(payload, { key = pair.privateKey, ts = "1700000000" } = {}) {
  const body = JSON.stringify(payload);
  const sig = await crypto.subtle.sign("Ed25519", key, new TextEncoder().encode(ts + body));
  return new Request("https://bot.test/", {
    method: "POST",
    body,
    headers: { "X-Signature-Ed25519": Buffer.from(sig).toString("hex"), "X-Signature-Timestamp": ts },
  });
}

function command(name, options = [], user = "owner") {
  return { type: 2, token: "itok", member: { user: { id: user } }, data: { name, options } };
}

// A fake network: `routes` maps "METHOD url-fragment" to a Response factory; every call is kept.
function net(routes = {}) {
  const calls = [];
  const fetch = async (url, init = {}) => {
    const method = init.method || "GET";
    calls.push({ url, method, init, body: init.body ? JSON.parse(init.body) : undefined });
    for (const [key, make] of Object.entries(routes)) {
      const [m, frag] = key.split(" ");
      if (m === method && url.includes(frag)) return make(url, init);
    }
    if (url.startsWith("https://discord.com")) return new Response(null, { status: 204 });
    return new Response("{}", { status: 404 });
  };
  return { fetch, calls };
}

async function run(payload, deps) {
  const pending = [];
  const res = await handle(await signed(payload), env, { waitUntil: (p) => pending.push(p) }, deps);
  await Promise.all(pending);
  return res;
}

const discordEdits = (calls) => calls.filter((c) => c.url.startsWith("https://discord.com"));
const ok = (data) => () => new Response(JSON.stringify(data), { status: 200 });

test("answers Discord's endpoint check and rejects unsigned or forged requests", async () => {
  const res = await run({ type: 1 }, net());
  assert.deepEqual(await res.json(), { type: 1 });
  const other = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const forged = await handle(await signed({ type: 1 }, { key: other.privateKey }), env, {}, net());
  assert.equal(forged.status, 401);
  const bare = new Request("https://bot.test/", { method: "POST", body: "{}" });
  assert.equal((await handle(bare, env, {}, net())).status, 401);
  assert.equal(await verify("{}", "zz", "1", publicKey), false); // malformed signature
});

test("only the owner can use it, and with no owner set nobody can", async () => {
  const n = net();
  const res = await run(command("plan", [], "stranger"), n);
  const data = (await res.json()).data;
  assert.match(data.content, /only takes commands from its owner/);
  assert.equal(data.flags, 64);
  assert.equal(n.calls.length, 0); // nothing was started
  const pending = [];
  const noOwner = await handle(await signed(command("plan")), { ...env, ALLOWED_USER_IDS: "" },
    { waitUntil: (p) => pending.push(p) }, n);
  assert.match((await noOwner.json()).data.content, /No owner is set/);
});

test("/plan starts a dry run by default, with the reply token", async () => {
  const n = net({ "POST dispatches": () => new Response(null, { status: 204 }) });
  const res = await run(command("plan"), n);
  assert.deepEqual(await res.json(), { type: 5 });
  const start = n.calls.find((c) => c.url.endsWith("/actions/workflows/plan.yml/dispatches"));
  assert.deepEqual(start.body, {
    ref: "master",
    inputs: { force: "true", dry: "true", discord_reply: "itok" },
  });
  assert.equal(start.init.headers.Authorization, "Bearer gh-token");
  assert.ok(!n.calls.some((c) => c.url.includes("status=in_progress"))); // a dry run never waits
  const [edit] = discordEdits(n.calls);
  assert.equal(edit.url, "https://discord.com/api/v10/webhooks/app1/itok/messages/@original");
  assert.equal(edit.method, "PATCH");
  assert.match(edit.body.content, /Started a dry run .*, forced/);
});

test("/plan force:false keeps the deadline window on a dry run", async () => {
  const n = net({ "POST dispatches": () => new Response(null, { status: 204 }) });
  await run(command("plan", [{ name: "force", value: false }]), n);
  const start = n.calls.find((c) => c.url.includes("plan.yml/dispatches"));
  assert.equal(start.body.inputs.force, "false");
});

test("/plan real:true starts a real run", async () => {
  const n = net({ "POST dispatches": () => new Response(null, { status: 204 }) });
  await run(command("plan", [{ name: "real", value: true }]), n);
  const start = n.calls.find((c) => c.url.includes("plan.yml/dispatches"));
  assert.deepEqual(start.body.inputs, { force: "false", dry: "false", discord_reply: "itok" });
  assert.match(discordEdits(n.calls)[0].body.content, /Started a real run/);
});

test("a real /plan waits when another job holds the database", async () => {
  const n = net({ "GET status=in_progress": ok({ workflow_runs: [{ name: "data" }] }) });
  await run(command("plan", [{ name: "real", value: true }]), n);
  assert.ok(!n.calls.some((c) => c.url.includes("dispatches")));
  assert.match(discordEdits(n.calls)[0].body.content, /The data job is running/);
});

test("a refused start is reported in Discord, not swallowed", async () => {
  const n = net({ "POST dispatches": () => new Response("no", { status: 403 }) });
  await run(command("explain", [{ name: "players", value: "Salah" }]), n);
  assert.match(discordEdits(n.calls)[0].body.content, /GitHub refused to start discord.yml \(HTTP 403\)/);
});

test("/explain starts the bot workflow", async () => {
  const n = net({ "POST dispatches": () => new Response(null, { status: 204 }) });
  await run(command("explain", [{ name: "players", value: "Salah, Haaland" }]), n);
  const start = n.calls.find((c) => c.url.includes("discord.yml/dispatches"));
  assert.deepEqual(start.body.inputs, { players: "Salah, Haaland", discord_reply: "itok" });
});

const release = {
  "GET /releases/tags/plan": ok({ assets: [{ id: 7, name: "latest.md" }, { id: 8, name: "latest.json" }] }),
  "GET /releases/assets/7": () => new Response(null, { status: 302, headers: { location: "https://store.test/md" } }),
  "GET /releases/assets/8": () => new Response(null, { status: 302, headers: { location: "https://store.test/json" } }),
  "GET store.test/md": () => new Response("GW6 plan: captain Haaland\n"),
  "GET store.test/json": () => new Response('{"gw": 6}'),
};

test("/latest posts the published report, and the storage link never gets the token", async () => {
  const n = net(release);
  await run(command("latest"), n);
  assert.equal(discordEdits(n.calls)[0].body.content, "GW6 plan: captain Haaland");
  const store = n.calls.filter((c) => c.url.startsWith("https://store.test"));
  assert.ok(store.length && store.every((c) => c.init.headers === undefined));
  const empty = net();
  await run(command("latest"), empty);
  assert.match(discordEdits(empty.calls)[0].body.content, /No plan has been published yet/);
});

test("/ask gives Claude the latest plan and posts its answer", async () => {
  const n = net(release);
  let seen;
  const claude = async (_env, question, plan) => ((seen = { question, plan }), "Captain Haaland.");
  await run(command("ask", [{ name: "question", value: "Who's captain?" }]), { ...n, claude });
  assert.deepEqual(seen, { question: "Who's captain?", plan: { md: "GW6 plan: captain Haaland", json: '{"gw": 6}' } });
  assert.equal(discordEdits(n.calls)[0].body.content, "> Who's captain?\n\nCaptain Haaland.");
});

test("/status shows the mode and the last runs", async () => {
  const when = new Date(Date.now() - 3 * 3600_000).toISOString();
  const n = net({
    "GET /actions/variables/AGENT_MODE": ok({ value: "live" }),
    "GET /runs?per_page=1": ok({ workflow_runs: [{ conclusion: "success", created_at: when, html_url: "https://gh/run" }] }),
  });
  await run(command("status"), n);
  const text = discordEdits(n.calls)[0].body.content;
  assert.match(text, /Mode: \*\*LIVE\*\*/);
  assert.match(text, /plan: success, 3 h ago/);
});

test("long answers are split into follow-up messages", async () => {
  const long = Array.from({ length: 80 }, (_, i) => `line ${i} ${"x".repeat(40)}`).join("\n");
  const n = net({ "GET /releases/tags/plan": ok({ assets: [] }) });
  await run(command("ask", [{ name: "question", value: "q" }]), { ...n, claude: async () => long });
  const sent = discordEdits(n.calls);
  assert.ok(sent.length > 1 && sent.slice(1).every((c) => c.method === "POST"));
  assert.ok(sent.every((c) => c.body.content.length <= 1900));
  assert.deepEqual(chunks("y".repeat(4000), 1900).map((p) => p.length), [1900, 1900, 200]);
});

// ---- the /ask context: compact, dated, facts kept ----
test("the /ask context is at least 30% smaller and keeps the plan's facts", async () => {
  const { planContext } = await import("../src/worker.js");
  const { readFileSync } = await import("node:fs");
  const md = readFileSync(new URL("./fixtures/plan.md", import.meta.url), "utf8").trim();
  const json = readFileSync(new URL("./fixtures/plan.json", import.meta.url), "utf8").trim();
  const before = `Latest plan report:\n${md}\n\nLatest plan data (JSON):\n${json}`;
  const after = planContext({ md, json, updatedAt: "2026-10-20T12:00:00Z" }, Date.parse("2026-10-20T15:00:00Z"));
  const saved = 1 - Buffer.byteLength(after) / Buffer.byteLength(before);
  assert.ok(saved >= 0.3, `only ${(saved * 100).toFixed(0)}% smaller`);
  assert.ok(after.includes(md), "the full report (every name, transfer and captain) is kept");
  assert.ok(after.includes('"chip":"wildcard"') && after.includes('"mode":"dry"'));
  assert.ok(after.includes("3.0 hours before this question"));
});

test("the /ask context says plainly when there is no plan or no publish time", async () => {
  const { planContext } = await import("../src/worker.js");
  assert.equal(planContext({}), "No plan has been published yet.");
  assert.ok(planContext({ md: "x", json: "not json" }).includes("Data age: unknown"));
});
