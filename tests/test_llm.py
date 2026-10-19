import httpx

from app.llm.client import LLMClient, extract_json


def test_extract_json_from_wrapped_text():
    assert extract_json('Вот ответ:\n```json\n{"a": 1}\n```') == {"a": 1}


def test_live_failure_falls_back_to_deterministic(monkeypatch):
    c = LLMClient()
    monkeypatch.setattr(c.settings, "llm_mode", "live")
    monkeypatch.setattr(c.settings, "llm_base_url", "https://llm.invalid/v1")
    monkeypatch.setattr(c.settings, "llm_api_key", "x")

    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(c, "_post", boom)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    result, meta = c.complete("classify", "s", "u", mock=lambda: {"ok": True})
    assert result == {"ok": True} and meta.fallback and meta.errors


def test_live_success_is_cached(monkeypatch):
    c = LLMClient()
    monkeypatch.setattr(c.settings, "llm_mode", "live")
    monkeypatch.setattr(c.settings, "llm_base_url", "https://llm.invalid/v1")
    monkeypatch.setattr(c.settings, "llm_api_key", "x")
    calls = []

    def fake(model, messages, json_mode):
        calls.append(model)
        return '{"answer": 42}', 100, 10

    monkeypatch.setattr(c, "_post", fake)
    r1, m1 = c.complete("chat", "sys-unique", "user-unique", mock=lambda: {})
    r2, m2 = c.complete("chat", "sys-unique", "user-unique", mock=lambda: {})
    assert r1 == r2 == {"answer": 42}
    assert len(calls) == 1 and m2.cache_hit and m1.cost_rub > 0
