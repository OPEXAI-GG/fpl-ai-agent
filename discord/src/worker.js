// Discord bot for the FPL agent, run as a Cloudflare Worker (an "interactions endpoint").
// Discord sends each slash command here; the Worker checks the signature and that the sender is
// the owner, answers at once with "thinking...", then edits that message with the result.
// Quick answers (/latest, /ask, /status) come from the latest plan the agent published; work that
// needs the database (/plan, /explain) starts a GitHub Actions run, which replies in Discord itself.
import Anthropic from "@anthropic-ai/sdk";

const DISCORD_API = "https://discord.com/api/v10";
const GITHUB_API = "https://api.github.com";
const LIMIT = 1900; // Discord refuses messages over 2000 characters
const PLAN_RELEASE = "plan"; // release the plan workflow uploads latest.md / latest.json to
const BUSY = ["plan", "data", "review", "backfill"]; // workflows that share the database
const DEFERRED = { type: 5 }; // "the bot is thinking..."
const EPHEMERAL = 64;

export default {
  async fetch(request, env, ctx) {
    return handle(request, env, ctx, { fetch: globalThis.fetch.bind(globalThis), claude });
  },
};

export async function handle(request, env, ctx, deps) {
  if (request.method === "GET") return new Response("fpl-agent discord bot is running");
  if (request.method !== "POST") return new Response("method not allowed", { status: 405 });
  const body = await request.text();
  const ok = await verify(
    body,
    request.headers.get("X-Signature-Ed25519"),
    request.headers.get("X-Signature-Timestamp"),
    env.DISCORD_PUBLIC_KEY,
  );
  if (!ok) return new Response("invalid request signature", { status: 401 });
  const interaction = JSON.parse(body);
  if (interaction.type === 1) return json({ type: 1 }); // Discord's endpoint check
  if (interaction.type !== 2) return json({ type: 4, data: { content: "Not supported." } });

  const user = interaction.member?.user?.id ?? interaction.user?.id;
  const owners = (env.ALLOWED_USER_IDS || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (!owners.includes(user)) {
    const content = owners.length
      ? "This bot only takes commands from its owner."
      : "No owner is set yet (ALLOWED_USER_IDS), so the bot ignores everyone.";
    return json({ type: 4, data: { content, flags: EPHEMERAL } });
  }

  const name = interaction.data?.name;
  const args = Object.fromEntries((interaction.data?.options || []).map((o) => [o.name, o.value]));
  const run = COMMANDS[name];
  if (!run) return json({ type: 4, data: { content: `Unknown command /${name}.` } });
  const reply = replier(env, interaction.token, deps.fetch);
  ctx.waitUntil(
    run({ env, args, token: interaction.token, deps })
      .catch((err) => `Something went wrong: ${err.message || err}`)
      .then((text) => reply(text)),
  );
  return json(DEFERRED);
}

const COMMANDS = {
  async latest({ env, deps }) {
    const plan = await latestPlan(env, deps.fetch);
    return plan.md || "No plan has been published yet. It appears after the next plan run.";
  },

  async ask({ env, args, deps }) {
    const plan = await latestPlan(env, deps.fetch);
    const answer = await deps.claude(env, args.question, plan);
    return `> ${args.question}\n\n${answer}`;
  },

  async status({ env, deps }) {
    const mode = await variable(env, deps.fetch, "AGENT_MODE", "dry");
    const enabled = await variable(env, deps.fetch, "AGENT_ENABLED", "true");
    const lines = [
      enabled === "false"
        ? "**Kill switch is on**: the agent does nothing."
        : `Mode: **${mode === "live" ? "LIVE" : mode === "dry" ? "DRY" : mode}**` +
          (mode === "live" ? " (makes moves on your team)" : " (reports only)"),
    ];
    for (const wf of ["plan", "data", "review"]) {
      const res = await github(env, deps.fetch, `/actions/workflows/${wf}.yml/runs?per_page=1`);
      const r = res.ok ? (await res.json()).workflow_runs?.[0] : null;
      lines.push(r ? `${wf}: ${r.conclusion || r.status}, ${ago(r.created_at)} (<${r.html_url}>)` : `${wf}: no runs`);
    }
    return lines.join("\n");
  },

  async plan({ env, args, token, deps }) {
    // A dry run (the default) only reports: no FPL sign-in, the team is never touched, and it does
    // not queue behind the jobs that share the database. A real run follows AGENT_MODE.
    const dry = !args.real;
    const busy = dry ? null : await running(env, deps.fetch);
    if (busy) {
      return `The ${busy} job is running right now. A real run queued behind it would be skipped (it could hold an out-of-date FPL login), so try again in a few minutes.`;
    }
    const force = args.force ?? dry; // a dry run plans whenever asked; a real one keeps the window
    const inputs = { force: String(force), dry: String(dry), discord_reply: token };
    await dispatch(env, deps.fetch, "plan.yml", inputs);
    const kind = dry
      ? "a dry run (report only, your team is not touched)"
      : "a real run (in live mode it makes the moves on your team)";
    return `Started ${kind}${force ? ", forced" : ""}. I will post the report here when it finishes. Runs: <${runsUrl(env, "plan.yml")}>`;
  },

  async explain({ env, args, token, deps }) {
    await dispatch(env, deps.fetch, "discord.yml", { players: args.players, discord_reply: token });
    return `Looking up ${args.players}. The answer lands here in a few minutes.`;
  },
};

// ---- Claude ----

async function claude(env, question, plan) {
  const client = new Anthropic({ apiKey: env.ANTHROPIC_API_KEY, timeout: 25_000, maxRetries: 0 });
  const context = plan.md
    ? `Latest plan report:\n${plan.md}\n\nLatest plan data (JSON):\n${plan.json || "(none)"}`
    : "No plan has been published yet.";
  const res = await client.beta.messages.create({
    model: env.CLAUDE_MODEL || "claude-opus-5-5",
    max_tokens: 4000,
    betas: ["server-side-fallback-2026-07-01"],
    fallbacks: "default",
    output_config: { effort: "low" },
    system:
      "You are the voice of the owner's Fantasy Premier League agent, answering in Discord. " +
      "Answer from the plan below; say so plainly when it does not cover the question rather " +
      "than guessing. Keep it short and conversational (a few lines, under 1500 characters), " +
      "Discord markdown only. You cannot change the team yourself: if the owner wants a fresh " +
      "plan, point them to /plan; for why a player projects as he does, /explain. Latency-" +
      "sensitive; begin your visible answer immediately.\n\n" +
      context,
    messages: [{ role: "user", content: question }],
  });
  if (res.stop_reason === "refusal") return "I can't help with that one.";
  const text = res.content.filter((b) => b.type === "text").map((b) => b.text).join("\n").trim();
  return text || "I had nothing to say to that.";
}

// ---- GitHub ----

function github(env, fetchFn, path, init = {}) {
  return fetchFn(`${GITHUB_API}/repos/${env.GITHUB_REPO}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "fpl-agent-discord",
      "X-GitHub-Api-Version": "2022-11-28",
      ...(init.headers || {}),
    },
  });
}

async function dispatch(env, fetchFn, workflow, inputs) {
  const res = await github(env, fetchFn, `/actions/workflows/${workflow}/dispatches`, {
    method: "POST",
    body: JSON.stringify({ ref: env.GITHUB_REF || "master", inputs }),
  });
  if (!res.ok) throw new Error(`GitHub refused to start ${workflow} (HTTP ${res.status})`);
}

async function running(env, fetchFn) {
  for (const status of ["in_progress", "queued"]) {
    const res = await github(env, fetchFn, `/actions/runs?status=${status}&per_page=20`);
    if (!res.ok) continue;
    const hit = (await res.json()).workflow_runs?.find((r) => BUSY.includes(r.name));
    if (hit) return hit.name;
  }
  return null;
}

async function variable(env, fetchFn, name, fallback) {
  const res = await github(env, fetchFn, `/actions/variables/${name}`);
  if (res.status === 404) return fallback;
  if (!res.ok) return "unknown (the bot's GitHub token cannot read variables)";
  return (await res.json()).value;
}

async function latestPlan(env, fetchFn) {
  const res = await github(env, fetchFn, `/releases/tags/${PLAN_RELEASE}`);
  if (!res.ok) return {};
  const assets = (await res.json()).assets || [];
  const out = {};
  for (const [key, file] of [["md", "latest.md"], ["json", "latest.json"]]) {
    const asset = assets.find((a) => a.name === file);
    if (asset) out[key] = await download(env, fetchFn, asset.id);
  }
  return out;
}

async function download(env, fetchFn, id) {
  // GitHub answers with a redirect to signed storage, which must not receive our token
  const res = await github(env, fetchFn, `/releases/assets/${id}`, {
    headers: { Accept: "application/octet-stream" },
    redirect: "manual",
  });
  const location = res.headers.get("location");
  const file = location ? await fetchFn(location) : res;
  return file.ok ? (await file.text()).trim() : undefined;
}

function runsUrl(env, workflow) {
  return `https://github.com/${env.GITHUB_REPO}/actions/workflows/${workflow}`;
}

// ---- Discord ----

function replier(env, token, fetchFn) {
  const base = `${DISCORD_API}/webhooks/${env.DISCORD_APP_ID}/${token}`;
  return async (text) => {
    const parts = chunks(text || "(no answer)");
    const send = (url, method, content) =>
      fetchFn(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, allowed_mentions: { parse: [] } }),
      });
    await send(`${base}/messages/@original`, "PATCH", parts[0]);
    for (const part of parts.slice(1)) await send(base, "POST", part);
  };
}

