"""Тестовый транспорт модели: заменяет HTTP-вызов шлюза и возвращает ответы по JSON-схемам промптов."""

import json
import re

from fake_rules import classify_rules, extract_facts

OFFTOPIC = ("погод", "рецепт", "анекдот", "футбол", "фильм")
GREETING = ("привет", "здравствуй", "что ты умеешь", "добрый день")


def _payload(messages: list[dict]) -> dict:
    content = messages[1]["content"]
    if isinstance(content, list):
        return {}
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {}


def classify(p: dict) -> dict:
    title, text = p.get("title", ""), p.get("text", "")
    cls = classify_rules(title, text)
    facts = extract_facts("m", title, text, cls.factors)
    out = cls.model_dump(exclude={"method", "flags"})
    out["facts"] = [{"text": f.text, "quote": f.quote.rstrip("…")} for f in facts]
    return out


def plan(p: dict) -> dict:
    contributions = (p.get("relevance") or {}).get("contributions") or []
    factors = [c["factor"] for c in contributions]
    top = [f["factor"] for f in (p.get("company") or {}).get("top_factors", [])]
    second = next((f for f in factors[1:] + top if f not in factors[:1] and f != "region"), None)
    steps = [
        {"n": 1, "tool": "get_event", "args": {}, "why": "факты"},
        {"n": 2, "tool": "get_company_profile", "args": {}, "why": "признаки"},
        {"n": 3, "tool": "get_factor_exposure", "args": {"factors": factors}, "why": "экспозиция"},
        {
            "n": 4,
            "tool": "search_alfa_analytics",
            "args": {"query": p["event"]["title"], "top_k": 2},
            "why": "банк",
        },
    ]
    if second:
        steps.append(
            {
                "n": 5,
                "tool": "get_related_events",
                "args": {"factor": second, "days": 30},
                "why": "второй фактор",
            }
        )
    if (p.get("company") or {}).get("counterparties"):
        steps.append(
            {"n": 6, "tool": "get_counterparties", "args": {"direction": "both", "top_n": 5}, "why": "связи"}
        )
    steps.append({"n": 7, "tool": "get_bank_products", "args": {}, "why": "продукты"})
    return {"goal": "оценить влияние", "steps": steps, "expected_outputs": ["insight"]}


def _first_sentence(text: str) -> str:
    parts = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 30]
    return parts[0] if parts else text.strip()[:200]


