import json
import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.agent.analyst import feature_ref
from app.config import load_factor_rules
from app.db.models import AppUser, Company, Insight, Material, Relevance
from app.llm.client import InvalidOutput, get_llm
from app.processing.text import BM25, best_sentence
from app.schemas import Claim

CHAT_SYSTEM = """Ты — ассистент аналитики Альфа-Банка для руководителя компании в интернет-банке «Альфа-Бизнес».
Отвечай только по контексту: карточка инсайта (если есть), лента инсайтов компании, найденные фрагменты новостей
и аналитики банка, профиль компании и признаки. Пиши по-русски, коротко (2–6 предложений), с привязкой к компании.
Разделяй: факты из новостей (kind=fact, source material), позицию банка (kind=bank, source alfa — только то, что есть
в отрывке), выводы (kind=ai) и сценарии (kind=hypothesis). Источники бери только из контекста:
{"type": "material"|"alfa"|"claim"|"feature"|"event", "id": ...}.
Если данных не хватает — так и скажи. Не давай инвестиционных рекомендаций.
Если вопрос не связан с бизнесом компании, новостями или аналитикой — вежливо откажись и подскажи, о чём можно спросить.
Верни JSON: {"type": "answer"|"refusal"|"help", "text": "ответ", "claims": [{"kind", "text", "sources": [...]}]}
type=refusal — вопрос не по теме; type=help — приветствие или вопрос о том, что ты умеешь."""

MAX_SNIPPETS = 4


def _material_index(session: Session, user: AppUser) -> tuple[BM25, dict[str, Material]]:
    mats = {
        m.id: m
        for m in session.scalars(
            select(Material).where(
                or_(Material.kind == "alfa", Material.event_id.is_not(None)),
                or_(Material.submitted_by.is_(None), Material.submitted_by == user.login),
            )
        )
    }
    return BM25({mid: f"{m.title}\n{m.text}" for mid, m in mats.items()}), mats


def _card(body: dict) -> dict:
    s = body["sections"]
    claims = []
    for name, items in (
        ("what_happened", s["what_happened"]),
        ("why_it_matters", s["why_it_matters"]),
        ("risks", s["consequences"]["risks"]),
        ("opportunities", s["consequences"]["opportunities"]),
        ("watch_factors", s["consequences"]["watch_factors"]),
        ("actions", s["actions"]),
        ("bank_view", s["bank_view"]),
    ):
        for c in items:
            claims.append({"block": name, **c})
    return {
        "title": body["title"],
        "summary": body["summary"],
        "impact": body.get("impact"),
        "horizon": body.get("horizon"),
        "claims": claims,
    }


def _feed(session: Session, user: AppUser) -> list[dict]:
    rows = session.execute(
        select(Insight, Relevance.score)
        .join(
            Relevance,
            (Relevance.event_id == Insight.event_id)
            & (Relevance.company_id == Insight.company_id)
            & (Relevance.role == Insight.role),
        )
        .where(Insight.company_id == user.company_id, Insight.role == user.role, Insight.status == "ok")
        .order_by(Relevance.score.desc())
        .limit(5)
    ).all()
    return [
        {
            "insight_id": i.id,
            "event_id": i.event_id,
            "title": i.body["title"],
            "summary": i.body["summary"],
            "polarity": i.body["polarity"],
        }
        for i, _ in rows
    ]


def _features(company: Company) -> dict:
    feats = {}
    for f in company.profile.get("factors", []):
        for dr in f["drivers"]:
            r = feature_ref(dr, company.profile)
            if r:
                feats[r[0]] = r[1]
    feats["MAINOKVED_CODE"] = f"основной вид деятельности — «{company.profile['industry'].get('name')}»"
    feats["REGION"] = f"регион обслуживания — {company.profile['geo'].get('region')}"
    return feats


def build_context(session: Session, user: AppUser, message: str, body: dict | None, history: list) -> dict:
    company = session.get(Company, user.company_id)
    bm, mats = _material_index(session, user)
    query = message + (" " + body["title"] if body else "")
    news, alfa = [], []
    for mid, _ in bm.search(query, top_k=20):
        m = mats[mid]
        item = {"id": mid, "title": m.title, "excerpt": best_sentence(m.text, query, 60)}
        if m.kind == "alfa" and len(alfa) < MAX_SNIPPETS:
            alfa.append({**item, "report": m.report, "page": m.page})
        elif m.kind != "alfa" and len(news) < MAX_SNIPPETS:
            news.append({**item, "source": m.source})
    names = {k: v.get("name", k) for k, v in load_factor_rules().get("catalog", {}).items()}
    return {
        "question": message,
        "history": (history or [])[-10:],
        "insight": _card(body) if body else None,
        "feed": _feed(session, user),
        "news": news,
        "alfa": alfa,
        "company": {
            "industry": company.profile["industry"].get("name"),
            "region": company.profile["geo"].get("region"),
            "factors": [
                {"factor": f["factor"], "name": names.get(f["factor"], f["factor"]), "weight": f["weight"]}
                for f in company.profile.get("factors", [])[:8]
            ],
            "summary": company.summary,
        },
        "features": _features(company),
    }


def allowed_sources(context: dict) -> dict[str, set]:
    allowed = {
        "material": {n["id"] for n in context["news"]},
        "alfa": {a["id"] for a in context["alfa"]},
        "feature": set(context["features"]),
        "claim": set(),
        "event": {f["event_id"] for f in context["feed"]},
    }
    if context["insight"]:
        for c in context["insight"]["claims"]:
            allowed["claim"].add(c["id"])
            for s in c.get("sources", []):
                allowed.setdefault(s["type"], set()).add(s["id"])
    return allowed


def _validator(context: dict):
    allowed = allowed_sources(context)

    def check(result):
        if not isinstance(result, dict) or not str(result.get("text") or "").strip():
            raise InvalidOutput("нет текста ответа")
        claims = []
        for i, raw in enumerate(result.get("claims") or [], start=1):
            if not isinstance(raw, dict):
                continue
            srcs = [s for s in raw.get("sources") or [] if s.get("id") in allowed.get(s.get("type"), set())]
            try:
                claims.append(Claim.model_validate({**raw, "id": f"a{i}", "sources": srcs}).model_dump())
            except Exception:
                continue
        kind = result.get("type") if result.get("type") in ("answer", "refusal", "help") else "answer"
        return {"type": kind, "text": re.sub(r"[ \t]+", " ", str(result["text"])).strip(), "claims": claims}

    return check


def answer(
    session: Session,
    user: AppUser,
    message: str,
    insight_id: str | None = None,
    history: list[dict] | None = None,
) -> dict:
    body = None
    if insight_id:
        ins = session.get(Insight, insight_id)
        if ins and ins.company_id == user.company_id:
            body = ins.body
    context = build_context(session, user, message, body, history or [])
    result, meta = get_llm().complete(
        "chat",
        CHAT_SYSTEM,
        json.dumps(context, ensure_ascii=False, default=str),
        validate=_validator(context),
    )
    return {**result, "mode": meta.mode, "model": meta.model}
