"""Feature slice end to end: news evidence -> eligible feature -> changed minutes and points ->
report line citing the evidence; stale, post-cutoff, ambiguous, conflicting and FPL-blocked
evidence changes nothing; workload features keep unknown apart from zero and stay bounded."""

from datetime import datetime, timedelta

import httpx
import numpy as np
import pandas as pd
import pytest

from fpl_agent import agent
from fpl_agent.model import evidence, overlays, project, workload
from fpl_agent.model.simulate import SimConfig
from fpl_agent.news.ingest import run_sources
from fpl_agent.news.registry import SourceConfig
from tests.synth import build_world

SEEN = datetime(2024, 9, 30, 12)  # when our ingester first saw the story
CUTOFF = datetime(2024, 10, 1)  # the GW9 run's information cutoff (GW9 kicks off 5 Oct)
SRC = SourceConfig("feed", "rss", tier=2, url="https://feed.example/x", min_interval_minutes=0)
SRC2 = SourceConfig("feed2", "rss", tier=2, url="https://feed2.example/x", min_interval_minutes=0)


def feed(*stories):
    body = "".join(f"<item><title>{t}</title><description>{d}</description><guid>{g}</guid>"
                   "<pubDate>Mon, 01 Jan 2024 09:00:00 GMT</pubDate></item>"
                   for g, t, d in stories)  # fmt: skip
    xml = f"<rss><channel><title>f</title>{body}</channel></rss>"
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=xml)))


@pytest.fixture(scope="module")
def world():
    return build_world(n_teams=6, seasons=(2023, 2024), seed=5)


@pytest.fixture(scope="module")
def star(world):
    """A first-choice midfielder who starts almost every week."""
    mids = [p for p, t in world.players.items() if t["pos"] == "MID"]
    return max(mids, key=lambda p: world.players[p]["start_p"])


@pytest.fixture(scope="module")
def base(world):
    return project.build_projection(world.db, 2024, [9, 10], as_of=CUTOFF.date(), cutoff=CUTOFF,
                                    cfg=SimConfig(n=400, seed=3), pool_gw=8)  # fmt: skip


def _ingest(world, *stories, src=SRC, at=SEEN):
    run_sources(world.db, [src], feed(*stories), at=at, force=True)


def _clear(world):
    for t in ("news_items", "news_revisions", "news_entities", "evidence_claims", "source_state",
              "source_runs"):  # fmt: skip
        world.db.con.execute(f"DELETE FROM {t}")


def _with_news(world, base, cutoff=CUTOFF):
    ov = overlays.NewsAvailability(world.db)
    inp = base.inputs
    proj = project.build_projection(
        world.db, 2024, [9, 10], as_of=CUTOFF.date(), cutoff=cutoff, cfg=SimConfig(n=400, seed=3),
        pool_gw=8, overlays=[ov], minutes_model=inp["minutes_model"], ratings=inp["ratings"],
        frame=inp["frame"], pm=inp["pm"])  # fmt: skip
    return proj, ov


def _mins(proj, pid, gw=9):
    t = proj.minutes_table
    return t[(t.player_id == pid) & (t.gw == gw)].iloc[0]