def analyst(p: dict) -> dict:
    n = 0

    def cid():
        nonlocal n
        n += 1
        return f"c{n}"

    mats = p.get("materials", {})
    what = []
    for f in p["event"]["facts"][:3]:
        if f["material_id"] in mats:
            what.append(
                {
                    "id": cid(),
                    "kind": "fact",
                    "text": f["text"],
                    "sources": [
                        {"type": "material", "id": f["material_id"], "quote": f["quote"].rstrip("…")}
                    ],
                }
            )
    feats = list(p.get("features", {}))
    contributions = (p.get("relevance") or {}).get("contributions") or []
    factors = [c["factor"] for c in contributions][:2]
    related = [r for r in p.get("related_events", []) if r.get("factor") not in factors][:1]
    why = []
    for i, factor in enumerate(factors or ["region"]):
        ids = feats[i : i + 2] if len(feats) > i + 1 else feats[:2]
        why.append(
            {
                "id": cid(),
                "kind": "ai",
                "factor": factor,
                "text": f"Фактор {factor} значим для компании с учётом её признаков: {', '.join(p['features'][x] for x in ids)}.",
                "sources": [{"type": "feature", "id": x} for x in ids],
            }
        )
    risks, opps, watch = [], [], []
    for c, w in zip(contributions[:2], why, strict=False):
        target = opps if c.get("polarity") == "opportunity" else risks
        src = [{"type": "claim", "id": w["id"]}]
        if what:
            src.append({"type": "material", "id": what[0]["sources"][0]["id"]})
        target.append(
            {
                "id": cid(),
                "kind": "hypothesis",
                "factor": c["factor"],
                "text": f"Сценарий для фактора {c['factor']}: событие меняет условия бизнеса компании.",
                "sources": src,
            }
        )
    for r in related:
        watch.append(
            {
                "id": cid(),
                "kind": "hypothesis",
                "factor": r["factor"],
                "text": f"Связанное событие «{r['title']}» усиливает эффект.",
                "sources": [{"type": "event", "id": r["event_id"]}],
            }
        )
    actions = []
    products = {pr["factor"]: pr for pr in p.get("products", [])}
    for h in (risks + opps)[:2]:
        a = {
            "id": cid(),
            "kind": "ai",
            "factor": h["factor"],
            "text": f"Проверить влияние фактора {h['factor']} на бюджет и договоры компании на ближайший квартал.",
            "sources": [{"type": "claim", "id": h["id"]}],
        }
        if h["factor"] in products:
            a["bank_product"] = products[h["factor"]]["code"]
        actions.append(a)
    bank = []
    for aid, a in list(p.get("alfa", {}).items())[:1]:
        quote = _first_sentence(a["text"])
        bank.append(
            {
                "id": cid(),
                "kind": "bank",
                "text": quote,
                "sources": [{"type": "alfa", "id": aid, "quote": quote}],
            }
        )
    pol = "risk" if risks and not opps else "opportunity" if opps and not risks else "mixed"
    return {
        "title": f"{p['event']['title']} — что это значит для компании",
        "summary": "Событие затрагивает ключевые для компании факторы; стоит проверить бюджет и договоры.",
        "sections": {
            "what_happened": what,
            "why_it_matters": why,
            "consequences": {"risks": risks, "opportunities": opps, "watch_factors": watch},
            "actions": actions,
            "bank_view": bank,
        },
        "factors": factors + [r["factor"] for r in related],
        "polarity": pol,
        "horizon": "1-3m",
        "confidence": 0.7,
        "impact_note": "",
        "missing": [] if bank else ["Аналитика банка по теме отсутствует"],
    }


def chat(p: dict) -> dict:
    q = p.get("question", "").lower()
    if any(w in q for w in GREETING):
        return {
            "type": "help",
            "text": "Я отвечаю на вопросы о событиях, аналитике банка и их влиянии на компанию.",
            "claims": [],
        }
    if any(w in q for w in OFFTOPIC):
        return {
            "type": "refusal",
            "text": "Это вне моей темы: спросите о новостях и их влиянии на компанию.",
            "claims": [],
        }
    claims = []
    if p.get("insight"):
        for c in p["insight"]["claims"][:2]:
            claims.append({k: c[k] for k in ("kind", "text", "sources")})
    for n in p.get("news", [])[:1]:
        claims.append(
            {"kind": "fact", "text": n["excerpt"], "sources": [{"type": "material", "id": n["id"]}]}
        )
    text = " ".join(c["text"] for c in claims) or "По этому вопросу в данных ничего не нашлось."
    return {"type": "answer", "text": text, "claims": claims}


RESPONDERS = {
    "classify": classify,
    "planner": plan,
    "analyst": analyst,
    "verifier": lambda p: {"issues": []},
    "chat": chat,
    "digest": lambda p: {"text": "Сводка: " + "; ".join(i["title"] for i in p.get("insights", [])[:3])},
    "profile_summary": lambda p: {
        "text": f"Компания отрасли «{p['industry'].get('name')}», регион {p['geo'].get('region')}. Чувствительна к факторам из профиля."
    },
    "vision": lambda p: {"text": ""},
}


def transport(purpose: str, model_key: str, messages: list[dict], json_mode: bool) -> tuple[str, int, int]:
    result = RESPONDERS[purpose](_payload(messages))
    text = json.dumps(result, ensure_ascii=False)
    return text, len(json.dumps(messages, ensure_ascii=False)) // 3, len(text) // 3
