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


def test_a_long_report_is_split_on_lines_and_nothing_is_lost():
    from fpl_agent.notify import chunks

    msg = "\n".join(f"line {i} " + "x" * 50 for i in range(100))
    parts = chunks(msg, 500)
    assert all(len(p) <= 500 for p in parts) and "\n".join(parts) == msg
    assert chunks("y" * 1200, 500) == ["y" * 500, "y" * 500, "y" * 200]


def test_interaction_url_is_the_follow_up_webhook():
    assert notify.interaction_url("1", "t") == "https://discord.com/api/v10/webhooks/1/t"
