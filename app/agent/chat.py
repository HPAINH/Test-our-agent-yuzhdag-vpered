import json
import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import load_factor_rules
from app.db.models import AppUser, Company, Insight, Material, Relevance
from app.llm.client import get_llm
from app.processing.text import BM25, sentences, stems, tokens

INTENTS: list[tuple[str, tuple[str, ...]]] = [
    ("sources", ("первоисточ", "источник", "откуда", "ссылк")),
    ("bank", ("говорит банк", "позиция банк", "позиции банк", "мнение банк", "аналитик", "цми", "альфа")),
    ("compare", ("сравн", "прошл", "похож", "ранее", "связанн событ")),
    ("actions", ("что делать", "делать", "действ", "рекоменд", "совет", "предприн", "шаг", "меры", "мер ")),
    ("risks", ("риск", "угроз", "опасн", "грозит", "негатив", "минус", "чем плох")),
    ("opportunities", ("возможност", "выгод", "плюс", "позитив", "заработ", "чем хорош")),
    ("why", ("почему", "зачем", "важн", "касает", "при чем", "причем", "как связан")),
    ("severity", ("серьезн", "серьёзн", "критичн", "насколько", "значим", "срочн", "уверен")),
    ("what", ("что произошло", "что случилось", "суть", "кратко", "о чем", "о чём", "перескаж")),
]
TOPICS: list[tuple[tuple[str, ...], tuple[str, ...], str]] = [
    (
        ("марж", "рентаб", "прибыл"),
        (
            "cmd_metals",
            "cmd_coal",
            "cmd_agro",
            "cmd_oil",
            "cmd_construction",
            "consumer_demand",
            "inflation",
            "logistics",
            "labor",
            "regulation",
        ),
        "маржу",
    ),
    (
        ("выручк", "продаж", "спрос", "доход"),
        (
            "consumer_demand",
            "real_estate",
            "cmd_metals",
            "cmd_agro",
            "cmd_construction",
            "sanctions_trade",
            "fx_cny",
            "fx_usd",
        ),
        "выручку",
    ),
    (
        ("долг", "кредит", "ставк", "процент", "рефинанс", "заём", "заем", "займ"),
        ("key_rate", "bonds"),
        "долг",
    ),
    (("курс", "валют", "юан", "доллар", "евро"), ("fx_cny", "fx_usd", "fx_eur"), "валютные расчёты"),
    (
        ("издерж", "себестоим", "затрат", "расход"),
        (
            "inflation",
            "cmd_metals",
            "cmd_coal",
            "cmd_oil",
            "cmd_agro",
            "cmd_construction",
            "logistics",
            "labor",
            "regulation",
        ),
        "издержки",
    ),
    (("налог", "пошлин", "регулир", "закон"), ("regulation", "sanctions_trade"), "налоги и регулирование"),
    (("персонал", "зарплат", "кадр", "сотрудник", "фот"), ("labor",), "персонал"),
    (("экспорт", "импорт", "санкц", "вэд", "китай"), ("sanctions_trade", "fx_cny", "logistics"), "ВЭД"),
    (("логист", "перевоз", "ржд", "доставк"), ("logistics",), "логистику"),
    (("ликвидн", "депозит", "свободн"), ("key_rate",), "ликвидность"),
]
GREETING = ("привет", "здравств", "добрый", "доброе", "hello", "hi")
HELP = ("что умеешь", "помоги", "помощь", "как пользоваться")
SECTION_LABEL = {
    "what_happened": "Что произошло",
    "why_it_matters": "Почему важно",
    "risks": "Риск",
    "opportunities": "Возможность",
    "watch_factors": "Отслеживать",
    "actions": "Действие",
    "bank_view": "Позиция банка",
}
CHAT_SYSTEM = """Ты — ассистент аналитики Альфа-Банка для руководителя компании в интернет-банке.
Отвечай на вопрос пользователя только по контексту: инсайт по событию, найденные фрагменты новостей и аналитики банка,
профиль компании. Пиши по-русски, коротко (2–5 предложений), по делу и с привязкой к компании.
Каждое утверждение — объект claims[] с kind fact|bank|ai и sources [{"type": "material"|"alfa"|"claim"|"feature", "id": ...}],
id бери только из контекста. Не приписывай банку то, чего нет в его аналитике. Если данных не хватает — так и скажи.
Если вопрос не связан с бизнесом компании, новостями или аналитикой — вежливо откажись.
В контексте есть draft — ответ, собранный правилами; улучши его формулировки, но не добавляй фактов вне контекста.
Верни JSON: {"text": "...", "claims": [...]}"""


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def detect(message: str) -> tuple[list[str], list[str], list[str]]:
    low = _norm(message) + " "
    intents = [name for name, keys in INTENTS if any(_norm(k) in low for k in keys)]
    factors: list[str] = []
    labels: list[str] = []
    for keys, fs, label in TOPICS:
        if any(k in low for k in keys):
            factors.extend(f for f in fs if f not in factors)
            labels.append(label)
    toks = [_norm(t) for t in tokens(message)]
    for code, spec in load_factor_rules().get("catalog", {}).items():
        for kw in spec.get("keywords", []):
            parts = kw.split()
            if all(any(t.startswith(p) for t in toks) for p in parts) and code not in factors:
                factors.append(code)
                labels.append(f"фактор «{spec.get('name', code)}»")
    return intents, factors, labels


