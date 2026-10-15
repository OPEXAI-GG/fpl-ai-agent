// Queries against the D1 database (schema in migrations/).

import { hash, LINK_MINUTES, now, SESSION_DAYS, token } from "./auth.js";

export const isPro = (u, t = now()) => !!u && Math.max(u.sub_until || 0, u.pass_until || 0) > t;

export async function userByEmail(db, email) {
  return db.prepare("SELECT * FROM users WHERE email = ?").bind(email).first();
}

export async function userById(db, id) {
  return db.prepare("SELECT * FROM users WHERE id = ?").bind(id).first();
}

export async function userByCustomer(db, customer) {
  return db.prepare("SELECT * FROM users WHERE stripe_customer = ?").bind(customer).first();
}

export async function ensureUser(db, email) {
  await db
    .prepare("INSERT INTO users (email, created_at) VALUES (?, ?) ON CONFLICT(email) DO NOTHING")
    .bind(email, now())
    .run();
  return userByEmail(db, email);
}

export async function setManager(db, userId, m) {
  await db
    .prepare("UPDATE users SET manager_id = ?, team_name = ?, manager_name = ? WHERE id = ?")
    .bind(m.id, m.team || null, m.manager || null, userId)
    .run();
}

export async function setBilling(db, userId, fields) {
  const keys = Object.keys(fields);
  const sql = `UPDATE users SET ${keys.map((k) => `${k} = ?`).join(", ")} WHERE id = ?`;
  await db
    .prepare(sql)
    .bind(...keys.map((k) => fields[k]), userId)
    .run();
}

// ---- sign-in links and sessions ----------------------------------------------------------------
export async function recentLink(db, email, seconds) {
  const r = await db
    .prepare("SELECT count(*) AS n FROM login_tokens WHERE email = ? AND created_at > ?")
    .bind(email, now() - seconds)
    .first();
  return (r?.n || 0) > 0;
}

export async function newLink(db, email) {
  const t = token();
  await db
    .prepare("INSERT INTO login_tokens (token_hash, email, created_at, expires_at) VALUES (?, ?, ?, ?)")
    .bind(await hash(t), email, now(), now() + LINK_MINUTES * 60)
    .run();
  return t;
}

// The email a valid, unused link belongs to (and the link is spent), or null.
export async function useLink(db, t) {
  const h = await hash(t);
  const row = await db
    .prepare("SELECT email FROM login_tokens WHERE token_hash = ? AND used = 0 AND expires_at > ?")
    .bind(h, now())
    .first();
  if (!row) return null;
  await db.prepare("UPDATE login_tokens SET used = 1 WHERE token_hash = ?").bind(h).run();
  return row.email;
}

export async function newSession(db, userId) {
  const t = token();
  await db
    .prepare("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)")
    .bind(await hash(t), userId, now() + SESSION_DAYS * 86400)
    .run();
  return t;
}

export async function sessionUser(db, t) {
  if (!t) return null;
  return db
    .prepare(
      "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id " +
        "WHERE s.token_hash = ? AND s.expires_at > ?",
    )
    .bind(await hash(t), now())
    .first();
}

export async function endSession(db, t) {
  if (t) await db.prepare("DELETE FROM sessions WHERE token_hash = ?").bind(await hash(t)).run();
}

// ---- what the engine writes --------------------------------------------------------------------
export async function latestGameweek(db) {
  const r = await db.prepare("SELECT * FROM gameweeks ORDER BY gw DESC LIMIT 1").first();
  return r ? { ...r, captains: JSON.parse(r.captains) } : null;
}

export async function planFor(db, managerId) {
  const r = await db
    .prepare("SELECT * FROM plans WHERE manager_id = ? ORDER BY gw DESC LIMIT 1")
    .bind(managerId)
    .first();
  return r ? { ...r, payload: JSON.parse(r.payload) } : null;
}

// Every manager id with an account: pro if any account on it is paid up, with this gameweek's
// plan kind and time so the engine can keep a fresh full plan.
export async function managers(db, gw) {
  const t = now();
  const { results } = await db
    .prepare(
      "SELECT u.manager_id, max(max(u.sub_until, u.pass_until) > ?) AS pro, " +
        "p.kind, p.created_at AS planned_at FROM users u " +
        "LEFT JOIN plans p ON p.manager_id = u.manager_id AND p.gw = ? " +
        "WHERE u.manager_id IS NOT NULL GROUP BY u.manager_id, p.kind, p.created_at " +
        "ORDER BY pro DESC, u.manager_id",
    )
    .bind(t, gw)
    .all();
  return results.map((r) => ({
    manager_id: r.manager_id,
    tier: r.pro ? "pro" : "free",
    kind: r.kind || null,
    planned_at: r.planned_at || null,
  }));
}

export async function saveGameweek(db, g) {
  await db
    .prepare(
      "INSERT INTO gameweeks (gw, deadline, captains, updated_at) VALUES (?, ?, ?, ?) " +
        "ON CONFLICT(gw) DO UPDATE SET deadline = excluded.deadline, " +
        "captains = excluded.captains, updated_at = excluded.updated_at",
    )
    .bind(g.gw, g.deadline, JSON.stringify(g.captains || []), now())
    .run();
}

export async function savePlans(db, plans) {
  const stmt = db.prepare(
    "INSERT INTO plans (manager_id, gw, kind, payload, created_at) VALUES (?, ?, ?, ?, ?) " +
      "ON CONFLICT(manager_id, gw) DO UPDATE SET kind = excluded.kind, " +
      "payload = excluded.payload, created_at = excluded.created_at",
  );
  const t = now();
  const rows = plans.map((p) =>
    stmt.bind(p.manager_id, p.gw, p.payload.kind, JSON.stringify(p.payload), t),
  );
  if (rows.length) await db.batch(rows);
}

// Stripe may deliver an event more than once: one is marked done only once it was handled.
export async function eventDone(db, id) {
  return !!(await db.prepare("SELECT id FROM stripe_events WHERE id = ?").bind(id).first());
}

export async function markEvent(db, id) {
  await db
    .prepare("INSERT INTO stripe_events (id, created_at) VALUES (?, ?) ON CONFLICT(id) DO NOTHING")
    .bind(id, now())
    .run();
}
