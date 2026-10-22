from datetime import datetime

from conftest import login

from app.config import get_settings
from app.db.models import AppUser, Company, Event
from app.db.session import session_scope
from app.profile.builder import build_profiles
from app.profile.loader import load_raw
from app.ranking.score import score_event


def _profiles():
    return build_profiles(load_raw(get_settings().data_path))


def test_data_cleaning_rules():
    result = _profiles()
    profiles, report = result["profiles"], result["report"]
    assert "U_ANON" not in profiles
    assert "U_ANON" in report["invalid"]["duplicate_ids"]["anonymization_artifacts"]
    stub = profiles["U_STUB_REV"]
    assert stub["scale"]["revenue_rub"] is None and stub["data_quality"]["revenue_placeholder"]
    assert report["invalid"]["self_transfers"]["rows"] == 1
    assert report["invalid"]["self_transfers"]["amount_share"] > 0.3
    a = profiles["U_DEMO_A"]
    assert all(c["company_id"] != "U_DEMO_A" for c in a["counterparties"])
    ext = next(c for c in profiles["U_DEMO_B"]["counterparties"] if c["company_id"] == "U_EXT_404")
    assert ext["known"] is False
    assert profiles["U_CLOSED"]["org"]["client_status"] == "closed"
    assert profiles["U_DEMO_C"]["org"]["client_status"] == "client"


def test_large_business_by_group_revenue():
    p = _profiles()["profiles"]["U_G42_RENT"]
    assert p["scale"]["revenue_rub"] < 15e9
    assert p["is_large"] and p["scale"]["large_basis"] == "group"


def test_group_member_industry_counts_for_relevance():
    profile = _profiles()["profiles"]["U_DEMO_A"]
    ev = Event(
        id="ev_test_group",
        title="Цены на энергетический уголь выросли",
        facts=[{"text": "Цены на уголь выросли", "material_id": "m", "quote": "уголь"}],
        material_ids=[],
        classification={"factors": [], "okved_sections": ["05"], "regions": []},
        alfa_refs=[],
        published_at=datetime(2026, 10, 1),
    )
    _, breakdown = score_event(ev, profile, "cfo", datetime(2026, 10, 2))
    assert breakdown["I"] == 0.7 and "группы" in breakdown["industry_match"]


def test_only_active_clients_can_log_in(client):
    with session_scope() as s:
        if s.get(AppUser, "prospect_user") is None:
            s.add(AppUser(login="prospect_user", display_name="Тест", company_id="U_0001", role="cfo"))
    assert client.post("/api/v1/auth/login", json={"login": "prospect_user"}).status_code == 403
    with session_scope() as s:
        s.delete(s.get(AppUser, "prospect_user"))


def test_user_with_several_companies(client):
    h = login(client, "cfo_a")
    me = client.get("/api/v1/me", headers=h).json()
    assert {c["id"] for c in me["companies"]} == {"U_DEMO_A", "U_G17_COAL", "U_G17_TRADE"}
    r = client.post("/api/v1/auth/company", json={"company_id": "U_G17_COAL"}, headers=h)
    assert r.status_code == 200
    h2 = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.get("/api/v1/me", headers=h2).json()["company"]["id"] == "U_G17_COAL"
    assert client.get("/api/v1/feed", headers=h2).status_code == 200
    assert client.get("/api/v1/me", headers=h).json()["company"]["id"] == "U_DEMO_A"
    foreign = client.post("/api/v1/auth/company", json={"company_id": "U_DEMO_B"}, headers=h)
    assert foreign.status_code == 403
    with session_scope() as s:
        assert s.get(AppUser, "cfo_a").company_id == "U_DEMO_A"


def test_hide_and_restore_event(client):
    h = login(client, "cfo_b")
    first = client.get("/api/v1/feed", headers=h).json()["items"][0]
    assert client.post(f"/api/v1/events/{first['event_id']}/hide", headers=h).status_code == 204
    ids = [c["event_id"] for c in client.get("/api/v1/feed", headers=h).json()["items"]]
    assert first["event_id"] not in ids
    hidden = client.get("/api/v1/feed?hidden=only", headers=h).json()["items"]
    assert [c["event_id"] for c in hidden] == [first["event_id"]] and hidden[0]["hidden"]
    assert client.delete(f"/api/v1/events/{first['event_id']}/hide", headers=h).status_code == 204
    ids = [c["event_id"] for c in client.get("/api/v1/feed", headers=h).json()["items"]]
    assert first["event_id"] in ids


