import json
from datetime import datetime

from app.agent.context import RunContext
from app.config import load_factor_rules, load_yaml
from app.llm.client import InvalidOutput
from app.schemas import Evidence, Impact, InsightBody, Sections, Verification


def _fmt_bn(v: float | None) -> str:
    return f"{v / 1e9:.1f}".replace(".", ",") if v else "н/д"


def feature_ref(driver: str, profile: dict) -> tuple[str, str] | None:
    phrases = load_yaml("interpretation.yaml")["feature_phrases"]
    if driver == "base" or "=" not in driver:
        return None
    key, value = driver.split("=", 1)
    if key.startswith("CL_PRODUCT") or key in ("FACTORING_MARK",):
        return key, phrases.get(key, key)
    if key in ("FTS_FLAG", "FTS_COUNTRY_NAME"):
        return "FTS_FLAG", phrases["FTS_FLAG"].format(
            country=profile["foreign_trade"].get("country") or "н/д"
        )
    if key == "WORKERS":
        return "AMOUNT_WORKERS_FNS_GRADATION", phrases["WORKERS"].format(workers=value)
    if key == "MAINOKVED_CODE":
        return "MAINOKVED_CODE", phrases["MAINOKVED_CODE"].format(okved_name=profile["industry"].get("name"))
    if key == "counterparty":
        cp = next((c for c in profile.get("counterparties", []) if c["company_id"] == value), None)
        if not cp:
            return None
        return f"PAYMENTS:{value}", phrases["COUNTERPARTY"].format(
            cp_dir="плательщик" if cp["direction"] == "in" else "получатель платежей",
            cp_flow="входящих" if cp["direction"] == "in" else "исходящих",
            cp_share=round(cp["share"] * 100),
            cp_okved_name=cp.get("okved_name") or "нет в выборке",
        )
    if key == "USER_INPUT":
        return "USER_INPUT", phrases["USER_INPUT"]
    return key, f"{key}={value}"


def assess_impact(
    ctx: RunContext,
    main: str | None,
    cfactors: dict,
    modality: str,
    has_bank: bool,
    estimate: str | None = None,
) -> Impact:
    interp = load_yaml("interpretation.yaml")
    scale = interp["impact_scale"]
    rel = ctx.relevance or {}
    score = round(100 * float(rel.get("score") or 0))
    basis = [f"значимость события для компании {score} из 100"]
    weight = cfactors.get(main, {}).get("weight", 0) if main else 0
    if main:
        name = load_factor_rules().get("catalog", {}).get(main, {}).get("name", main)
        basis.append(f"вес фактора «{name}» для компании {weight:.2f} из 1")
    direct = bool(rel.get("industry_match") or rel.get("region_match"))
    if direct:
        basis.append(rel.get("industry_match") or rel.get("region_match"))
    basis.append("событие произошло" if modality == "fact" else "событие ожидается или обсуждается")
    if has_bank:
        basis.append("тема есть в аналитике банка")
    level = "low"
    for name in ("high", "medium"):
        if score >= scale[name]["min_score"]:
            level = name
            break
    if level == "high" and modality != "fact":
        level = "medium"
    if level == "low" and weight >= 0.7 and modality == "fact":
        level = "medium"
    return Impact(
        level=level, score=score, basis=basis, estimate=(estimate or None) if level != "low" else None
    )


