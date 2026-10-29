"""Evidence links are applied only on request, as one reversible batch in one transaction; a
retry adds nothing; readers credit a linked record to its FPL player only from the link's
`valid_from`, never twice, and source rows keep their own ids."""

import json
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from fpl_agent.cli import main
from fpl_agent.db import player_links
from fpl_agent.db.database import FootballDB
from fpl_agent.db.link_audit import coverage
from fpl_agent.model import features
from tests.test_link_candidates import CUT, SNAP, T0, fotmob, fpl, season


def league(db):
    season(db)
    p = fpl(db, "Bukayo Saka", {1: 77, 2: 90, 3: 64})
    q = fotmob(db, "B. Saka", {1: 77, 2: 90, 3: 65})
    return p, q


def view(db, as_of, decided_at=None):
    """Who each match row is credited to, for a decision made at `decided_at` (default: now)."""
    decided = decided_at or datetime.now(UTC).replace(tzinfo=None)
    pm = player_links.resolve(features.match_frame(db), as_of, decided)
    return sorted(zip(pm["match_id"], pm["player_id"], strict=True))


def test_apply_retry_undo_round_trip():
    db = FootballDB()
    p, q = league(db)
    before = view(db, CUT)
    raw = db.query("SELECT * FROM appearances ORDER BY ALL")
    r = player_links.apply(db, 2026, CUT)
    assert r["added"] == 1 and r["batch_id"]
    assert view(db, CUT) == [(1, p), (2, p), (3, p)]
    assert coverage(db, 2026)["linked_by_evidence"] == 1
    again = player_links.apply(db, 2026, CUT)
    assert (again["added"], again["already_active"], again["batch_id"]) == (0, 0, None)
    reviewed = [{"fpl_player_id": p, "candidate": q}]
    assert player_links.apply(db, 2026, CUT, reviewed)["already_active"] == 1
    assert db.query("SELECT count(*) n FROM player_links").n[0] == 1
    assert player_links.undo(db, r["batch_id"]) == 1
    assert player_links.undo(db, r["batch_id"]) == 0
    assert view(db, CUT) == before
    pd.testing.assert_frame_equal(db.query("SELECT * FROM appearances ORDER BY ALL"), raw)
    assert player_links.batches(db)[0]["active"] == 0


def test_a_link_is_used_only_from_its_valid_from():
    db = FootballDB()
    p, q = league(db)
    player_links.apply(db, 2026, CUT)
    valid = db.query("SELECT valid_from FROM player_links").valid_from[0]
    assert valid == pd.Timestamp(T0 + timedelta(weeks=1, days=1)).normalize()
    day_before = (valid - timedelta(days=1)).date()
    assert {pid for _, pid in view(db, day_before)} == {q}
    assert {pid for _, pid in view(db, valid.date())} == {p}


def test_a_past_decision_never_uses_a_link_written_after_it():
    db = FootballDB()
    p, q = league(db)
    player_links.apply(db, 2026, CUT)
    rec = db.query("SELECT recorded_at FROM player_links").recorded_at[0]
    later = (rec + timedelta(days=30)).date()  # evidence long valid by then
    assert {pid for _, pid in view(db, later, rec - timedelta(seconds=1))} == {q}
    assert {pid for _, pid in view(db, later, rec)} == {p}
    assert player_links.resolve(features.match_frame(db), CUT.date())["player_id"].eq(q).all()
    db.con.execute("UPDATE player_links SET recorded_at = NULL")
    assert {pid for _, pid in view(db, later)} == {q}  # an untimed link is never used


def test_coverage_at_a_time_counts_only_links_usable_then():
    db = FootballDB()
    p, q = league(db)
    player_links.apply(db, 2026, CUT)
    valid, rec = db.query("SELECT valid_from, recorded_at FROM player_links").iloc[0]
    assert coverage(db, 2026)["linked_by_evidence"] == 1
    assert coverage(db, 2026, at=valid - timedelta(seconds=1))["linked_by_evidence"] == 0
    assert coverage(db, 2026, at=rec - timedelta(seconds=1))["linked_by_evidence"] == 0
    assert coverage(db, 2026, at=rec)["linked_by_evidence"] == 1
    db.con.execute("UPDATE player_links SET valid_from = ?", [rec + timedelta(days=2)])
    assert coverage(db, 2026, at=rec + timedelta(days=1))["linked_by_evidence"] == 0