def test_service_request_after_insight(client, cfo_a, ceo_b):
    ins = next(c for c in client.get("/api/v1/feed", headers=cfo_a).json()["items"] if c["insight_id"])
    r = client.post(
        "/api/v1/requests", json={"kind": "callback", "insight_id": ins["insight_id"]}, headers=cfo_a
    )
    assert r.status_code == 201 and r.json()["simulated"]
    assert any(x["id"] == r.json()["id"] for x in client.get("/api/v1/requests", headers=cfo_a).json())
    foreign = client.post(
        "/api/v1/requests", json={"kind": "manager", "insight_id": ins["insight_id"]}, headers=ceo_b
    )
    assert foreign.status_code == 404


def test_user_factor_hints_change_profile(client):
    h = login(client, "cfo_b")
    factors = client.get("/api/v1/companies/me/profile", headers=h).json()["profile"]["factors"]
    target = factors[-1]
    r = client.put(f"/api/v1/companies/me/factors/{target['factor']}", json={"mode": "boost"}, headers=h)
    assert r.status_code == 200
    after = client.get("/api/v1/companies/me/profile", headers=h).json()["profile"]["factors"]
    boosted = next(f for f in after if f["factor"] == target["factor"])
    assert boosted["weight"] > target["weight"] and "USER_INPUT=boost" in boosted["drivers"]
    client.put(f"/api/v1/companies/me/factors/{target['factor']}", json={"mode": "mute"}, headers=h)
    after = client.get("/api/v1/companies/me/profile", headers=h).json()["profile"]["factors"]
    assert target["factor"] not in {f["factor"] for f in after}
    client.put(f"/api/v1/companies/me/factors/{target['factor']}", json={"mode": "reset"}, headers=h)
    after = client.get("/api/v1/companies/me/profile", headers=h).json()["profile"]["factors"]
    restored = next(f for f in after if f["factor"] == target["factor"])
    assert restored["weight"] == target["weight"]
    bad = client.put("/api/v1/companies/me/factors/unknown_x", json={"mode": "boost"}, headers=h)
    assert bad.status_code == 404


def test_insight_separates_hypotheses_and_has_impact_and_disclaimer(client, cfo_a):
    ins = next(c for c in client.get("/api/v1/feed", headers=cfo_a).json()["items"] if c["insight_id"])
    data = client.get(f"/api/v1/insights/{ins['insight_id']}", headers=cfo_a).json()
    body = data["insight"]
    cons = body["sections"]["consequences"]
    kinds = {c["kind"] for c in cons["risks"] + cons["opportunities"] + cons["watch_factors"]}
    assert kinds == {"hypothesis"}
    assert all(c["kind"] == "bank" for c in body["sections"]["bank_view"])
    assert body["impact"]["level"] in ("low", "medium", "high")
    assert 0 <= body["impact"]["score"] <= 100 and body["impact"]["basis"]
    assert "не является" in data["disclaimer"] and "инвестиционной рекомендацией" in data["disclaimer"]
    assert set(data["impact_scale"]) == {"low", "medium", "high"}


def test_company_store_keeps_hints_on_reload():
    from app.profile.store import load_companies

    with session_scope() as s:
        c = s.get(Company, "U_DEMO_C")
        p = dict(c.profile)
        p["user_hints"] = {"labor": "mute"}
        c.profile = p
    with session_scope() as s:
        load_companies(s, get_settings().data_path)
    with session_scope() as s:
        c = s.get(Company, "U_DEMO_C")
        assert c.profile["user_hints"] == {"labor": "mute"}
        assert "labor" not in {f["factor"] for f in c.profile["factors"]}
        p = dict(c.profile)
        p.pop("user_hints")
        p["factors"] = p.pop("factors_base")
        c.profile = p