def test_evidence_changes_minutes_points_and_the_report(world, star, base, capsys, tmp_path):
    _clear(world)
    _ingest(world, ("s1", f"Player {star} Synth ruled out",
                    f"Player {star} Synth has been ruled out of the weekend game with a "
                    "hamstring injury, his manager confirmed."))  # fmt: skip
    proj, ov = _with_news(world, base)
    before, after = _mins(base, star), _mins(proj, star)
    assert after.availability == pytest.approx(0.70)  # bounded: at most 0.30 from FPL's flag
    assert after.p_start < before.p_start * 0.75
    i = proj.row(star)
    assert proj.mean()[i, 0] < base.mean()[i, 0]  # fewer expected points next gameweek
    later = _mins(proj, star, gw=10)  # the effect decays a week further out
    assert _mins(base, star, gw=10).availability - later.availability == pytest.approx(0.15)
    others = proj.minutes_table[proj.minutes_table.player_id != star]
    base_others = base.minutes_table[base.minutes_table.player_id != star]
    assert np.allclose(others.p_start.to_numpy(), base_others.p_start.to_numpy())
    # explanation and persistence cite the evidence
    lines = agent.explain_overlays([ov], proj, {star: "Star"}, shadow=True)
    assert lines and "Star availability 100% -> 70%" in lines[0] and "ruled out" in lines[0]
    assert "shadow, not used by the plan" in lines[0] and "claim " in lines[0]
    proj.variant = "shadow"
    project.save_projection(world.db, proj, created_at=datetime(2024, 10, 1))
    det = world.db.query("SELECT detail FROM projections WHERE run_id = ? AND gw = 9 AND "
                         "player_id = ?", [proj.run_id, star])["detail"].iloc[0]  # fmt: skip
    assert "ruled out of the weekend game" in det and '"source": "feed"' in det
    from fpl_agent.cli import main

    world.db.export_parquet(tmp_path / "x")
    argv = ["explain", "--db", str(tmp_path / "e.duckdb"), "--import-dir", str(tmp_path / "x"),
            "--season", "2024", "--gw", "9", "--players", f"Player {star} Synth"]  # fmt: skip
    capsys.readouterr()
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "evidence [applied]" in out and "ruled out of the weekend game" in out
    run = world.db.con.execute("SELECT eligible, variant FROM forecast_runs WHERE run_id = ?",
                               [proj.run_id]).fetchone()  # fmt: skip
    assert run == (True, "shadow")


def test_stale_post_cutoff_and_ambiguous_evidence_change_nothing(world, star, base):
    _clear(world)
    text = (f"Player {star} Synth ruled out", f"Player {star} Synth has been ruled out.")
    _ingest(world, ("s1", *text))
    stale, _ = _with_news(world, base, cutoff=SEEN + timedelta(days=8))  # older than 7 days
    early, _ = _with_news(world, base, cutoff=SEEN - timedelta(hours=1))  # before we saw it
    for p in (stale, early):
        assert _mins(p, star).availability == _mins(base, star).availability
    _clear(world)
    _ingest(world, ("s2", "Synth ruled out", "Synth has been ruled out of the game."))
    amb, ov = _with_news(world, base)
    assert _mins(amb, star).availability == _mins(base, star).availability
    assert not ov.changed and ov.counts["excluded_unresolved"] >= 1


def test_conflicting_confirmed_reports_change_nothing(world, star, base):
    _clear(world)
    _ingest(world, ("a", "Team news", f"Player {star} Synth has been ruled out of the match."))
    _ingest(world, ("b", "Different story", f"Player {star} Synth is fit and available again "
                    "says the club in a statement today."), src=SRC2, at=SEEN + timedelta(hours=2))  # fmt: skip
    ev = evidence.player_evidence(world.db, CUTOFF)[star]
    assert ev.state == "conflict" and len(ev.claims) == 2
    proj, ov = _with_news(world, base)
    assert _mins(proj, star).availability == _mins(base, star).availability
    assert ov.counts.get("conflict")


def test_copies_are_not_independent_confirmation(world, star):
    _clear(world)
    story = f"Player {star} Synth has been ruled out of Saturday's match with a knee injury"
    _ingest(world, ("a", "Out", story))
    _ingest(world, ("b", "Out", story), src=SRC2, at=SEEN + timedelta(hours=1))  # a copy
    copied = evidence.player_evidence(world.db, CUTOFF)[star]
    assert copied.stories == 1
    _clear(world)
    _ingest(world, ("a", "Out", story))
    _ingest(world, ("b", "Club update", f"The club said Player {star} Synth will miss the next "
                    "two league games after scans."), src=SRC2, at=SEEN + timedelta(hours=1))  # fmt: skip
    independent = evidence.player_evidence(world.db, CUTOFF)[star]
    assert independent.stories == 2 and independent.weight > copied.weight


def test_edit_replaces_the_earlier_revision(world, star):
    _clear(world)
    _ingest(world, ("a", "Update", f"Player {star} Synth has been ruled out."))
    _ingest(world, ("a", "Update", f"Player {star} Synth is fit and available for selection."),
            at=SEEN + timedelta(hours=3))  # fmt: skip
    assert evidence.player_evidence(world.db, SEEN + timedelta(hours=1))[star].p_play == 0.0
    assert evidence.player_evidence(world.db, CUTOFF)[star].p_play == 0.95


