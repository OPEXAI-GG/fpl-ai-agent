"""Structural and cutoff checks for `minutes-workload-v0` (no performance claims).

Copies the needed tables of a source (Parquet export dir or .duckdb, read-only) into an
in-memory DuckDB, then mutates only that copy:
  1. future: appearances whose match kicks off after C - FINISH_BUFFER (incl. inside the 3 h
     buffer, and after C) are added with huge minutes  -> both variants unchanged
  2. late ingest: a pre-cutoff appearance is re-stamped ingested_at = C + 1 h -> rec unchanged,
     obs loses exactly that row
  3. namesake: an appearance for another club before C is added -> both variants unchanged
  4. NULL semantics: unlinked and link-suspect players have NULL features (never 0); trusted
     linked players have no NULL count features
  5. determinism: two computations on the unchanged copy are identical
  6. suspect link: a linked player with no window rows gets an observed other-club row -> both
     variants mark link_suspect and NULL all features; nobody else changes
  7. repair after C: a journalled repair (repair_log 'added' image, logged after C) of an
     observed row -> obs loses exactly that row, rec unchanged; the same entry logged before C
     changes nothing
Prints one line per check and exits 1 on any failure. Run from the repo root:
  PYTHONPATH=src .venv/Scripts/python.exe artifacts/model-factor-workload-20261030/checks.py \
      --source artifacts/FAA-7-production/r20261028/export --season 2026 --gw 8
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import workload as w  # noqa: E402

FEATS = ["mins_7d", "mins_14d", "starts_7d", "starts_14d", "apps_14d", "euro_mins_14d",
         "rest_days"]  # fmt: skip


def copy(src: Path) -> SimpleNamespace:
    ro = w.open_source(src)
    mem = duckdb.connect()
    present = [t for t in w.TABLES if t not in ro.missing_tables]
    for t in present:
        mem.register("_t", ro.con.execute(f"SELECT * FROM {t}").df())
        mem.execute(f"CREATE TABLE {t} AS SELECT * FROM _t")
        mem.unregister("_t")
    return SimpleNamespace(con=mem, missing_tables=ro.missing_tables)


def cols(prefix: str) -> list[str]:
    return [prefix + c for c in FEATS]


def same(a: pd.DataFrame, b: pd.DataFrame, prefix: str) -> bool:
    k = ["fpl_id"] + cols(prefix)
    x = a[k].sort_values("fpl_id").reset_index(drop=True)
    y = b[k].sort_values("fpl_id").reset_index(drop=True)
    return x.equals(y)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--gw", type=int, required=True)
    a = ap.parse_args()
    db = copy(a.source)
    base, info = w.gameweek(db, a.season, a.gw)
    C = pd.Timestamp(info["cutoff"]).to_pydatetime()
    res = {}

    again, _ = w.gameweek(db, a.season, a.gw)
    res["5 determinism"] = same(base, again, "obs_") and same(base, again, "rec_")

    lk = base[base.linked]
    feats = cols("obs_") + cols("rec_")
    unknown = ~base.linked | base.obs_link_suspect | base.rec_link_suspect
    trusted = base[base.linked & ~base.obs_link_suspect & ~base.rec_link_suspect]
    res["4 NULL semantics (unlinked/suspect NULL, trusted counts non-NULL)"] = bool(
        base.loc[unknown & ~base.linked, feats].isna().all().all()
        and base.loc[base.obs_link_suspect, cols("obs_")].isna().all().all()
        and base.loc[base.rec_link_suspect, cols("rec_")].isna().all().all()
        and not base.loc[base.obs_link_suspect, "obs_no_recent_30d"].any()
        and not base.loc[base.rec_link_suspect, "rec_no_recent_30d"].any()
        and not trusted[[c for c in feats if "rest" not in c]].isna().any().any()
    )  # fmt: skip

    # a trusted player with FPL-club minutes in the window; extra rows go on fresh match ids
    p = trusted[trusted.rec_rest_days.notna()].iloc[0]
    mid = db.con.execute("SELECT max(match_id) + 1 FROM matches").fetchone()[0]

    def add_match(i: int, kickoff, team_id: int, minutes: int, ingested) -> None:
        db.con.execute(
            "INSERT INTO matches (match_id, competition_id, season, kickoff, match_date, "
            "home_team_id, away_team_id, finished, source, ingested_at) VALUES "
            "(?, 'UCL', ?, ?, ?, ?, -1, true, 'check', ?)",
            [mid + i, a.season, kickoff, kickoff.date(), team_id, ingested])  # fmt: skip
        db.con.execute(
            "INSERT INTO appearances (match_id, player_id, team_id, source, started, minutes, "
            "position, ingested_at) VALUES (?, ?, ?, 'check', true, ?, 'X', ?)",
            [mid + i, int(p.player_id), team_id, minutes, ingested])  # fmt: skip

    team = int(p.team_id)
    add_match(0, C - timedelta(hours=2), team, 999, C - timedelta(hours=1))  # inside buffer
    add_match(1, C + timedelta(hours=1), team, 999, C - timedelta(minutes=1))  # after C
    add_match(2, C + timedelta(days=3), team, 999, C + timedelta(days=4))  # target week
    fut, _ = w.gameweek(db, a.season, a.gw)
    res["1 future/in-buffer events change nothing"] = same(base, fut, "obs_") and same(
        base, fut, "rec_"
    )

    add_match(3, C - timedelta(days=2), team + 1000, 90, C - timedelta(days=1))  # namesake
    nm, _ = w.gameweek(db, a.season, a.gw)
    res["3 other-club rows change nothing"] = same(base, nm, "obs_") and same(base, nm, "rec_")

    # 6: a linked player with no rows at all in the window gains a namesake row
    db.con.register("_lk", lk[["player_id", "team_id"]])
    q = db.con.execute(
        "SELECT p.player_id FROM _lk p WHERE NOT EXISTS (SELECT 1 FROM appearances a JOIN "
        "matches m USING (match_id) WHERE a.player_id = p.player_id AND m.kickoff BETWEEN "
        "?::TIMESTAMP - INTERVAL 30 DAY AND ?) ORDER BY 1 LIMIT 1", [C, C]).fetchone()  # fmt: skip
    if q is None:
        res["6 suspect link is NULL, not rest"] = "skipped: no linked player without rows"
    else:
        p = base[base.player_id == q[0]].iloc[0]  # add_match writes rows for `p`
        add_match(4, C - timedelta(days=5), int(p.team_id) + 1000, 90, C - timedelta(days=4))
        sus, _ = w.gameweek(db, a.season, a.gw)
        s2 = sus.set_index("player_id").loc[q[0]]
        others = sus.player_id != q[0]
        res["6 suspect link is NULL, not rest"] = bool(
            p.obs_no_recent_30d and s2.obs_link_suspect and s2.rec_link_suspect
            and pd.isna(s2[cols("obs_") + cols("rec_")]).all()
            and not s2.obs_no_recent_30d and not s2.rec_no_recent_30d
            and same(base[base.player_id != q[0]], sus[others], "obs_")
            and same(base[base.player_id != q[0]], sus[others], "rec_"))  # fmt: skip
    base, _ = w.gameweek(db, a.season, a.gw)  # the reference for 7 and 2 includes 1, 3, 6

    def observed_row(skip: tuple | None = None):
        return db.con.execute(
            "SELECT a.match_id, a.player_id FROM appearances a JOIN matches m USING (match_id) "
            "JOIN _lk p ON p.player_id = a.player_id AND p.team_id = a.team_id "
            "WHERE a.source <> 'check' AND a.minutes > 0 AND a.ingested_at <= ? AND m.kickoff "
            "BETWEEN ?::TIMESTAMP - INTERVAL 7 DAY AND ? AND (a.match_id, a.player_id) <> "
            "(?, ?) ORDER BY a.match_id, a.player_id LIMIT 1",
            [C, C, C - w.FINISH_BUFFER, *(skip or (-1, -1))]).fetchone()  # fmt: skip

    def drops_only(after: pd.DataFrame, key: tuple) -> bool:
        b = base.set_index("player_id").loc[key[1]]
        x = after.set_index("player_id").loc[key[1]]
        keep_a, keep_b = after.player_id != key[1], base.player_id != key[1]
        return bool(same(base, after, "rec_") and x.obs_apps_14d == b.obs_apps_14d - 1
                    and same(base[keep_b], after[keep_a], "obs_"))  # fmt: skip

    # 7: a journalled repair of an observed row, logged before and then after C
    rrow = observed_row()
    if rrow is None:
        res["7 repair after C drops from obs only"] = "skipped: no observed 7-day row"
    else:
        if "repair_log" in db.missing_tables:
            db.con.execute("CREATE TABLE repair_log (repair_id VARCHAR, seq INTEGER, step "
                           "VARCHAR, table_name VARCHAR, action VARCHAR, image VARCHAR, reason "
                           "VARCHAR, logged_at TIMESTAMP, rolled_back_at TIMESTAMP)")  # fmt: skip
            db.missing_tables = [t for t in db.missing_tables if t != "repair_log"]
        image = f'{{"match_id": {rrow[0]}, "player_id": {rrow[1]}}}'

        def journal(at) -> pd.DataFrame:
            db.con.execute("DELETE FROM repair_log WHERE repair_id = 'check'")
            db.con.execute("INSERT INTO repair_log VALUES ('check', 1, 'check', 'appearances', "
                           "'added', ?, 'check', ?, NULL)", [image, at])  # fmt: skip
            return w.gameweek(db, a.season, a.gw)[0]

        before_c = journal(C - timedelta(hours=1))
        after_c = journal(C + timedelta(hours=1))
        res["7 repair after C drops from obs only"] = bool(
            same(base, before_c, "obs_") and same(base, before_c, "rec_")
            and drops_only(after_c, rrow))  # fmt: skip
        db.con.execute("DELETE FROM repair_log WHERE repair_id = 'check'")

    # 2: re-stamp one real, admissible (same-club) pre-cutoff row as ingested after C
    row = observed_row()
    if row is None:
        res["2 late ingest drops from obs only"] = "skipped: no observed 7-day row"
    else:
        db.con.execute("UPDATE appearances SET ingested_at = ? WHERE match_id = ? AND "
                       "player_id = ?", [C + timedelta(hours=1), *row])  # fmt: skip
        res["2 late ingest drops from obs only"] = drops_only(w.gameweek(db, a.season, a.gw)[0],
                                                              row)  # fmt: skip

    ok = True
    for k, v in res.items():
        print(f"{'PASS' if v is True else 'FAIL' if v is False else 'SKIP'}  {k}"
              + ("" if isinstance(v, bool) else f" ({v})"))  # fmt: skip
        ok &= v is not False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
