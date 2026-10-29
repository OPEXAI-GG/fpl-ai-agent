"""M02 comparison on the M00 isolated copy (read-only). Three parts:

A. Baseline reproduction: current code with the switch off at the M00 cutoff must reproduce
   baseline-rates.csv byte for byte on the original columns.
B. Live candidate at the M00 cutoff (strictly point in time): who changes and by how much.
   No outcomes exist after this cutoff in the copy, so nothing is scored here.
C. EXPLORATORY reconstruction, GWs 5-7: FPL rows are treated as known at kickoff (they were
   really ingested 2026-10-20), cutoff = the gameweek's first kickoff, scored on that gameweek.
   Oracle minutes and the team's realised FotMob xG isolate the share estimate that M02 changes.
   Zero eligible gameweeks exist, so no result here is validated or promotable.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from fpl_agent.db import player_links
from fpl_agent.db.database import FootballDB
from fpl_agent.model.features import match_frame, player_gw_frame
from fpl_agent.model.players import fit_player_rates
from fpl_agent.model.translation import Translations

here = Path(__file__).resolve().parent
src = here.parent / "work-data.duckdb"
OLD_COLS = ["player_id", "position", "xg_share", "xa_share", "finish", "cbit_mu", "cbit_r",
            "yellow90", "red90", "evidence_minutes", "source"]  # fmt: skip
EXCLUDED = {31, 40, 62}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def deviance(y, mu):
    y, mu = np.asarray(y, float), np.clip(np.asarray(mu, float), 1e-9, None)
    t = np.where(y > 0, y * np.log(np.where(y > 0, y, 1) / mu), 0.0)
    return float(2 * np.sum(t - (y - mu)))


def calib(y, mu):
    """Poisson regression of y on log(mu): (intercept, slope); slope 1 = calibrated."""
    x = np.log(np.clip(np.asarray(mu, float), 1e-9, None))
    X = np.column_stack([np.ones_like(x), x])
    b = np.array([0.0, 1.0])
    y = np.asarray(y, float)
    for _ in range(50):
        m = np.exp(X @ b)
        step = np.linalg.solve(X.T @ (X * m[:, None]) + 1e-9 * np.eye(2), X.T @ (y - m))
        b += step
        if np.abs(step).max() < 1e-10:
            break
    return float(b[0]), float(b[1])


start_hash = sha(src)
db = FootballDB(src, schema="read")
try:
    frame = player_gw_frame(db, min_season=2026)
    raw = match_frame(db)
    current = db.query(
        "SELECT player_id, position, team_id FROM fpl_players_gw WHERE season = 2026 "
        "QUALIFY row_number() OVER (PARTITION BY player_id "
        "ORDER BY snapshot_at DESC, gw DESC) = 1"
    )
    positions = dict(zip(current.player_id.astype(int), current.position, strict=True))
    team_of = dict(zip(current.player_id.astype(int), current.team_id.astype(int), strict=True))
    matches = db.query(
        "SELECT m.match_id, m.kickoff, tm.team_id, tm.xg_for FROM matches m "
        "JOIN team_match tm USING (match_id) WHERE m.competition_id = 'ENG1' "
        "AND m.season = 2026 AND tm.source = 'fotmob'"
    )

    def rates(cutoff, fr, on):
        blocked = player_links.unsupported(db, 2026, cutoff)
        pm = player_links.resolve(raw[raw.date < pd.Timestamp(cutoff.date())],
                                  cutoff.date(), cutoff, blocked)  # fmt: skip
        return fit_player_rates(pm, fr, positions, 2026, Translations.load(db, as_of=cutoff),
                                as_of=cutoff, fpl_xg_fallback=on).sort_values("player_id")  # fmt: skip

    # A + B at the M00 cutoff
    m00_cut = datetime(2026, 10, 27, 16, 34, tzinfo=UTC).replace(tzinfo=None)
    base = rates(m00_cut, frame, False)
    csv = base[OLD_COLS].to_csv(index=False, float_format="%.12g", na_rep="NA",
                                lineterminator="\n")  # fmt: skip
    (here / "baseline-reproduced.csv").write_text(csv, encoding="utf-8", newline="")
    cand = rates(m00_cut, frame, True)
    cand.to_csv(here / "candidate-rates-m00-cutoff.csv", index=False, float_format="%.12g",
                na_rep="NA", lineterminator="\n")  # fmt: skip
    j = base.merge(cand, on="player_id", suffixes=("_b", "_c"))
    changed = j[(j.xg_share_b != j.xg_share_c) | (j.xa_share_b != j.xa_share_c)]
    part_b = {
        "players": len(j),
        "changed_players": len(changed),
        "baseline_source_counts": base.source.value_counts().to_dict(),
        "candidate_source_counts": cand.source.value_counts().to_dict(),
        "candidate_xg_source": cand.xg_source.value_counts().to_dict(),
        "candidate_xa_source": cand.xa_source.value_counts().to_dict(),
        "changed_by_position": changed.position_b.value_counts().to_dict(),
        "unchanged_detailed_players_identical": bool(
            (
                j.loc[j.xg_source_c == "detailed", "xg_share_b"]
                == j.loc[j.xg_source_c == "detailed", "xg_share_c"]
            ).all()
        ),  # fmt: skip
        "xg_share_changed_median_ratio": float((changed.xg_share_c / changed.xg_share_b).median())
        if len(changed)
        else None,
    }

    # C: exploratory reconstruction
    recon = frame.copy()
    recon["stats_ingested_at"] = recon["kickoff"]  # assumption: known at kickoff
    rows, per_gw = [], {}
    for gw in (5, 6, 7):
        g = frame[(frame.gw == gw) & (frame.minutes > 0)].copy()
        cut = pd.Timestamp(g.kickoff.min()).to_pydatetime()
        fit_rows = recon[recon.kickoff < cut]
        b, c = rates(cut, fit_rows, False), rates(cut, fit_rows, True)
        g["team_id"] = g.player_id.map(team_of)
        g = g.merge(matches, left_on=["kickoff", "team_id"], right_on=["kickoff", "team_id"],
                    how="left")  # fmt: skip
        per_gw[gw] = {
            "cutoff": str(cut),
            "rows": len(g),
            "unmatched_team_xg": int(g.xg_for.isna().sum()),
        }
        g = g[g.xg_for.notna() & ~g.match_id.isin(EXCLUDED)]
        for tag, r in (("b", b), ("c", c)):
            g = g.merge(r[["player_id", "xg_share", "xa_share", "finish", "xg_source", "xa_source"]]
                        .add_suffix(f"_{tag}").rename(columns={f"player_id_{tag}": "player_id"}),
                        on="player_id", how="left")  # fmt: skip
        g["gw_eval"] = gw
        rows.append(g)
    ev = pd.concat(rows, ignore_index=True)
    expo = ev.xg_for * ev.minutes / 90.0
    for t in ("b", "c"):
        ev[f"mu_g_{t}"] = ev[f"xg_share_{t}"] * ev[f"finish_{t}"] * expo
        ev[f"mu_a_{t}"] = ev[f"xa_share_{t}"] * expo
    ev.to_csv(here / "exploratory-gw5-7-rows.csv", index=False, float_format="%.12g")

    cohorts = {
        "all rows": ev.index == ev.index,
        "xG detailed history unusable (fallback applied)": ev.xg_source_c == "fpl",
        "xA detailed history unusable (fallback applied)": ev.xa_source_c == "fpl",
        "unchanged rows with usable detailed history": (ev.xg_source_c == "detailed")
        & (ev.xa_source_c == "detailed"),
    }
    for pos in ("DEF", "MID", "FWD"):
        cohorts[f"position {pos}"] = ev.position == pos
    part_c = {}
    for name, m in cohorts.items():
        d = ev[m]
        out = {"rows": int(len(d)), "players": int(d.player_id.nunique()),
               "goal_events": int(d.goals.sum()), "assist_events": int(d.assists.sum())}  # fmt: skip
        for stat, y in (("goals", d.goals), ("assists", d.assists)):
            k = "g" if stat == "goals" else "a"
            db_, dc_ = deviance(y, d[f"mu_{k}_b"]), deviance(y, d[f"mu_{k}_c"])
            out[stat] = {
                "deviance_baseline": db_, "deviance_candidate": dc_,
                "reduction_fraction": (db_ - dc_) / db_ if db_ else None,
                "mae_per90_baseline": float((abs(y - d[f"mu_{k}_b"]) * 90 / d.minutes).mean())
                if len(d) else None,
                "mae_per90_candidate": float((abs(y - d[f"mu_{k}_c"]) * 90 / d.minutes).mean())
                if len(d) else None,
                "calibration_baseline": calib(y, d[f"mu_{k}_b"]) if y.sum() > 0 else None,
                "calibration_candidate": calib(y, d[f"mu_{k}_c"]) if y.sum() > 0 else None,
            }  # fmt: skip
        part_c[name] = out
finally:
    db.close()

minima = json.loads((here.parent / "m02-predeclared-evaluation.json").read_text())[
    "sample_minimum_for_any_performance_claim"
]
result = {
    "label": "EXPLORATORY - reconstruction; 0 eligible gameweeks; not validated; no promotion",
    "source_db_sha256_before": start_hash,
    "source_db_sha256_after": sha(src),
    "A_baseline_reproduction": {
        "sha256": hashlib.sha256(csv.encode()).hexdigest(),
        "matches_m00": hashlib.sha256(csv.encode()).hexdigest()
        == sha(here.parent / "baseline-rates.csv"),
    },
    "B_live_candidate_m00_cutoff": part_b,
    "C_exploratory_gw5_7": {
        "per_gw": per_gw,
        "cohorts": part_c,
        "eligible_gameweeks": 0,
        "predeclared_minima": minima,
    },  # fmt: skip
}
(here / "m02-comparison.json").write_text(json.dumps(result, indent=2, default=str))
print(json.dumps(result, indent=1, default=str))
