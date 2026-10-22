import base64
import hashlib
import hmac
import json
import time

from app.api.launch import sign_launch
from app.config import get_settings


def _forge(claims: dict, secret: str | None = None) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    key = (secret or get_settings().host_launch_secret).encode()
    sig = (
        base64.urlsafe_b64encode(hmac.new(key, payload.encode(), hashlib.sha256).digest())
        .decode()
        .rstrip("=")
    )
    return f"{payload}.{sig}"


def _launch(client, token: str):
    return client.post("/api/v1/auth/launch", json={"launch_token": token})


def _company(client, login: str) -> str:
    users = client.get("/api/v1/auth/users").json()
    return next(u["company"]["id"] for u in users if u["login"] == login)


def test_launch_gives_session_and_target(client):
    token = sign_launch("cfo_a", _company(client, "cfo_a"), "feed")
    r = _launch(client, token)
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["login"] == "cfo_a" and body["target"] == "feed"
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {body['token']}"}).status_code == 200


def test_launch_is_single_use(client):
    token = sign_launch("cfo_a", _company(client, "cfo_a"))
    assert _launch(client, token).status_code == 200
    r = _launch(client, token)
    assert r.status_code == 401 and r.json()["error"]["code"] == "launch_replayed"


def test_launch_rejects_bad_tokens(client):
    company = _company(client, "cfo_a")
    now = int(time.time())
    cases = {
        "launch_bad_signature": _forge(
            {"sub": "cfo_a", "company": company, "exp": now + 60, "nonce": "n" * 16}, "wrong"
        ),
        "launch_expired": _forge({"sub": "cfo_a", "company": company, "exp": now - 1, "nonce": "e" * 16}),
        "launch_ttl_too_long": _forge(
            {"sub": "cfo_a", "company": company, "exp": now + 3600, "nonce": "t" * 16}
        ),
        "launch_unknown_user": _forge(
            {"sub": "ghost", "company": company, "exp": now + 60, "nonce": "u" * 16}
        ),
        "launch_company_mismatch": _forge(
            {"sub": "cfo_a", "company": "OTHER", "exp": now + 60, "nonce": "c" * 16}
        ),
        "launch_malformed": "not-a-token-at-all",
    }
    for code, token in cases.items():
        r = _launch(client, token)
        assert r.status_code == 401, code
        assert r.json()["error"]["code"] == code


def test_launch_target_whitelist(client):
    company = _company(client, "cfo_a")
    for target, expected in [
        ("insight/ins_abc123", "insight/ins_abc123"),
        ("javascript:alert(1)", "home"),
        ("../admin", "home"),
    ]:
        r = _launch(client, sign_launch("cfo_a", company, target))
        assert r.json()["target"] == expected


def test_host_demo_link_and_switch(client, monkeypatch):
    r = client.post("/api/v1/host-demo/launch-link", json={"login": "ceo_b", "target": "feed"})
    assert r.status_code == 200
    link = r.json()
    assert link["url"].startswith("/#/launch?t=") and link["embed_url"].startswith("/#/embed?t=")
    assert _launch(client, link["launch_token"]).json()["user"]["login"] == "ceo_b"

    monkeypatch.setattr(get_settings(), "host_demo", False)
    assert client.post("/api/v1/host-demo/launch-link", json={"login": "ceo_b"}).status_code == 404


def test_launch_is_audited(client, cfo_a):
    _launch(client, "broken.token")
    rows = client.get("/api/v1/admin/audit", headers=cfo_a).json()
    items = rows["items"] if isinstance(rows, dict) else rows
    assert any(a["action"] == "launch" and a["result"] == "denied" for a in items)
