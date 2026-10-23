from datetime import UTC, datetime, timedelta

import httpx
import pytest

from fpl_agent.config import Settings
from fpl_agent.data.calendar import hours_to_deadline, in_action_window, upcoming_deadlines
from fpl_agent.data.fpl_client import FPLClient
from fpl_agent.data.players import fixtures_frame, players_frame
from fpl_agent.data.schemas import Bootstrap, Fixture
from fpl_agent.data.store import SnapshotStore
from fpl_agent.errors import SourceUnavailable


def test_schema_validates_and_keeps_extras(bootstrap_raw):
    bs = Bootstrap.model_validate(bootstrap_raw)
    assert len(bs.elements) == 30
    assert bs.elements[0].model_extra  # extra FPL fields are preserved


def test_players_frame(bootstrap_raw):
    df = players_frame(bootstrap_raw)
    assert set(df["position"]).issubset({"GKP", "DEF", "MID", "FWD"})
    assert (df["price"] == df["now_cost"] / 10).all()
    assert df["team_short"].notna().all()


def test_fixtures_frame(fixtures_raw):
    fx = [Fixture.model_validate(f) for f in fixtures_raw]
    df = fixtures_frame(fixtures_raw)
    assert len(df) == len(fx)
    assert "stats" not in df.columns


def test_calendar(bootstrap_raw):
    bs = Bootstrap.model_validate(bootstrap_raw)
    ev = bs.events[3]
    now = ev.deadline_time - timedelta(hours=10)
    assert in_action_window(ev, now)
    assert in_action_window(ev, ev.deadline_time - timedelta(hours=1))  # the final re-plan
    assert not in_action_window(ev, ev.deadline_time - timedelta(minutes=30))
    assert not in_action_window(ev, ev.deadline_time - timedelta(hours=40))
    assert hours_to_deadline(ev, now) == pytest.approx(10)
    ups = upcoming_deadlines(bs, now=datetime(2026, 8, 1, tzinfo=UTC))
    assert ups[0][0] == 1


def test_store_replay_by_time(tmp_path):
    store = SnapshotStore(tmp_path)
    t1 = datetime(2026, 10, 1, 12, tzinfo=UTC)
    t2 = datetime(2026, 10, 2, 12, tzinfo=UTC)
    store.write("bootstrap", 8, {"v": 1}, now=t1)
    store.write("bootstrap", 8, {"v": 2}, now=t2)
    assert store.latest("bootstrap", 8) == {"v": 2}
    assert store.latest("bootstrap", 8, before=t1 + timedelta(hours=1)) == {"v": 1}
    with pytest.raises(FileNotFoundError):
        store.latest("bootstrap", 9)


def test_client_retries_then_succeeds(bootstrap_raw):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=bootstrap_raw)

    client = FPLClient(client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0)
    assert len(client.bootstrap().elements) == 30
    assert calls["n"] == 3


def test_client_does_not_retry_404():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404)

    client = FPLClient(client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0)
    with pytest.raises(SourceUnavailable):
        client.bootstrap_raw()
    assert calls["n"] == 1


def test_settings_default_dry(monkeypatch):
    monkeypatch.delenv("AGENT_MODE", raising=False)
    s = Settings.from_env()
    assert s.mode == "dry" and not s.is_live
    monkeypatch.setenv("AGENT_MODE", "bogus")
    assert Settings.from_env().mode == "dry"
    monkeypatch.setenv("AGENT_MODE", "live")
    monkeypatch.setenv("AGENT_ENABLED", "false")
    assert not Settings.from_env().is_live


def test_retry_after_is_honoured_and_capped(monkeypatch):
    import httpx

    from fpl_agent.data import fpl_client
    from fpl_agent.data.fpl_client import FPLClient

    waits = []
    monkeypatch.setattr(fpl_client.time, "sleep", waits.append)
    replies = iter([httpx.Response(429, headers={"Retry-After": "7"}),
                    httpx.Response(503, headers={"Retry-After": "600"}),
                    httpx.Response(200, json={"ok": 1})])  # fmt: skip
    c = FPLClient(client=httpx.Client(transport=httpx.MockTransport(lambda r: next(replies))))
    assert c._get("x") == {"ok": 1}
    assert waits == [7.0, FPLClient.MAX_WAIT]


def test_only_a_client_it_created_is_closed():
    import httpx

    from fpl_agent.data.fpl_client import FPLClient

    mine = httpx.Client()
    with FPLClient(client=mine):
        pass
    assert not mine.is_closed  # an injected client stays open for its owner
    own = FPLClient()
    own.close()
    assert own._http.is_closed


def test_retry_after_date_and_negative_delay_are_bounded():
    import httpx

    from fpl_agent.data.fpl_client import FPLClient

    with FPLClient() as client:
        req = httpx.Request("GET", "https://fixture.example")
        for header, expected in [("-3", 0), ("Thu, 01 Jan 2099 00:00:00 GMT", client.MAX_WAIT)]:
            res = httpx.Response(429, request=req, headers={"Retry-After": header})
            err = httpx.HTTPStatusError("fixture", request=req, response=res)
            assert client._wait(err, 0) == expected
