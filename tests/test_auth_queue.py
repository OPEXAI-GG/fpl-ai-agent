"""Concurrent Actions auth users serialize; queued secrets must still be fresh."""

import httpx
import pytest

from fpl_agent.execution.auth import AuthError, TokenExchange, check_queued_token


@pytest.mark.parametrize(
    "rotated,allowed",
    [
        ("2026-10-20T09:59:59Z", True),
        ("2026-10-20T10:00:00Z", False),
        ("2026-10-20T10:00:01Z", False),
    ],
)
def test_a_waiting_run_cannot_exchange_the_previous_refresh_token(rotated, allowed):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        assert request.headers["Authorization"] == "Bearer fixture-pat"
        payload = (
            {"created_at": "2026-10-20T10:00:00Z"}
            if "/runs/" in request.url.path
            else {"updated_at": rotated}
        )
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        if allowed:
            check_queued_token(client, "owner/repo", "123", "fixture-pat")
        else:
            with pytest.raises(AuthError, match="start a new run"):
                check_queued_token(client, "owner/repo", "123", "fixture-pat")
    assert len(calls) == 2


@pytest.mark.parametrize("status,payload", [(403, {}), (200, {}), (200, {"created_at": "bad"})])
def test_unverifiable_metadata_fails_closed(status, payload):
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(status, json=payload))
        ) as client,
        pytest.raises(AuthError, match="metadata unavailable"),
    ):
        check_queued_token(client, "owner/repo", "123", "fixture-pat")


def test_bad_actions_identity_never_calls_the_network():
    def forbidden(request):
        pytest.fail("network called before identity validation")

    with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
        with pytest.raises(AuthError, match="missing Actions identity"):
            check_queued_token(client, "../other", "123", "fixture-pat")


def test_stale_queue_guard_runs_before_oauth(monkeypatch):
    from fpl_agent.execution import auth

    def stale(*args):
        raise AuthError("stale queued token")

    monkeypatch.setattr(auth, "check_queued_token", stale)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: pytest.fail("OAuth must not run after a queue guard failure")
        )
    ) as client:
        exchange = TokenExchange(
            "fixture",
            "https://fpl.example/token",
            "client",
            lambda *args: None,
            client=client,
            check_queue=True,
        )
        with pytest.raises(AuthError, match="stale queued token"):
            exchange.exchange()
