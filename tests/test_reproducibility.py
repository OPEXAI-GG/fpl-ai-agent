"""A forecast depends on the data, never on the order the database happens to return rows in.

DuckDB returns unordered rows in a different order from one process to the next. Before the
canonical ordering, a re-run of the GW10 plan on identical inputs (same cutoff, samples and
seed) moved one fixture's projections by up to 0.25 points: the ratings fit summed its matches
in a new order, a goal rate moved by about 1e-5, a Poisson draw consumed a different number of
uniforms, and every later draw of that fixture was re-dealt.
"""

import hashlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fpl_agent.db.database import FootballDB
from fpl_agent.model import features, minutes, players, project, strength, translation
from fpl_agent.model.order import canonical
from fpl_agent.model.simulate import SimConfig
from fpl_agent.rules import Rules
from tests.synth import build_world

SEASON, GWS, AS_OF = 2024, [10, 11], date(2024, 10, 20)
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def world():
    return build_world(n_teams=8, seasons=(2023, 2024), seed=11)


def _tables(db: FootballDB) -> list[str]:
    rows = db.con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
    ).fetchall()
    return sorted(r[0] for r in rows if r[0] != "schema_migrations")


def shuffled_copy(db: FootballDB, path, seed: int) -> FootballDB:
    """The same rows in every table, inserted in a different order, behind a fresh connection."""
    out = FootballDB(path)
    for i, t in enumerate(_tables(db)):
        df = db.query(f"SELECT * FROM {t}")
        if df.empty:
            continue
        out.con.execute(f"DELETE FROM {t}")
        out.append(t, df.sample(frac=1, random_state=seed + i).reset_index(drop=True))
    out.con.execute("CHECKPOINT")
    return out


def _project(db: FootballDB, **kw):
    return project.build_projection(db, SEASON, GWS, as_of=AS_OF, cfg=SimConfig(n=300, seed=3),
                                    pool_gw=GWS[0], **kw)  # fmt: skip


def by_player(proj) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Sampled points and minutes in player-id order, so two runs compare row for row."""
    order = np.argsort(np.asarray(proj.player_ids))
    return np.asarray(proj.player_ids)[order], proj.points[order], proj.minutes[order]


def digest(proj) -> str:
    ids, pts, mins = by_player(proj)
    h = hashlib.sha256()
    for a in (ids.astype(np.int64), pts.astype(np.float32), mins.astype(np.int16)):
        h.update(np.ascontiguousarray(a).tobytes())
    return h.hexdigest()


def test_reordered_source_rows_give_every_player_the_same_sampled_points(world, tmp_path):
    base = by_player(_project(world.db))
    for seed in (1, 2):
        again = by_player(_project(shuffled_copy(world.db, tmp_path / f"s{seed}.duckdb", seed)))
        assert np.array_equal(base[0], again[0])
        assert np.array_equal(base[1], again[1]), "sampled points depend on row order"
        assert np.array_equal(base[2], again[2]), "sampled minutes depend on row order"


def test_each_fit_and_the_simulation_ignore_the_order_of_their_inputs(world):
    db, rules = world.db, Rules.load(2026)

    def sh(df: pd.DataFrame, s: int = 5) -> pd.DataFrame:
        return df.sample(frac=1, random_state=s).reset_index(drop=True)

    m = strength.load_matches(db, as_of=AS_OF)
    a, b = strength.fit_ratings(m, AS_OF), strength.fit_ratings(sh(m), AS_OF)
    assert a.att == b.att and a.dfn == b.dfn and a.mu == b.mu and a.home == b.home

    frame = features.player_gw_frame(db)
    hist = frame[(frame.season < SEASON) | ((frame.season == SEASON) & (frame.gw < GWS[0]))]
    pool = project.current_pool(db, SEASON, GWS[0])
    up = pool.assign(season=SEASON, gw=GWS[0], horizon=1, kickoff=pd.Timestamp("2024-10-26"))
    f1 = minutes.MinutesModel.fit(hist).forecast(hist, up)
    f2 = minutes.MinutesModel.fit(sh(hist)).forecast(sh(hist, 6), sh(up))
    pd.testing.assert_frame_equal(canonical(f1, ["player_id"]), canonical(f2, ["player_id"]))

    pm = features.match_frame(db)
    pos = dict(zip(pool["player_id"], pool["position"], strict=True))
    tr = translation.Translations.load(db)
    r1 = players.fit_player_rates(pm, hist, pos, SEASON, tr).set_index("player_id").sort_index()
    r2 = players.fit_player_rates(sh(pm), sh(hist), pos, SEASON, tr)
    pd.testing.assert_frame_equal(r1, r2.set_index("player_id").sort_index())
    assert project.fit_bps_noise(hist, rules) == project.fit_bps_noise(sh(hist), rules)

    # the simulation itself: the same pool handed over in another order
    p1 = by_player(_project(db, pool=pool))
    p2 = by_player(_project(db, pool=sh(pool)))
    assert all(np.array_equal(x, y) for x, y in zip(p1, p2, strict=True))


def test_separate_processes_and_fresh_connections_give_identical_samples(world, tmp_path):
    """Two interpreters on two differently ordered files give one digest, the in-process one."""
    paths = [shuffled_copy(world.db, tmp_path / f"p{i}.duckdb", 10 * i) for i in (1, 2)]
    for db in paths:
        db.con.close()
    code = (
        "import sys, json; from datetime import date; "
        "from fpl_agent.db.database import FootballDB; from fpl_agent.model import project; "
        "from fpl_agent.model.simulate import SimConfig; "
        "from tests.test_reproducibility import digest; "
        "p = project.build_projection(FootballDB(sys.argv[1]), 2024, [10, 11], "
        "as_of=date(2024, 10, 20), cfg=SimConfig(n=300, seed=3), pool_gw=10); "
        "print(json.dumps(digest(p)))"
    )
    got = []
    for i in (1, 2):
        out = subprocess.run([sys.executable, "-c", code, str(tmp_path / f"p{i}.duckdb")],
                             cwd=ROOT, capture_output=True, text=True, check=True)  # fmt: skip
        got.append(json.loads(out.stdout.strip().splitlines()[-1]))
    assert got[0] == got[1] == digest(_project(world.db))


def test_canonical_order_is_total_even_with_tied_keys():
    df = pd.DataFrame({"k": [1, 1, 0, 1], "x": [0.5, 0.25, 9.0, 0.5], "s": ["b", "a", None, "a"]})
    want = canonical(df, ["k"])
    for s in range(5):
        got = canonical(df.sample(frac=1, random_state=s), ["k"])
        pd.testing.assert_frame_equal(got, want)
    assert want["k"].tolist() == [0, 1, 1, 1] and want["x"].tolist() == [9.0, 0.25, 0.5, 0.5]
