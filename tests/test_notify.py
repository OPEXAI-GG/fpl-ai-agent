import httpx

from fpl_agent import notify


def client(status, text=""):
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, text=text)))


def test_a_refused_message_is_reported_without_the_webhook_address(capsys):
    ok = notify.send("hi", client(401, "Invalid Webhook Token"), "https://example.test/secret-hook")
    err = capsys.readouterr().err
    assert not ok and "HTTP 401" in err and "Invalid Webhook Token" in err
    assert "secret-hook" not in err


def test_a_sent_message_is_reported(capsys):
    assert notify.send("hi", client(204), "https://example.test/hook")
    assert "notify: sent" in capsys.readouterr().err


def test_no_webhook_says_so(capsys, monkeypatch):
    monkeypatch.delenv("NOTIFY_WEBHOOK", raising=False)
    assert not notify.send("hi")
    assert "no webhook" in capsys.readouterr().err