def test_a_failure_inside_the_batch_leaves_nothing():
    db = FootballDB()
    league(db)
    real = db.con

    class FailAfterInsert:  # the connection, failing on the check that runs after the insert
        def __getattr__(self, name):
            return getattr(real, name)

        def execute(self, sql, *args):
            if sql.startswith("SELECT count(*) FROM (SELECT from_player_id"):
                assert real.execute("SELECT count(*) FROM player_links").fetchone()[0] == 1
                raise RuntimeError("disk full")
            return real.execute(sql, *args)

    db.con = FailAfterInsert()
    with pytest.raises(RuntimeError):
        player_links.apply(db, 2026, CUT)
    db.con = real
    assert db.query("SELECT count(*) n FROM player_links").n[0] == 0


def test_conflicting_or_stale_batches_are_refused_whole():
    db = FootballDB()
    p, q = league(db)
    other = fpl(db, "Someone", {4: 10})
    db.con.execute("INSERT INTO player_links VALUES ('x:1', 'x', 'fotmob', 2026, ?, ?, ?, '{}', "
                   "now(), NULL)", [q, other, SNAP])  # fmt: skip
    with pytest.raises(player_links.LinkConflict, match="no longer accepted"):
        player_links.apply(db, 2026, CUT, [{"fpl_player_id": p, "candidate": q}])
    db.con.execute("UPDATE player_links SET to_player_id = ?, from_player_id = 999", [p])
    with pytest.raises(player_links.LinkConflict):
        player_links.apply(db, 2026, CUT, [{"fpl_player_id": p, "candidate": q}])
    assert db.query("SELECT count(*) n FROM player_links").n[0] == 1


def test_a_linked_record_never_doubles_a_players_own_row():
    pm = pd.DataFrame({"match_id": [1, 1, 2], "player_id": [5, 9, 9],
                       "link_to": pd.array([None, 5, 5], dtype="Int32"),
                       "link_valid_from": pd.to_datetime([None, "2026-01-01", "2026-01-01"]),
                       "link_recorded_at": pd.to_datetime([None, "2026-01-02", "2026-01-02"])})  # fmt: skip
    out = player_links.resolve(pm, date(2026, 2, 1))
    assert sorted(zip(out["match_id"], out["player_id"], strict=True)) == [(1, 5), (2, 5)]
    no_time = pm.drop(columns=["link_recorded_at"])  # an untimed link is never used
    assert player_links.resolve(no_time, date(2026, 2, 1))["player_id"].tolist() == [5, 9, 9]
    plain = pm.drop(columns=["link_to", "link_valid_from", "link_recorded_at"])
    assert player_links.resolve(plain, date(2026, 2, 1)) is plain