def is_greeting(message: str) -> bool:
    low = _norm(message)
    return len(tokens(message)) <= 4 and any(low.startswith(g) or f" {g}" in low for g in GREETING)


def _claim_list(body: dict) -> list[tuple[str, dict]]:
    s = body["sections"]
    cons = s["consequences"]
    out = [("what_happened", c) for c in s["what_happened"]]
    out += [("why_it_matters", c) for c in s["why_it_matters"]]
    out += [("risks", c) for c in cons["risks"]]
    out += [("opportunities", c) for c in cons["opportunities"]]
    out += [("watch_factors", c) for c in cons["watch_factors"]]
    out += [("actions", c) for c in s["actions"]]
    out += [("bank_view", c) for c in s["bank_view"]]
    return out


def _sentence_join(parts: list[str], limit: int = 5) -> str:
    seen, out = set(), []
    for p in parts:
        p = p.strip()
        if p and p not in seen:
            seen.add(p)
            out.append(p if p.endswith((".", "!", "?", "»", ":")) else p + ".")
        if len(out) >= limit:
            break
    return " ".join(out)


POLARITY = {"risk": "риск", "opportunity": "возможность", "mixed": "двоякое влияние"}
HORIZON = {"0-1m": "до 1 месяца", "0-3m": "до 3 месяцев", "1-3m": "1–3 месяца", "3-12m": "3–12 месяцев"}


def _factor_names() -> dict:
    return {k: v["name"] for k, v in load_factor_rules().get("catalog", {}).items()}


def _company_factor_claim(company: Company, factors: list[str]) -> dict | None:
    cf = {f["factor"]: f for f in company.profile.get("factors", [])}
    hit = next((cf[f] for f in factors if f in cf), None)
    if not hit:
        return None
    from app.agent.analyst import feature_ref

    drivers = [d for d in hit["drivers"] if d != "base"]
    phrases = [r[1] for r in (feature_ref(d, company.profile) for d in drivers) if r]
    reason = (
        "; ".join(dict.fromkeys(phrases[:3]))
        if phrases
        else "базовая чувствительность любой крупной компании"
    )
    return {
        "id": "p1",
        "kind": "ai",
        "text": f"Для вашей компании фактор «{_factor_names().get(hit['factor'], hit['factor'])}» имеет вес "
        f"{hit['weight']:.2f} из 1 ({POLARITY.get(hit['polarity'], hit['polarity'])}); основания: {reason}",
        "sources": [{"type": "feature", "id": d.split("=")[0]} for d in drivers[:3]]
        or [{"type": "feature", "id": "MAINOKVED_CODE"}],
    }


