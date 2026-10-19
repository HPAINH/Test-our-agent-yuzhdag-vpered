import json
import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import load_factor_rules
from app.db.models import AppUser, Company, Insight, Material
from app.llm.client import get_llm
from app.processing.text import BM25, best_sentence, stems

QUICK = {
    "risks": ("только риски", "риски"),
    "debt": ("влияние на долг", "долг", "кредит", "ставк"),
    "bank": ("что говорит банк", "позиция банка", "мнение банка"),
    "sources": ("первоисточник", "источник"),
    "compare": ("сравни", "прошл", "связан"),
    "actions": ("что делать", "действи"),
}
CHAT_SYSTEM = """Ты — ассистент аналитики Альфа-Банка для руководителя компании.
Отвечай только на основе контекста: инсайта, первоисточников, аналитики банка и профиля компании.
Каждое утверждение — объект claims[] с kind fact|bank|ai и sources [{"type": "material"|"alfa"|"claim"|"feature", "id": ...}].
Если вопрос не о бизнесе компании, новостях или аналитике — вежливо откажись. Если данных нет — так и скажи.
Верни JSON: {"text": "краткий ответ 2-5 предложений", "claims": [...]}"""


def _intent(message: str) -> str | None:
    low = message.lower()
    for name, keys in QUICK.items():
        if any(k in low for k in keys):
            return name
    return None


def _claims_text(claims: list[dict]) -> str:
    return " ".join(c["text"] for c in claims)


def _quick_answer(intent: str, body: dict, session: Session) -> dict | None:
    s = body["sections"]
    cons = s["consequences"]
    if intent == "risks":
        claims = cons["risks"]
        return {
            "text": "Риски для компании: "
            + (_claims_text(claims) if claims else "по этому событию явных рисков нет."),
            "claims": claims,
        }
    if intent == "debt":
        claims = [
            c
            for c in cons["risks"] + cons["opportunities"] + cons["watch_factors"] + s["actions"]
            if c.get("factor") in ("key_rate", "bonds")
        ]
        text = (
            _claims_text(claims)
            if claims
            else "Это событие напрямую не меняет стоимость долга компании: факторы ставки и долгового рынка в нём не затронуты."
        )
        return {"text": text, "claims": claims}
    if intent == "bank":
        claims = s["bank_view"]
        text = (
            ("Позиция Центра макроэкономических исследований Альфа-Банка: " + _claims_text(claims))
            if claims
            else "Аналитика банка по этой теме в базе отсутствует."
        )
        return {"text": text, "claims": claims}
    if intent == "sources":
        mats = session.scalars(
            select(Material).where(Material.id.in_(s["evidence"]["materials"] or []))
        ).all()
        claims = [
            {
                "id": f"s{i}",
                "kind": "fact",
                "text": f"{m.source}: «{m.title}»",
                "sources": [{"type": "material", "id": m.id, "label": m.source}],
            }
            for i, m in enumerate(mats, 1)
        ]
        return {
            "text": "Первоисточники вывода: " + "; ".join(f"{m.source} — «{m.title}»" for m in mats) + ".",
            "claims": claims,
        }
    if intent == "compare":
        claims = [c for c in cons["watch_factors"] if any(src["type"] == "event" for src in c["sources"])]
        text = _claims_text(claims) if claims else "Связанных событий за последние 30 дней не найдено."
        return {"text": text, "claims": claims}
    if intent == "actions":
        claims = s["actions"]
        return {"text": "Возможные действия: " + _claims_text(claims), "claims": claims}
    return None