ANALYST_SYSTEM = """Ты — аналитик Альфа-Банка для руководителей крупного бизнеса. Подготовь персональную интерпретацию
события для конкретной компании и роли. Это не пересказ новости: объясни, что событие значит для бизнеса именно
этой компании, через её признаки, связи и другие факторы.
Структура sections:
- what_happened: 2–4 факта, kind=fact, sources=[{"type":"material","id":<id из materials>,"quote":<дословная цитата>}].
- why_it_matters: 2–3 вывода kind=ai; каждый ссылается на признаки компании (type=feature, id из features),
  в сумме минимум 2 разных признака; укажи "factor" — код фактора.
- consequences.risks / opportunities / watch_factors: kind=hypothesis — возможные последствия и сценарии;
  объясни логику и на чём основано (type=claim на id из why_it_matters, type=material, type=event из related_events);
  учти минимум 2 фактора, в том числе из связанных событий; вероятности не выдумывай.
- actions: 1–3 конкретных шага для этой роли, kind=ai, со ссылкой на claim; продукт банка — только если уместен и есть
  в products ("bank_product": code, "bank_product_title": title).
- bank_view: только цитаты из аналитики банка, kind=bank, sources=[{"type":"alfa","id":<id из alfa>,"quote":...}].
  Если в alfa нет материала по теме — оставь пустым и добавь в missing "Аналитика банка по теме отсутствует".
  Если твой вывод расходится с позицией банка — прямо напиши в выводе, что это оценка модели, а не позиция банка.
Каждое утверждение: {"id": "c1"…, "kind", "text", "sources": [...], "factor"?, "bank_product"?, "bank_product_title"?}.
Числа и даты — только из источников. Ничего не выдумывай; если данных мало — перечисли недостающее в missing.
Верни JSON: {"title": вывод для компании (не заголовок новости), "summary": 1–2 предложения с главным выводом и
действием, "sections": {...}, "factors": [коды], "polarity": "risk"|"opportunity"|"mixed",
"horizon": "0-1m"|"0-3m"|"1-3m"|"3-12m", "confidence": 0..1,
"impact_note": количественная оценка влияния, если её можно получить из данных (формула чувствительности или
расчёт по числам из источников), иначе пустая строка, "missing": [...]}"""


def llm_context(ctx: RunContext, issues: list[dict] | None) -> dict:
    roles = load_yaml("roles.yaml")["roles"]
    feats = {}
    for f in ctx.profile.get("factors", []):
        for dr in f["drivers"]:
            r = feature_ref(dr, ctx.profile)
            if r:
                feats[r[0]] = r[1]
    rev = ctx.profile["scale"].get("revenue_rub")
    feats["REVENUE_COMPANY"] = f"выручка за 2025 год {_fmt_bn(rev)} млрд ₽" if rev else "выручка: нет данных"
    feats["REGION"] = f"регион обслуживания — {ctx.profile['geo'].get('region')}"
    feats["MAINOKVED_CODE"] = f"основной вид деятельности — «{ctx.profile['industry'].get('name')}»"
    if (ctx.profile.get("group") or {}).get("member_count", 0) > 1:
        g = ctx.profile["group"]
        feats["GROUP_ID"] = f"входит в группу {g['group_id']} из {g['member_count']} компаний"
    return {
        "role": {"code": ctx.role, **roles.get(ctx.role, {})},
        "company": {
            "industry": ctx.profile["industry"],
            "scale": ctx.profile["scale"],
            "geo": ctx.profile["geo"],
            "products": ctx.profile["products"],
            "foreign_trade": ctx.profile["foreign_trade"],
            "group": ctx.profile.get("group"),
            "factors": ctx.profile.get("factors", [])[:8],
            "summary": ctx.company.summary,
        },
        "features": feats,
        "relevance": {k: ctx.relevance.get(k) for k in ("contributions", "industry_match", "region_match")},
        "event": {
            "id": ctx.event.id,
            "title": ctx.event.title,
            "classification": ctx.event.classification,
            "facts": ctx.event.facts,
        },
        "materials": {
            mid: {"source": m.source, "title": m.title, "text": m.text[:2500]}
            for mid, m in list(ctx.materials.items())[:6]
        },
        "alfa": {
            cid: {"report": m.report, "page": m.page, "text": m.text[:1500]} for cid, m in ctx.alfa.items()
        },
        "related_events": ctx.related[:4],
        "counterparties": ctx.counterparties[:5],
        "group": ctx.group,
        "products": ctx.products,
        "previous_issues": issues or [],
    }