def card_answer(session: Session, body: dict, message: str, company: Company) -> dict | None:
    intents, factors, labels = detect(message)
    items = _claim_list(body)
    s = body["sections"]
    cons = s["consequences"]
    picked: list[tuple[str, dict]] = []
    lead: list[str] = []

    def take(section: str, limit: int = 3):
        for sec, c in items:
            if sec == section and (sec, c) not in picked and limit > 0:
                picked.append((sec, c))
                limit -= 1

    for intent in intents:
        if intent == "what":
            take("what_happened")
        elif intent == "why":
            take("why_it_matters")
        elif intent == "risks":
            if cons["risks"]:
                take("risks")
            else:
                lead.append("Явных рисков по этому событию для компании не выявлено")
        elif intent == "opportunities":
            if cons["opportunities"]:
                take("opportunities")
            else:
                lead.append("Явных возможностей по этому событию не выявлено")
        elif intent == "actions":
            take("actions")
        elif intent == "bank":
            if s["bank_view"]:
                take("bank_view", 2)
            else:
                lead.append("Аналитики Альфа-Банка именно по этой теме в базе нет")
        elif intent == "compare":
            related = [
                c
                for sec, c in items
                if sec == "watch_factors" and any(x["type"] == "event" for x in c["sources"])
            ]
            if related:
                picked.extend(("watch_factors", c) for c in related)
            else:
                lead.append("Связанных событий за последние 30 дней не найдено")
        elif intent == "severity":
            rel = body.get("relevance", {})
            lead.append(
                f"Значимость события для компании — {round((rel.get('score') or 0) * 100)}%, характер — "
                f"{POLARITY.get(body['polarity'], 'двоякое влияние')}, горизонт — {HORIZON.get(body['horizon'], body['horizon'])}, "
                f"уверенность вывода — {round(body['confidence'] * 100)}%"
            )
            take("risks", 1)
            take("opportunities", 1)
        elif intent == "sources":
            mats = session.scalars(
                select(Material).where(Material.id.in_(s["evidence"]["materials"] or []))
            ).all()
            for i, m in enumerate(mats, 1):
                picked.append(
                    (
                        "sources",
                        {
                            "id": f"s{i}",
                            "kind": "fact",
                            "text": f"{m.source}: «{m.title}»",
                            "sources": [{"type": "material", "id": m.id, "label": m.source}],
                        },
                    )
                )

    if factors:
        topical = [
            (sec, c)
            for sec, c in items
            if sec in ("risks", "opportunities", "watch_factors", "actions") and c.get("factor") in factors
        ]
        if topical:
            picked.extend(x for x in topical if x not in picked)
        else:
            what = " и ".join(dict.fromkeys(labels)) if labels else "этот фактор"
            lead.append(
                f"Это событие напрямую не влияет на {what}. Что известно по теме из других материалов"
            )
            more = corpus_answer(session, None, company, message, factors_override=factors)
            if more:
                picked.extend(("corpus", c) for c in more["claims"])
            else:
                extra = _company_factor_claim(company, factors)
                if extra:
                    picked.append(("profile", extra))

    if not picked and not lead:
        q = set(stems(message))
        scored = []
        for sec, c in items:
            overlap = len(q & set(stems(c["text"])))
            if overlap:
                scored.append((overlap, sec, c))
        scored.sort(key=lambda x: -x[0])
        picked = [(sec, c) for _, sec, c in scored[:3]]
    if not picked and not lead:
        return None

    parts = list(lead)
    for sec, c in picked[:6]:
        label = SECTION_LABEL.get(sec)
        if sec == "corpus" and c["kind"] == "bank":
            label = "Позиция Альфа-Банка"
        parts.append(
            f"{label}: {c['text']}" if label and sec not in ("what_happened", "sources") else c["text"]
        )
    return {
        "text": _sentence_join(parts, 6),
        "claims": [c for _, c in picked[:6]],
        "intents": intents,
        "factors": factors,
    }


