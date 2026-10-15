// The D1 API over node:sqlite, with the real migrations applied: enough for the worker's queries.

import { readdirSync, readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";

class Stmt {
  constructor(db, sql, args = []) {
    this.db = db;
    this.sql = sql;
    this.args = args;
  }
  bind(...args) {
    return new Stmt(this.db, this.sql, args);
  }
  async first() {
    return this.db.prepare(this.sql).get(...this.args) ?? null;
  }
  async all() {
    return { results: this.db.prepare(this.sql).all(...this.args) };
  }
  async run() {
    const r = this.db.prepare(this.sql).run(...this.args);
    return { meta: { changes: Number(r.changes) } };
  }
}

export function fakeD1() {
  const db = new DatabaseSync(":memory:");
  const dir = new URL("../migrations/", import.meta.url);
  for (const f of readdirSync(dir).sort()) db.exec(readFileSync(new URL(f, dir), "utf8"));
  return {
    raw: db,
    prepare: (sql) => new Stmt(db, sql),
    batch: async (stmts) => Promise.all(stmts.map((s) => s.run())),
  };
}