def test_fpl_hard_eligibility_is_never_overridden(world, star, base):
    _clear(world)
    _ingest(world, ("a", "Back", f"Player {star} Synth returned to full training today."))
    mins = base.minutes_table.copy()
    up = mins[["player_id", "gw", "horizon"]].assign(status="a", chance=None,
                                                     kickoff=pd.Timestamp("2024-10-05 15:00"))  # fmt: skip
    up.loc[up.player_id == star, "status"] = "u"
    mins.loc[mins.player_id == star, "availability"] = 0.0
    out = overlays.NewsAvailability(world.db).apply(mins, up, CUTOFF)
    assert out.loc[out.player_id == star, "availability"].eq(0.0).all()


# ---- workload -----------------------------------------------------------------------------------
def test_workload_features_units_cutoff_and_unknown():
    apps = pd.DataFrame({"player_id": [1, 1, 1], "match_id": [1, 2, 3], "minutes": [90, 0, 75],
                         "kickoff": pd.to_datetime(["2024-09-20 15:00", "2024-09-25 19:00",
                                                    "2024-09-28 15:00"])})  # fmt: skip
    teams = pd.DataFrame({"team_id": [7, 7, 7], "kickoff": apps["kickoff"]})
    targets = pd.DataFrame({"player_id": [1, 2], "team_id": [7, 8],
                            "kickoff": pd.to_datetime(["2024-10-01 19:00"] * 2)})  # fmt: skip
    f = workload.workload_features(targets, apps, teams)
    assert f.loc[0, "xc_rest_days"] == pytest.approx(3 + 4 / 24)
    assert f.loc[0, "xc_mins_7d"] == 75 and f.loc[0, "xc_mins_14d"] == 165
    assert f.loc[0, "xc_team_matches_7d"] == 2
    assert f.loc[1].isna().all()  # no coverage at all: unknown, not zero
    # facts after the cutoff (minus the 3h for results to be public) do not exist yet
    g = workload.workload_features(targets.iloc[:1], apps, teams, cutoff=datetime(2024, 9, 28, 17))
    assert g.loc[0, "xc_mins_7d"] == 0 and g.loc[0, "xc_rest_days"] == pytest.approx(11 + 4 / 24)


def test_covered_player_with_no_minutes_is_zero_not_unknown():
    apps = pd.DataFrame({"player_id": [1], "match_id": [1], "minutes": [0],
                         "kickoff": pd.to_datetime(["2024-09-28 15:00"])})  # fmt: skip
    targets = pd.DataFrame({"player_id": [1], "team_id": [7],
                            "kickoff": pd.to_datetime(["2024-10-01 19:00"])})  # fmt: skip
    f = workload.workload_features(targets, apps, pd.DataFrame(columns=["team_id", "kickoff"]))
    assert f.loc[0, "xc_mins_7d"] == 0 and np.isnan(f.loc[0, "xc_rest_days"])


def test_workload_overlay_is_bounded_and_next_gameweek_only(world, base):
    ov = overlays.Workload(world.db, bound=0.05)
    inp = base.inputs
    proj = project.build_projection(
        world.db, 2024, [9, 10], as_of=CUTOFF.date(), cutoff=CUTOFF, cfg=SimConfig(n=200, seed=3),
        pool_gw=8, overlays=[ov], minutes_model=inp["minutes_model"], ratings=inp["ratings"],
        frame=inp["frame"], pm=inp["pm"])  # fmt: skip
    a = proj.minutes_table.set_index(["player_id", "gw"])
    b = base.minutes_table.set_index(["player_id", "gw"])
    d9 = (a.xs(9, level="gw").p_start - b.xs(9, level="gw").p_start).abs()
    assert d9.max() <= 0.05 + 1e-9 and ov.changed > 0
    assert np.allclose(a.xs(10, level="gw").p_start, b.xs(10, level="gw").p_start)
    assert ov.summary()["train_rows"] > 0


def test_flags_default_to_shadow_and_env_wins(monkeypatch):
    monkeypatch.delenv("FEATURE_WORKLOAD", raising=False)
    monkeypatch.setenv("FEATURE_NEWS_AVAILABILITY", "off")
    m = overlays.modes({"workload": "on", "news_availability": "on"})
    assert m == {"news_availability": "off", "workload": "on"}
    monkeypatch.setenv("FEATURE_NEWS_AVAILABILITY", "bogus")
    assert overlays.modes()["news_availability"] == "shadow"