def corpus_answer(
    session: Session,
    user: AppUser | None,
    company: Company,
    message: str,
    factors_override: list[str] | None = None,
) -> dict | None:
    intents, factors, labels = detect(message)
    factors = factors_override or factors
    catalog = load_factor_rules().get("catalog", {})
    visible = (
        Material.submitted_by.is_(None)
        if user is None
        else or_(Material.submitted_by.is_(None), Material.submitted_by == user.login)
    )
    news = session.scalars(select(Material).where(Material.kind == "news", visible)).all()
    alfa = session.scalars(select(Material).where(Material.kind == "alfa")).all()
    bank_only = "bank" in intents
    query = message
    if bank_only and not factors:
        top = [f["factor"] for f in company.profile.get("factors", []) if f["factor"] != "region"][:3]
        factors = top
        query = message + " " + " ".join(catalog.get(f, {}).get("name", "") for f in top)
    units: dict[str, tuple[Material, str]] = {}
    for m in alfa if bank_only else [*news, *alfa]:
        for i, sent in enumerate(sentences(m.text)):
            units[f"{m.id}#{i}"] = (m, sent)
    if factors:
        kws = [k for f in factors for k in catalog.get(f, {}).get("keywords", [])]
        keep = {}
        for uid, (m, sent) in units.items():
            toks = [_norm(t) for t in tokens(sent + " " + m.title)]
            if any(all(any(t.startswith(p) for t in toks) for p in kw.split()) for kw in kws):
                keep[uid] = (m, sent)
        units = keep or units
    bm = BM25({uid: f"{m.title} {sent}" for uid, (m, sent) in units.items()})
    hits = bm.search(query, top_k=12)
    threshold = 0.0 if factors else 2.0
    claims, seen_m = [], set()
    n_news = n_bank = 0
    max_bank = 2 if bank_only else 1
    for uid, score in hits:
        m, sent = units[uid]
        if score < threshold or m.id in seen_m:
            continue
        if m.kind == "news" and n_news < 2:
            n_news += 1
            claims.append(
                {
                    "id": f"n{n_news}",
                    "kind": "fact",
                    "text": sent,
                    "sources": [{"type": "material", "id": m.id, "quote": sent, "label": m.source}],
                }
            )
            seen_m.add(m.id)
        elif m.kind == "alfa" and n_bank < max_bank:
            n_bank += 1
            claims.append(
                {
                    "id": f"b{n_bank}",
                    "kind": "bank",
                    "text": sent,
                    "sources": [
                        {"type": "alfa", "id": m.id, "quote": sent, "label": f"{m.report}, с. {m.page}"}
                    ],
                }
            )
            seen_m.add(m.id)
    extra = _company_factor_claim(company, factors) if factors else None
    if extra:
        claims.append(extra)
    if not claims:
        return None
    facts = [c["text"] for c in claims if c["kind"] == "fact"]
    banks = [c["text"] for c in claims if c["kind"] == "bank"]
    ais = [c["text"] for c in claims if c["kind"] in ("ai", "hypothesis")]
    parts = []
    if facts:
        parts.append("По новостям: " + _sentence_join(facts, 2))
    if banks:
        parts.append("Позиция Альфа-Банка: " + _sentence_join(banks, 2))
    if ais:
        parts.append(_sentence_join(ais, 1))
    return {"text": " ".join(parts), "claims": claims, "intents": intents, "factors": factors}