export function chunks(message, limit = LIMIT) {
  const out = [];
  let cur = "";
  for (let line of message.split("\n")) {
    while (line.length > limit) {
      if (cur) out.push(cur), (cur = "");
      out.push(line.slice(0, limit));
      line = line.slice(limit);
    }
    if (cur && cur.length + 1 + line.length > limit) out.push(cur), (cur = line);
    else cur = cur ? `${cur}\n${line}` : line;
  }
  if (cur) out.push(cur);
  return out;
}

export async function verify(body, signature, timestamp, publicKey) {
  if (!signature || !timestamp || !publicKey) return false;
  try {
    const key = await crypto.subtle.importKey("raw", hex(publicKey), { name: "Ed25519" }, false, ["verify"]);
    return await crypto.subtle.verify("Ed25519", key, hex(signature), new TextEncoder().encode(timestamp + body));
  } catch {
    return false;
  }
}

function hex(s) {
  if (!/^([0-9a-f]{2})+$/i.test(s)) throw new Error("not hex");
  return Uint8Array.from(s.match(/../g), (b) => parseInt(b, 16));
}

function json(data) {
  return new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
}

function ago(iso) {
  const mins = Math.round((Date.now() - Date.parse(iso)) / 60000);
  if (mins < 60) return `${mins} min ago`;
  if (mins < 48 * 60) return `${Math.round(mins / 60)} h ago`;
  return `${Math.round(mins / 1440)} days ago`;
}
