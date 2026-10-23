import httpx
import pytest

from app.llm.client import InvalidOutput, LLMClient, LLMError, LLMUnavailable, extract_json


def _live(monkeypatch) -> LLMClient:
    c = LLMClient()
    monkeypatch.setattr(c.settings, "llm_mode", "live")
    monkeypatch.setattr(c.settings, "llm_base_url", "https://llm.invalid/v1")
    monkeypatch.setattr(c.settings, "llm_api_key", "x")
    monkeypatch.setattr("time.sleep", lambda *_: None)
    return c


def test_extract_json_from_wrapped_text():
    assert extract_json('Вот ответ:\n```json\n{"a": 1}\n```') == {"a": 1}


def test_without_key_models_are_unavailable(monkeypatch):
    c = LLMClient()
    monkeypatch.setattr(c.settings, "llm_mode", "live")
    monkeypatch.setattr(c.settings, "llm_base_url", "")
    monkeypatch.setattr(c.settings, "llm_api_key", "")
    assert c.mode == "off" and not c.available
    with pytest.raises(LLMUnavailable):
        c.complete("chat", "s", "u")


def test_gateway_failure_raises_instead_of_faking(monkeypatch):
    c = _live(monkeypatch)

    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(c, "_post", boom)
    with pytest.raises(LLMError):
        c.complete("classify", "s", "u")


def test_invalid_output_is_retried_with_feedback(monkeypatch):
    c = _live(monkeypatch)
    sent = []

    def fake(purpose, model, messages, json_mode):
        sent.append(messages)
        return ('{"ok": false}', 10, 5) if len(sent) == 1 else ('{"ok": true}', 10, 5)

    def check(r):
        if not r["ok"]:
            raise InvalidOutput("ok должен быть true")
        return r

    monkeypatch.setattr(c, "_post", fake)
    result, meta = c.complete("chat", "sys-retry", "user-retry", validate=check, use_cache=False)
    assert result == {"ok": True} and len(sent) == 2
    assert "ok должен быть true" in sent[1][-1]["content"] and meta.fallback


def test_live_success_is_cached(monkeypatch):
    c = _live(monkeypatch)
    calls = []

    def fake(purpose, model, messages, json_mode):
        calls.append(model)
        return '{"answer": 42}', 100, 10

    monkeypatch.setattr(c, "_post", fake)
    r1, m1 = c.complete("chat", "sys-unique", "user-unique")
    r2, m2 = c.complete("chat", "sys-unique", "user-unique")
    assert r1 == r2 == {"answer": 42}
    assert len(calls) == 1 and m2.cache_hit and m1.cost_rub > 0