def _validator(ctx: RunContext, context: dict):
    feature_ids = set(context["features"])
    material_ids = set(ctx.materials)
    alfa_ids = set(ctx.alfa)
    event_ids = {ctx.event.id} | {r.get("event_id") for r in ctx.related}
    products = {p["code"]: p["title"] for p in ctx.products}
    catalog = set(load_factor_rules().get("catalog", {}))

    def check(result):
        if not isinstance(result, dict) or not isinstance(result.get("sections"), dict):
            raise InvalidOutput("нет объекта sections")
        sec = result["sections"]
        cons = sec.get("consequences") or {}
        for key in ("risks", "opportunities", "watch_factors"):
            for c in cons.get(key, []) or []:
                if c.get("kind") == "ai":
                    c["kind"] = "hypothesis"
        sec["consequences"] = cons
        sec["evidence"] = {"features": [], "materials": [], "alfa": []}
        try:
            sections = Sections.model_validate(sec)
        except Exception as exc:
            raise InvalidOutput(f"разделы не по схеме: {exc}") from exc
        claims = [
            *sections.what_happened,
            *sections.why_it_matters,
            *sections.consequences.risks,
            *sections.consequences.opportunities,
            *sections.consequences.watch_factors,
            *sections.actions,
            *sections.bank_view,
        ]
        ids = [c.id for c in claims]
        problems = []
        if len(ids) != len(set(ids)):
            problems.append("повторяются id утверждений")
        for c in claims:
            for src in c.sources:
                ok = {
                    "material": src.id in material_ids,
                    "alfa": src.id in alfa_ids,
                    "feature": src.id in feature_ids,
                    "claim": src.id in ids,
                    "event": src.id in event_ids,
                }[src.type]
                if not ok:
                    problems.append(f"{c.id}: источник {src.type}:{src.id} отсутствует в контексте")
            if c.bank_product and c.bank_product not in products:
                problems.append(f"{c.id}: продукта {c.bank_product} нет в products")
            elif c.bank_product:
                c.bank_product_title = products[c.bank_product]
        if any(c.kind != "bank" for c in sections.bank_view):
            problems.append("в bank_view есть утверждения не из аналитики банка")
        if not sections.what_happened:
            problems.append("пустой блок what_happened")
        if problems:
            raise InvalidOutput("; ".join(problems[:8]))
        used = {s.id for c in claims for s in c.sources}
        sections.evidence = Evidence(
            features=sorted(used & feature_ids),
            materials=sorted(used & material_ids),
            alfa=sorted(used & alfa_ids),
        )
        factors = [f for f in result.get("factors", []) if f in catalog]
        return {
            "title": str(result.get("title") or ctx.event.title)[:300],
            "summary": str(result.get("summary") or "")[:1200],
            "sections": sections,
            "factors": factors,
            "polarity": result.get("polarity")
            if result.get("polarity") in ("risk", "opportunity", "mixed")
            else "mixed",
            "horizon": result.get("horizon")
            if result.get("horizon") in ("0-1m", "0-3m", "1-3m", "3-12m")
            else "1-3m",
            "confidence": min(0.95, max(0.0, float(result.get("confidence") or 0.5))),
            "impact_note": str(result.get("impact_note") or "").strip(),
            "missing": [str(m) for m in result.get("missing", []) if m][:6],
        }

    return check


def llm_analyst(ctx: RunContext, issues: list[dict] | None = None) -> tuple[InsightBody, object]:
    from app.llm.client import get_llm

    context = llm_context(ctx, issues)
    out, meta = get_llm().complete(
        "analyst",
        ANALYST_SYSTEM,
        json.dumps(context, ensure_ascii=False, default=str),
        trace_id=ctx.trace_id,
        use_cache=not issues,
        validate=_validator(ctx, context),
    )
    cfactors = {f["factor"]: f for f in ctx.profile.get("factors", [])}
    contributions = ctx.relevance.get("contributions", [])
    main = out["factors"][0] if out["factors"] else (contributions[0]["factor"] if contributions else None)
    modality = (ctx.event.classification or {}).get("modality", "fact")
    body = InsightBody(
        insight_id="pending",
        event_id=ctx.event.id,
        company_id=ctx.company.id,
        role=ctx.role,
        title=out["title"],
        summary=out["summary"],
        sections=out["sections"],
        factors=out["factors"],
        polarity=out["polarity"],
        horizon=out["horizon"],
        impact=assess_impact(
            ctx, main, cfactors, modality, bool(out["sections"].bank_view), out["impact_note"]
        ),
        confidence=out["confidence"],
        verification=Verification(passed=False, iterations=0),
        status="ok",
        missing=out["missing"],
        trace_id=ctx.trace_id,
        generated_at=datetime.utcnow().isoformat(timespec="seconds"),
        models={},
    )
    return body, meta
