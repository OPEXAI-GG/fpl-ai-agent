// Local preview with sample data: node --no-warnings scripts/preview.mjs, then open
// http://localhost:8787. Uses an in-memory database and test-mode sign-in (no email, no Stripe).

import { createServer } from "node:http";

import { fakeD1 } from "../test/fake-d1.js";
import { handle, makeDeps } from "../src/worker.js";

const env = { DB: fakeD1(), DEV_LOGIN: "1", ENGINE_TOKEN: "dev", SITE_URL: "http://localhost:8787" };
const p = (id, name, team, pos, xp, price = 6.0) => ({ id, name, team, pos, xp, price });
const captains = [
  p(1, "Haaland", "MCI", "FWD", 8.4, 15.1), p(2, "Salah", "LIV", "MID", 7.9, 14.4),
  p(3, "Palmer", "CHE", "MID", 6.8, 10.6), p(4, "Saka", "ARS", "MID", 6.5, 10.2),
  p(5, "Isak", "NEW", "FWD", 6.3, 9.0), p(6, "Watkins", "AVL", "FWD", 5.9, 8.8),
  p(7, "Mbeumo", "MUN", "MID", 5.6, 8.1), p(8, "Gordon", "NEW", "MID", 5.2, 7.6),
].map((c, i) => ({ ...c, p_haul: [0.34, 0.31, 0.24, 0.22, 0.23, 0.19, 0.17, 0.15][i], owned: [62, 48, 31, 27, 19, 14, 22, 9][i] }));
const deadline = new Date(Date.now() + 2 * 86400e3).toISOString();
const xi = [
  p(20, "Raya", "ARS", "GKP", 4.6), p(21, "Gabriel", "ARS", "DEF", 5.1), p(22, "Virgil", "LIV", "DEF", 4.8),
  p(23, "Gvardiol", "MCI", "DEF", 4.5), p(2, "Salah", "LIV", "MID", 7.9), p(3, "Palmer", "CHE", "MID", 6.8),
  p(4, "Saka", "ARS", "MID", 6.5), p(8, "Gordon", "NEW", "MID", 5.2), p(1, "Haaland", "MCI", "FWD", 8.4),
  p(5, "Isak", "NEW", "FWD", 6.3), p(6, "Watkins", "AVL", "FWD", 5.9),
];
const full = {
  kind: "full", gw: 9, action: "make transfers", chip: null, xp: 63.4, xp_no_transfers: 58.9, hits: 0,
  bank_after: 0.7, ft_after: 1,
  transfers: [{ out: p(30, "Rogers", "AVL", "MID", 3.1, 7.0), in: p(8, "Gordon", "NEW", "MID", 5.2, 7.6) }],
  captain: xi[8], vice: xi[4],
  captain_options: [{ ...xi[8], p_haul: 0.34 }, { ...xi[4], p_haul: 0.31 }, { ...xi[5], p_haul: 0.24 }],
  xi, bench: [p(31, "Lewis", "MCI", "DEF", 3.2), p(32, "Wharton", "CRY", "MID", 2.9), p(33, "Strand Larsen", "WOL", "FWD", 2.4), p(34, "Henderson", "CRY", "GKP", 2.1)],
  held: { bboost: "worth 9.8 more in GW12, when four of your bench have two fixtures" },
  warnings: ["kept for ownership (popular players are not sold without your OK): Haaland 62%"],
  field: ["your differentials (under 5% owned, upside): Gvardiol 4%"], checks: [],
  horizon: [
    { gw: 10, chip: null, xp: 57.2, hits: 0, free_transfers: 1, captain: "Salah", transfers: [] },
    { gw: 11, chip: null, xp: 59.0, hits: 0, free_transfers: 2, captain: "Haaland", transfers: [{ out: p(33, "Strand Larsen", "WOL", "FWD"), in: p(35, "Wood", "NFO", "FWD") }] },
    { gw: 12, chip: "bboost", xp: 74.6, hits: 0, free_transfers: 1, captain: "Haaland", transfers: [] },
  ],
};
const free = { kind: "free", gw: 9, captain: xi[8], vice: xi[4], xp: 58.9, captain_options: full.captain_options };
const deps = makeDeps({ fetch: async () => Response.json({ name: "Sim City FC", player_first_name: "Abdalla" }) });

async function seed() {
  const call = (path, body) => handle(new Request(`http://localhost:8787${path}`, { method: "POST", body: JSON.stringify(body), headers: { Authorization: "Bearer dev" } }), env, deps);
  await call("/api/engine/gameweek", { gw: 9, deadline, captains });
  await call("/api/engine/plans", { plans: [{ manager_id: 1001, gw: 9, payload: free }, { manager_id: 1002, gw: 9, payload: full }] });
  const now = Math.floor(Date.now() / 1000);
  env.DB.raw.prepare("INSERT INTO users (email, manager_id, team_name, created_at) VALUES ('free@x.test', 1001, 'Sim City FC', ?)").run(now);
  env.DB.raw.prepare("INSERT INTO users (email, manager_id, team_name, pass_until, created_at) VALUES ('pro@x.test', 1002, 'Expected Goals FC', ?, ?)").run(now + 9e6, now);
}
await seed();

createServer(async (req, res) => {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const r = await handle(new Request(`http://localhost:8787${req.url}`, {
    method: req.method, headers: req.headers, body: chunks.length ? Buffer.concat(chunks) : undefined,
  }), env, deps);
  res.writeHead(r.status, Object.fromEntries(r.headers));
  res.end(Buffer.from(await r.arrayBuffer()));
}).listen(8787, () => console.log("FPL Sim preview on http://localhost:8787 (log in as free@x.test or pro@x.test)"));