def answer(
    session: Session,
    user: AppUser,
    message: str,
    insight_id: str | None = None,
    history: list[dict] | None = None,
) -> dict:
    company = session.get(Company, user.company_id)
    body = None
    if insight_id:
        ins = session.get(Insight, insight_id)
        if ins and ins.company_id == user.company_id:
            body = ins.body
    intent = _intent(message)
    if body and intent:
        quick = _quick_answer(intent, body, session)
        if quick and (quick["claims"] or intent in ("bank", "compare")):
            return {**quick, "mode": "quick", "intent": intent}

    news = {
        m.id: m
        for m in session.scalars(
            select(Material).where(
                Material.kind == "news",
                or_(Material.submitted_by.is_(None), Material.submitted_by == user.login),
            )
        )
    }
    alfa = {m.id: m for m in session.scalars(select(Material).where(Material.kind == "alfa"))}
    query = message
    news_hits = BM25({k: f"{m.title} {m.text}" for k, m in news.items()}).search(query, top_k=3)
    alfa_hits = BM25({k: f"{m.title} {m.text}" for k, m in alfa.items()}).search(query, top_k=2)
    catalog = load_factor_rules().get("catalog", {})
    q_stems = set(stems(message))
    own_news = BM25({k: f"{m.title} {m.text}" for k, m in news.items()}).search(message, top_k=1)
    own_alfa = BM25({k: f"{m.title} {m.text}" for k, m in alfa.items()}).search(message, top_k=1)
    keyword_hit = any(
        any(st.startswith(k.split()[0][:5]) for st in q_stems)
        for f in catalog.values()
        for k in f.get("keywords", [])
    )
    business_words = {
        "компан",
        "бизнес",
        "риск",
        "банк",
        "новост",
        "выручк",
        "долг",
        "кредит",
        "продаж",
        "рынок",
        "рынк",
    }
    topical = (
        keyword_hit
        or any(st.startswith(w) for st in q_stems for w in business_words)
        or (own_news and own_news[0][1] >= 3.0)
        or (own_alfa and own_alfa[0][1] >= 3.0)
    )
    if not topical:
        return {
            "text": "Я отвечаю на вопросы о новостях, аналитике банка и их влиянии на вашу компанию. "
            "Попробуйте спросить, например, «как ставка влияет на наш долг?».",
            "claims": [],
            "mode": "refusal",
        }

    claims = []
    for i, (mid, _) in enumerate(news_hits[:2], 1):
        m = news[mid]
        sent = best_sentence(m.text, message, 35)
        claims.append(
            {
                "id": f"n{i}",
                "kind": "fact",
                "text": sent,
                "sources": [{"type": "material", "id": mid, "quote": sent, "label": m.source}],
            }
        )
    for i, (aid, _) in enumerate(alfa_hits[:1], 1):
        m = alfa[aid]
        sent = best_sentence(m.text, message, 40)
        claims.append(
            {
                "id": f"b{i}",
                "kind": "bank",
                "text": sent,
                "sources": [{"type": "alfa", "id": aid, "quote": sent, "label": f"{m.report}, с. {m.page}"}],
            }
        )
    factors = {f["factor"]: f for f in company.profile.get("factors", [])}
    touched = [
        f
        for f, spec in catalog.items()
        if f in factors
        and any(any(st.startswith(k.split()[0]) for st in q_stems) for k in spec.get("keywords", []))
    ]
    if touched:
        f = factors[touched[0]]
        name = catalog[touched[0]]["name"]
        claims.append(
            {
                "id": "a1",
                "kind": "ai",
                "text": f"Для вашей компании фактор «{name}» имеет вес {f['weight']:.2f} "
                f"({'риск' if f['polarity'] == 'risk' else 'возможность' if f['polarity'] == 'opportunity' else 'двоякое влияние'}); "
                f"основания: {', '.join(d for d in f['drivers'] if d != 'base')[:200] or 'базовая чувствительность'}.",
                "sources": [{"type": "feature", "id": d.split("=")[0]} for d in f["drivers"] if d != "base"][
                    :3
                ]
                or [{"type": "feature", "id": "MAINOKVED_CODE"}],
            }
        )
    parts = []
    facts = [c["text"] for c in claims if c["kind"] == "fact"]
    banks = [c["text"] for c in claims if c["kind"] == "bank"]
    ais = [c["text"] for c in claims if c["kind"] == "ai"]
    if facts:
        parts.append("По данным источников: " + " ".join(facts))
    if banks:
        parts.append("Позиция Альфа-Банка: " + " ".join(banks))
    if ais:
        parts.append(" ".join(ais))
    fallback = {"text": "\n\n".join(parts) or "Данных по вопросу в базе не нашлось.", "claims": claims}

    context = {
        "question": message,
        "history": (history or [])[-10:],
        "insight": body
        and {"title": body["title"], "summary": body["summary"], "sections": body["sections"]},
        "materials": {mid: news[mid].text[:1500] for mid, _ in news_hits},
        "alfa": {aid: alfa[aid].text[:1200] for aid, _ in alfa_hits},
        "company": {
            "industry": company.profile["industry"],
            "factors": company.profile.get("factors", [])[:6],
        },
    }
    result, meta = get_llm().complete(
        "chat", CHAT_SYSTEM, json.dumps(context, ensure_ascii=False, default=str), mock=lambda: fallback
    )
    if not isinstance(result, dict) or "text" not in result:
        result = fallback
    allowed = set(news) | set(alfa)
    clean_claims = []
    for c in result.get("claims", []):
        srcs = [
            src
            for src in c.get("sources", [])
            if src.get("type") in ("feature", "claim") or src.get("id") in allowed
        ]
        if srcs:
            clean_claims.append({**c, "sources": srcs})
    text = re.sub(r"[ \t]+", " ", result["text"]).strip()
    return {"text": text, "claims": clean_claims, "mode": meta.mode if not meta.fallback else "fallback"}
