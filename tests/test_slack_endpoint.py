"""The Slack webhook must reject anything not signed by Slack."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.config import Settings

SECRET = "test-signing-secret"


@pytest.fixture
def client(monkeypatch, tmp_path):
    s = Settings(slack_signing_secret=SECRET, database_url=f"sqlite:///{tmp_path}/t.db", _env_file=None)
    monkeypatch.setattr(main, "get_settings", lambda: s)
    import app.db as dbmod

    monkeypatch.setattr(dbmod, "get_settings", lambda: s)
    monkeypatch.setattr(dbmod, "_engine", None)
    clicks = []
    monkeypatch.setattr(main, "_handle_click", lambda *a: clicks.append(a))
    with TestClient(main.app) as c:
        c.clicks = clicks
        yield c


def _signed(body: str, secret=SECRET, ts=None):
    ts = str(ts or int(time.time()))
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:{body}".encode(), hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig,
            "Content-Type": "application/x-www-form-urlencoded"}


def _body(action_id="approve_refund", value="7"):
    payload = {"user": {"name": "srinivas"}, "actions": [{"action_id": action_id, "value": value}]}
    return urlencode({"payload": json.dumps(payload)})


def test_valid_click_is_accepted(client):
    body = _body()
    r = client.post("/slack/actions", content=body, headers=_signed(body))
    assert r.status_code == 200
    assert client.clicks == [(7, True, "srinivas")]


def test_forged_signature_is_rejected(client):
    body = _body()
    r = client.post("/slack/actions", content=body, headers=_signed(body, secret="wrong"))
    assert r.status_code == 401 and client.clicks == []


def test_replayed_old_request_is_rejected(client):
    body = _body()
    r = client.post("/slack/actions", content=body, headers=_signed(body, ts=int(time.time()) - 3600))
    assert r.status_code == 401 and client.clicks == []


def test_reject_button(client):
    body = _body("reject_refund", "3")
    client.post("/slack/actions", content=body, headers=_signed(body))
    assert client.clicks == [(3, False, "srinivas")]