def test_cli_needs_an_explicit_database_and_reads_old_files(tmp_path, capsys):
    assert main(["db-links", "--apply"]) == 1
    assert "--db" in capsys.readouterr().out
    path = tmp_path / "f.duckdb"
    db = FootballDB(path)
    p, q = league(db)
    db.close()
    assert main(["db-links", "--db", str(path), "--apply", "--cutoff", CUT.isoformat()]) == 0
    batch = json.loads(capsys.readouterr().out)["batch_id"]
    assert main(["db-links", "--db", str(path), "--batches"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["active"] == 1
    assert main(["db-links", "--db", str(path), "--undo", batch]) == 0
    assert main(["db-links", "--db", str(path), "--undo", batch]) == 1
    capsys.readouterr()
    old = FootballDB(path)
    old.con.execute("DROP TABLE player_links")
    old.close()
    ro = FootballDB(path, schema="read")
    pm = features.match_frame(ro)
    assert "link_to" not in pm.columns and set(pm["player_id"]) == {q}
    ro.close()


def test_proposals_cli_writes_a_file(tmp_path):
    path = tmp_path / "f.duckdb"
    db = FootballDB(path)
    p, q = league(db)
    db.close()
    out = tmp_path / "p.json"
    assert main(["db-links", "--db", str(path), "--propose", "--cutoff",
                 datetime(2026, 10, 1).isoformat(), "--out", str(out)]) == 0  # fmt: skip
    got = json.loads(out.read_text(encoding="utf-8"))["accepted"]
    assert [(a["fpl_player_id"], a["candidate"]) for a in got] == [(p, q)]


def test_the_projection_credits_a_linked_record_only_once_valid(monkeypatch):
    from fpl_agent.model import project
    from fpl_agent.model.simulate import SimConfig
    from tests.synth import build_world

    db = build_world(n_teams=6, seasons=(2023, 2024), seed=7).db
    as_of = date(2024, 10, 20)

    def minutes_of(pid):
        proj = project.build_projection(db, 2024, [10], as_of=as_of,
                                        cfg=SimConfig(n=50, seed=1), pool_gw=9)  # fmt: skip
        return proj.meta.set_index("player_id").evidence_minutes[pid]

    pid = int(db.query("SELECT player_id FROM appearances GROUP BY 1 ORDER BY sum(minutes) DESC "
                       "LIMIT 1").player_id[0])  # fmt: skip
    own = minutes_of(pid)
    q = int(db.query("SELECT max(player_id) + 1 AS q FROM players").q[0])
    db.con.execute("INSERT INTO players VALUES (?, 'Record', NULL, 'MID')", [q])
    for t in ("appearances", "player_match_stats"):
        db.con.execute(f"UPDATE {t} SET player_id = ? WHERE player_id = ?", [q, pid])
    assert minutes_of(pid) < own  # the record is not theirs until linked
    db.con.execute("INSERT INTO player_links VALUES ('t:1', 't', 'fotmob', 2024, ?, ?, ?, '{}', "
                   "?, NULL)", [q, pid, datetime(2024, 10, 21), datetime(2024, 10, 1)])  # fmt: skip
    assert minutes_of(pid) < own  # valid only after this cutoff
    db.con.execute("UPDATE player_links SET valid_from = ?, recorded_at = ?",
                   [datetime(2024, 10, 20), datetime(2024, 10, 21)])  # fmt: skip
    assert minutes_of(pid) < own  # written only after this decision
    db.con.execute("UPDATE player_links SET recorded_at = ?", [datetime(2024, 10, 19)])
    # the synthetic world stores no evidence the rule accepts, so revalidation suspends it ...
    assert minutes_of(pid) < own
    # ... and once the evidence supports it, the record is credited to the player
    monkeypatch.setattr(player_links, "unsupported", lambda *a: frozenset())
    assert minutes_of(pid) == own


def linked_league():
    """A link written on 1 September, after its evidence (matches 1-3 stored by 5 September)."""
    db = FootballDB()
    p, q = league(db)
    player_links.apply(db, 2026, CUT)
    db.con.execute("UPDATE player_links SET recorded_at = ?, valid_from = ?",
                   [datetime(2026, 9, 6), datetime(2026, 9, 6)])  # fmt: skip
    return db, p, q


def test_a_conflict_on_a_linked_record_excludes_the_match():
    from fpl_agent.db.eligibility import excluded_fixtures
    from fpl_agent.db.quality import source_conflicts
    from tests.test_link_candidates import fpl as fpl_rows

    db, p, q = linked_league()
    assert source_conflicts(db) == []
    fpl_rows(db, "Bukayo Saka", {4: (90, {"goals_scored": 1})})
    db.con.execute("INSERT INTO appearances VALUES (4, ?, 1, 'fotmob', false, 20, 'FWD', ?)",
                   [q, datetime(2026, 9, 12, 18)])  # fmt: skip
    db.con.execute("INSERT INTO player_match_stats VALUES (4, ?, 'fotmob', 'goals', 0, ?)",
                   [q, datetime(2026, 9, 12, 18)])  # fmt: skip
    found = source_conflicts(db)
    assert [(c["match_id"], c["player_id"], c["linked_record"], c["kinds"]) for c in found] == [
        (4, p, q, ["goals", "minutes"])]  # fmt: skip
    assert excluded_fixtures(db) == ({4}, {(2026, 104)})


def test_later_contradicting_evidence_suspends_the_link_from_then_on():
    from fpl_agent.db.link_candidates import revalidate
    from fpl_agent.db.quality import run_checks
    from tests.test_link_candidates import fpl as fpl_rows

    db, p, q = linked_league()
    assert revalidate(db, 2026) == []
    # match 4: FPL says the player sat out, FotMob has the record playing 90 for his side
    fpl_rows(db, "Bukayo Saka", {4: 0})
    db.con.execute("INSERT INTO appearances VALUES (4, ?, 1, 'fotmob', true, 90, 'FWD', ?)",
                   [q, datetime(2026, 9, 12, 18)])  # fmt: skip
    stale = revalidate(db, 2026)
    assert [(r["from_player_id"], r["to_player_id"], r["reason"]) for r in stale] == [
        (q, p, "contradicted by the evidence")]  # fmt: skip
    assert revalidate(db, 2026, datetime(2026, 9, 10)) == []  # not known before match 4
    assert player_links.unsupported(db, 2026, datetime(2026, 9, 10)) == frozenset()
    assert player_links.unsupported(db, 2026, datetime(2026, 9, 13)) == {q}
    pm = features.match_frame(db)
    before = player_links.resolve(pm, date(2026, 9, 10), datetime(2026, 9, 10))
    assert set(before["player_id"]) == {p}
    after = player_links.resolve(pm, date(2026, 9, 13), datetime(2026, 9, 13),
                                 player_links.unsupported(db, 2026, datetime(2026, 9, 13)))  # fmt: skip
    assert set(after["player_id"]) == {q}
    r = coverage(db, 2026)
    assert r["linked_by_evidence"] == 0 and r["unsupported_links"][0]["from_player_id"] == q
    assert "player_link_unsupported" in {i.check for i in run_checks(db)}


def test_apply_undo_apply_in_one_second_keeps_every_batch():
    db = FootballDB()
    p, q = league(db)
    first = player_links.apply(db, 2026, CUT)["batch_id"]
    player_links.undo(db, first)
    second = player_links.apply(db, 2026, CUT)["batch_id"]
    assert first != second
    hist = {b["batch_id"]: b for b in player_links.batches(db)}
    assert hist[first]["active"] == 0 and hist[first]["rolled_back_at"]
    assert hist[second]["active"] == 1
    assert db.query("SELECT count(*) n FROM player_links").n[0] == 2


def test_coverage_is_per_season_and_the_reader_follows_identity_across_seasons():
    from fpl_agent.db.quality import run_checks
    from tests.test_link_candidates import kick

    db, p, q = linked_league()
    # last season: the player played, the record has a European match on file
    db.upsert("fpl_points_gw", pd.DataFrame([dict(
        season=2025, gw=30, fpl_id=1, player_id=p, fixture_id=900, minutes=90, total_points=2)]))  # fmt: skip
    db.con.execute("INSERT INTO matches VALUES (90, 'UCL', 2025, 1, ?, ?, 1, 2, 1, 0, true, "
                   "'fotmob', now())", [kick(1) - timedelta(days=200), (kick(1) - timedelta(days=200)).date()])  # fmt: skip
    db.con.execute("INSERT INTO appearances VALUES (90, ?, 1, 'fotmob', true, 90, 'FWD', ?)",
                   [q, kick(1) - timedelta(days=199)])  # fmt: skip
    assert coverage(db, 2026)["linked_by_evidence"] == 1
    old = coverage(db, 2025)  # a 2026 link says nothing about 2025's data
    assert old["linked_by_evidence"] == 0 and old["minute_share"] == 0.0
    # identity does not change: a decision allowed to use the link credits all of the record's
    # rows, any season; one made before the link was written credits none
    pm = features.match_frame(db)
    now = player_links.resolve(pm, date(2026, 9, 7), datetime(2026, 9, 7))
    assert set(zip(now["match_id"], now["player_id"], strict=True)) >= {(90, p), (1, p)}
    early = player_links.resolve(pm, date(2026, 9, 7), datetime(2026, 9, 5))
    assert set(early["player_id"]) == {q}
    db.con.execute("DELETE FROM players WHERE player_id = ?", [q])
    assert "orphan_player_links.from_player_id" in {i.check for i in run_checks(db)}


def test_contradicting_evidence_without_a_stored_time_still_withdraws_the_link():
    from fpl_agent.db.link_candidates import revalidate

    db, p, q = linked_league()
    db.upsert(
        "fpl_points_gw",
        pd.DataFrame(
            [
                dict(
                    season=2026,
                    gw=4,
                    fpl_id=1,
                    player_id=p,
                    fixture_id=104,
                    minutes=0,
                    total_points=0,
                    kickoff=datetime(2026, 9, 12, 15),
                    stats=json.dumps({"minutes": 0}),
                )
            ]
        ),
    )  # no ingested_at
    db.con.execute("INSERT INTO appearances VALUES (4, ?, 1, 'fotmob', true, 90, 'FWD', NULL)",
                   [q])  # fmt: skip
    assert [r["reason"] for r in revalidate(db, 2026)] == ["contradicted by the evidence"]
    assert coverage(db, 2026)["linked_by_evidence"] == 0
    assert player_links.batches(db)[0]["active"] == 1  # reported, never changed by a report