def feed_answer(session: Session, user: AppUser, message: str) -> dict | None:
    intents, _, _ = detect(message)
    wanted = [i for i in intents if i in ("risks", "opportunities", "actions", "severity", "what")]
    if not wanted:
        return None
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
        .limit(3)
    ).all()
    if not rows:
        return None
    parts, claims = ["Главное в вашей ленте сейчас:"], []
    key = {"risks": "risks", "opportunities": "opportunities"}
    for ins, _ in rows:
        b = ins.body
        sec = next((key[i] for i in wanted if i in key), None)
        pool = (
            b["sections"]["consequences"][sec]
            if sec
            else (b["sections"]["actions"] if "actions" in wanted else [])
        )
        c = pool[0] if pool else None
        parts.append(f"«{b['title']}»" + (f" — {c['text'][0].lower() + c['text'][1:]}" if c else ""))
        if c:
            claims.append(c)
    return {"text": _sentence_join(parts, 4), "claims": claims, "intents": intents, "factors": []}


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

    if is_greeting(message) or any(h in _norm(message) for h in HELP):
        return {
            "text": "Здравствуйте! Я отвечаю по этому событию, новостям и аналитике Альфа-Банка с учётом профиля "
            "вашей компании. Спросите, например: «Какие риски для нас?», «Как это скажется на марже?», "
            "«Что делать?» или «Что говорит банк?».",
            "claims": [],
            "mode": "help",
        }

    draft = card_answer(session, body, message, company) if body else None
    if draft is None and not body:
        draft = feed_answer(session, user, message)
    if draft is None:
        draft = corpus_answer(session, user, company, message)
    about_event = any(w in _norm(message) for w in ("это", "событ", "новост", "нас", "наш", "компан", "мы "))
    if draft is None and body and about_event:
        draft = {
            "text": f"Коротко о событии: {body['summary']} Уточните вопрос — например, про риски, маржу, долг "
            f"или действия.",
            "claims": [],
            "intents": [],
            "factors": [],
        }
    if draft is None and not body and set(detect(message)[0]) & {"sources", "why", "compare"}:
        return {
            "text": "Этот вопрос относится к конкретному событию: откройте его карточку в ленте и задайте вопрос "
            "там — я покажу первоисточники, связь с компанией и связанные события.",
            "claims": [],
            "mode": "help",
        }
    if draft is None:
        return {
            "text": "Я отвечаю на вопросы о новостях, аналитике банка и их влиянии на вашу компанию. "
            "Попробуйте спросить, например, «как ставка влияет на наш долг?».",
            "claims": [],
            "mode": "refusal",
        }

    context = {
        "question": message,
        "history": (history or [])[-10:],
        "draft": draft,
        "insight": body
        and {
            "title": body["title"],
            "summary": body["summary"],
            "sections": body["sections"],
            "relevance": body.get("relevance", {}).get("score"),
            "horizon": body["horizon"],
        },
        "company": {
            "industry": company.profile["industry"],
            "factors": company.profile.get("factors", [])[:6],
        },
    }
    fallback = {"text": draft["text"], "claims": draft["claims"]}
    result, meta = get_llm().complete(
        "chat", CHAT_SYSTEM, json.dumps(context, ensure_ascii=False, default=str), mock=lambda: fallback
    )
    if not isinstance(result, dict) or not result.get("text"):
        result = fallback
    allowed = {s["id"] for c in draft["claims"] for s in c.get("sources", [])}
    if body:
        allowed |= set(body["sections"]["evidence"]["materials"]) | set(body["sections"]["evidence"]["alfa"])
        allowed |= {c["id"] for _, c in _claim_list(body)}
    clean = []
    for c in result.get("claims", []):
        srcs = [x for x in c.get("sources", []) if x.get("type") == "feature" or x.get("id") in allowed]
        if srcs:
            clean.append({**c, "sources": srcs})
    text = re.sub(r"[ \t]+", " ", result["text"]).strip()
    return {
        "text": text,
        "claims": clean or draft["claims"],
        "mode": "fallback" if meta.fallback else meta.mode,
        "intents": draft.get("intents", []),
    }
