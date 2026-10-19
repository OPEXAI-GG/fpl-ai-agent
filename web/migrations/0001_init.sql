-- FPL Sim website: accounts, sign-in, subscriptions and the plans the engine writes.
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  manager_id INTEGER,
  team_name TEXT,
  manager_name TEXT,
  stripe_customer TEXT,
  stripe_subscription TEXT,
  sub_until INTEGER NOT NULL DEFAULT 0,   -- monthly plan paid up to (unix seconds)
  pass_until INTEGER NOT NULL DEFAULT 0,  -- season pass valid up to (unix seconds)
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS users_manager ON users (manager_id);
CREATE INDEX IF NOT EXISTS users_customer ON users (stripe_customer);

CREATE TABLE IF NOT EXISTS login_tokens (
  token_hash TEXT PRIMARY KEY,
  email TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  used INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS login_tokens_email ON login_tokens (email, created_at);

CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS gameweeks (
  gw INTEGER PRIMARY KEY,
  deadline TEXT NOT NULL,
  captains TEXT NOT NULL,                 -- JSON: the week's best captains
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
  manager_id INTEGER NOT NULL,
  gw INTEGER NOT NULL,
  kind TEXT NOT NULL,                     -- free | full | error
  payload TEXT NOT NULL,                  -- JSON from the engine (fpl_agent/site.py)
  created_at INTEGER NOT NULL,
  PRIMARY KEY (manager_id, gw)
);

CREATE TABLE IF NOT EXISTS stripe_events (
  id TEXT PRIMARY KEY,
  created_at INTEGER NOT NULL
);
