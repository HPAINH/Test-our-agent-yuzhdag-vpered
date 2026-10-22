import json
from datetime import datetime

from app.agent.context import RunContext
from app.config import load_factor_rules, load_yaml
from app.schemas import (
    Claim,
    Consequences,
    Evidence,
    Impact,
    InsightBody,
    Sections,
    SourceRef,
    Verification,
)

PRODUCES = {
    "metallurgy": "cmd_metals",
    "metals_trade": "cmd_metals",
    "coal": "cmd_coal",
    "oil_gas": "cmd_oil",
    "agro": "cmd_agro",
    "building_materials": "cmd_construction",
}
BANK_MIN_OVERLAP = 3


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


def is_producer(factor: str, drivers: list[str], profile: dict) -> bool:
    return PRODUCES.get(profile["industry"].get("sector")) == factor and any(
        d.startswith("MAINOKVED") for d in drivers
    )


def classify_effect(
    factor: str, polarity: str, direction: str, drivers: list[str], profile: dict
) -> list[str]:
    if factor.startswith("cmd_"):
        if direction == "flat":
            return ["watch"]
        if is_producer(factor, drivers, profile):
            return ["opportunity" if direction == "up" else "risk_producer"]
        return ["risk" if direction == "up" else "opportunity_buyer"]
    if polarity in ("risk", "opportunity"):
        if factor == "key_rate" and direction == "down":
            return ["opportunity" if polarity == "risk" else "risk"]
        return [polarity]
    if factor == "key_rate":
        flags = profile.get("flags", {})
        out = []
        if flags.get("CL_PRODUCT_LOA_MARK") or any(d.startswith("base") for d in drivers):
            out.append("risk" if direction in ("up", "flat") else "opportunity")
        if flags.get("CL_PRODUCT_DEP_MARK"):
            out.append("opportunity" if direction in ("up", "flat") else "risk")
        return out or ["watch"]
    if factor in ("consumer_demand", "real_estate"):
        return ["watch"] if direction == "flat" else ["opportunity" if direction == "up" else "risk"]
    if factor in ("regulation", "sanctions_trade", "logistics", "labor", "inflation", "bonds"):
        return (
            ["watch"]
            if direction == "flat" and factor not in ("regulation",)
            else ["risk" if direction in ("up", "flat") else "opportunity"]
        )
    return ["watch"]


def bucket(effect: str) -> str:
    return (
        "risk"
        if effect.startswith("risk")
        else "opportunity"
        if effect.startswith("opportunity")
        else "watch"
    )


def merge_refs(refs: list[tuple[str, str]], profile: dict) -> list[tuple[str, str]]:
    out, cp_groups = [], {}
    for fid, phrase in refs:
        if fid.startswith("PAYMENTS:"):
            cp = next(
                (c for c in profile.get("counterparties", []) if c["company_id"] == fid.split(":", 1)[1]),
                None,
            )
            if cp:
                key = (cp["direction"], cp.get("okved_name"))
                cp_groups.setdefault(key, []).append((fid, cp))
                continue
        out.append((fid, phrase))
    for (direction, okved_name), items in cp_groups.items():
        share = round(sum(c["share"] for _, c in items) * 100)
        who = (
            ("плательщики" if direction == "in" else "получатели платежей")
            if len(items) > 1
            else ("плательщик" if direction == "in" else "получатель платежей")
        )
        verb = "работают" if len(items) > 1 else "работает"
        flow = "входящих" if direction == "in" else "исходящих"
        out.append(
            (
                items[0][0],
                f"{'крупные' if len(items) > 1 else 'крупный'} {who} ({share}% {flow} платежей) "
                f"{verb} в отрасли «{okved_name or 'нет в выборке'}»",
            )
        )
    return out


def assess_impact(ctx: RunContext, main: str | None, cfactors: dict, modality: str, has_bank: bool) -> Impact:
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
    estimate = interp.get("sensitivity", {}).get(main) if main and level != "low" else None
    return Impact(level=level, score=score, basis=basis, estimate=estimate)


class Draft:
    def __init__(self):
        self.n = 0
        self.claims: dict[str, Claim] = {}

    def claim(self, kind: str, text: str, sources: list[SourceRef], **kw) -> Claim:
        self.n += 1
        c = Claim(id=f"c{self.n}", kind=kind, text=text, sources=sources, **kw)
        self.claims[c.id] = c
        return c


def template_analyst(ctx: RunContext, issues: list[dict] | None = None) -> InsightBody:
    rules = load_factor_rules()
    catalog = rules.get("catalog", {})
    interp = load_yaml("interpretation.yaml")["factors"]
    roles = load_yaml("roles.yaml")["roles"]
    profile = ctx.profile
    ev = ctx.event
    contributions = ctx.relevance.get("contributions", [])
    cfactors = {f["factor"]: f for f in profile.get("factors", [])}
    d = Draft()
    issues = issues or []
    issue_codes = {i["code"] for i in issues}

    what = []
    for fact in ev.facts[:3]:
        mid = fact["material_id"]
        if mid not in ctx.materials:
            continue
        what.append(
            d.claim(
                "fact",
                fact["text"],
                [SourceRef(type="material", id=mid, quote=fact["quote"], label=ctx.materials[mid].source)],
            )
        )
    if len(ev.material_ids) > 1:
        what.append(
            d.claim(
                "ai",
                f"Событие подтверждают несколько независимых источников: {len(ev.material_ids)}.",
                [
                    SourceRef(type="material", id=m, label=ctx.materials[m].source)
                    for m in ev.material_ids
                    if m in ctx.materials
                ],
            )
        )

    role_mult = roles.get(ctx.role, {}).get("multipliers", {})
    primary = ((ev.classification or {}).get("factors") or [{}])[0].get("factor")
    ranked = sorted(
        contributions, key=lambda c: (c["factor"] != primary, -c["value"] * role_mult.get(c["factor"], 1.0))
    )
    ordered = ranked[:1] + [c for c in ranked[1:] if c.get("strength", 1) >= 2][:2]
    why, feature_ids = [], []
    for contrib in ordered:
        factor = contrib["factor"]
        refs = merge_refs([r for r in (feature_ref(dr, profile) for dr in contrib["drivers"]) if r], profile)
        if not refs:
            continue
        for fid, _ in refs:
            if fid not in feature_ids:
                feature_ids.append(fid)
        phrases = "; ".join(p for _, p in refs[:3])
        why.append(
            d.claim(
                "ai",
                f"Фактор «{catalog.get(factor, {}).get('name', factor)}» значим для компании: {phrases}.",
                [SourceRef(type="feature", id=fid, label=p) for fid, p in refs[:3]],
                factor=factor,
            )
        )
    match = ctx.relevance.get("industry_match")
    if match and match.startswith("отрасль компании группы"):
        g = profile.get("group") or {}
        why.append(
            d.claim(
                "ai",
                f"Событие касается {match}: последствия для одной компании группы {g.get('group_id')} "
                f"могут затронуть и вашу через общую выручку, финансирование и внутригрупповые расчёты.",
                [SourceRef(type="feature", id="GROUP_ID", label=g.get("group_id"))],
            )
        )
        if "GROUP_ID" not in feature_ids:
            feature_ids.append("GROUP_ID")
    elif match:
        why.append(
            d.claim(
                "ai",
                f"Событие напрямую касается отрасли: {match}.",
                [SourceRef(type="feature", id="MAINOKVED_CODE", label=profile["industry"].get("name"))],
            )
        )
        if "MAINOKVED_CODE" not in feature_ids:
            feature_ids.append("MAINOKVED_CODE")
    if ctx.relevance.get("region_match"):
        why.append(
            d.claim(
                "ai",
                f"Событие затрагивает {ctx.relevance['region_match']}.",
                [SourceRef(type="feature", id="REGION", label=profile["geo"].get("region"))],
            )
        )
        feature_ids.append("REGION")
    if len(set(feature_ids)) < 2 or "few_features" in issue_codes:
        rev = profile["scale"].get("revenue_rub")
        scale = load_yaml("interpretation.yaml")["feature_phrases"]["REVENUE_COMPANY"].format(
            revenue_bn=_fmt_bn(rev)
        )
        extra = [SourceRef(type="feature", id="REVENUE_COMPANY", label=scale)]
        text = f"Масштаб бизнеса усиливает эффект: {scale}"
        if profile.get("group", {}).get("member_count", 0) > 1:
            g = profile["group"]
            text += f"; компания входит в группу из {g['member_count']} компаний, эффект может затронуть всю группу"
            extra.append(SourceRef(type="feature", id="GROUP_ID", label=g["group_id"]))
        why.append(d.claim("ai", text + ".", extra))
        feature_ids.extend(e.id for e in extra)

    risks, opps, watch, actions = [], [], [], []
    used_factors = []
    why_by_factor = {c.factor: c for c in why if c.factor}
    for contrib in ordered[:3]:
        factor, direction = contrib["factor"], contrib.get("direction") or "flat"
        spec = interp.get(factor)
        if not spec:
            continue
        cf = cfactors.get(factor, {})
        effects = classify_effect(factor, cf.get("polarity", "mixed"), direction, contrib["drivers"], profile)
        dir_text = spec["dir"].get(direction, spec["dir"].get("flat"))
        base_src = (
            [SourceRef(type="claim", id=why_by_factor[factor].id)]
            if factor in why_by_factor
            else [SourceRef(type="feature", id=feature_ids[0] if feature_ids else "MAINOKVED_CODE")]
        )
        evidence = [SourceRef(type="material", id=ev.material_ids[0], label="событие")]
        for eff in effects:
            kind = bucket(eff)
            if kind == "watch":
                if direction == "flat" and contrib is not ordered[0]:
                    continue
                watch.append(
                    d.claim(
                        "hypothesis",
                        spec["mixed"].format(dir=dir_text).capitalize() + ".",
                        base_src + evidence,
                        factor=factor,
                    )
                )
                continue
            variant = f"{eff}_down" if direction == "down" and f"{eff}_down" in spec else eff
            text = spec.get(variant, spec.get(kind)).format(dir=dir_text)
            target = risks if kind == "risk" else opps
            target.append(
                d.claim("hypothesis", text[0].upper() + text[1:] + ".", base_src + evidence, factor=factor)
            )
            pkind = (
                kind
                if not variant.endswith("_down")
                else ("risk" if kind == "opportunity" else "opportunity")
            )
            prod = spec.get(f"product_{pkind}")
            product = (
                next((p for p in ctx.products if p["factor"] == factor and p["kind"] == pkind), None)
                if prod
                else None
            )
            act = spec.get(f"action_{variant}") or spec.get(f"action_{eff}") or spec.get(f"action_{kind}")
            if act and len(actions) < 3:
                actions.append(
                    d.claim(
                        "ai",
                        act + ".",
                        [SourceRef(type="claim", id=target[-1].id)],
                        factor=factor,
                        bank_product=product["code"] if product else None,
                        bank_product_title=product["title"] if product else None,
                    )
                )
        used_factors.append(factor)

    seen_rel, related = set(), []
    for rel in sorted(ctx.related, key=lambda r: (r["factor"] in used_factors, -r.get("strength", 1))):
        if rel["factor"] in seen_rel:
            continue
        seen_rel.add(rel["factor"])
        related.append(rel)
    for rel in related[:2]:
        spec = interp.get(rel["factor"])
        if not spec or rel["material_id"] not in ctx.materials:
            continue
        dir_text = spec["dir"].get(rel["direction"], spec["dir"].get("flat"))
        name = catalog.get(rel["factor"], {}).get("name", rel["factor"])
        date = (rel.get("published_at") or "")[:10]
        watch.append(
            d.claim(
                "hypothesis",
                f"Связанный фактор «{name}»: {dir_text} (событие «{rel['title']}», {date}). "
                f"Вместе с текущей новостью это усиливает общий эффект для компании.",
                [
                    SourceRef(type="material", id=rel["material_id"], label=rel["title"]),
                    SourceRef(type="event", id=rel["event_id"]),
                ],
                factor=rel["factor"],
            )
        )
        if rel["factor"] not in used_factors:
            used_factors.append(rel["factor"])

    if len(used_factors) >= 2 and (risks or opps):
        names = [catalog.get(f, {}).get("name", f) for f in used_factors[:2]]
        anchor = (risks or opps)[0]
        (risks if risks else opps).append(
            d.claim(
                "hypothesis",
                f"Эффекты складываются: «{names[0]}» и «{names[1]}» действуют на компанию одновременно, "
                f"поэтому оценивать их стоит вместе, а не по отдельности.",
                [SourceRef(type="claim", id=anchor.id)]
                + [SourceRef(type="claim", id=w.id) for w in watch[:1]]
                + [SourceRef(type="feature", id=feature_ids[0] if feature_ids else "MAINOKVED_CODE")],
            )
        )

    if not actions:
        generic = load_yaml("interpretation.yaml").get("generic_actions", [])
        if generic and (risks or opps or watch):
            actions.append(
                d.claim("ai", generic[0], [SourceRef(type="claim", id=(risks or opps or watch)[0].id)])
            )

    bank = []
    from app.processing.text import best_sentence, stems

    event_stems = set(stems(" ".join([ev.title] + [f["text"] for f in ev.facts])))
    for f in used_factors:
        event_stems |= set(
            stems(" ".join(catalog.get(f, {}).get("keywords", []) + [catalog.get(f, {}).get("name", "")]))
        )
    q = " ".join(
        [ev.title]
        + [f["text"] for f in ev.facts[:2]]
        + [catalog.get(f, {}).get("name", "") for f in used_factors]
    )
    for cid, m in ctx.alfa.items():
        sentence = best_sentence(m.text, q, 45)
        overlap = len(set(stems(sentence)) & event_stems)
        if not sentence or overlap < BANK_MIN_OVERLAP:
            continue
        bank.append(
            d.claim(
                "bank",
                sentence,
                [SourceRef(type="alfa", id=cid, quote=sentence, label=f"{m.report}, с. {m.page}")],
            )
        )
        if len(bank) == 2:
            break

    main = ordered[0]["factor"] if ordered else None
    main_dir = (ordered[0].get("direction") or "flat") if ordered else "flat"
    if main and main in interp:
        spec = interp[main]
        dir_text = spec["dir"].get(main_dir, spec["dir"].get("flat"))
        main_risk = any(c.factor == main for c in risks)
        main_opp = any(c.factor == main for c in opps)
        kind = (
            "риск"
            if main_risk and not main_opp
            else "возможность"
            if main_opp and not main_risk
            else "риск и возможность"
            if main_risk and main_opp
            else "важно отслеживать"
        )
        title = f"{ev.title.rstrip('.')} — {kind} для {spec.get('object', 'компании')}"
    else:
        title = ev.title
    main_claims = [c for c in risks + opps if c.factor == main]
    lead = main_claims[0] if main_claims else (risks or opps or [None])[0]
    kinds = {("risk" if c in risks else "opportunity") for c in main_claims}
    polarity = kinds.pop() if len(kinds) == 1 else "mixed"
    summary_parts = []
    if lead is not None:
        summary_parts.append(lead.text)
    lead_action = next((a for a in actions if a.factor == main), actions[0] if actions else None)
    if lead_action is not None:
        summary_parts.append("Что сделать: " + lead_action.text[0].lower() + lead_action.text[1:])
    modality = (ev.classification or {}).get("modality", "fact")
    confidence = (
        0.5
        + 0.15 * bool(bank)
        + 0.1 * (modality == "fact")
        + 0.1 * (len(used_factors) >= 2)
        + 0.1 * (len(ev.material_ids) > 1)
    )
    impact = assess_impact(ctx, main, cfactors, modality, bool(bank))
    missing = []
    if not bank:
        missing.append("Аналитика банка по теме отсутствует")
    if not what:
        missing.append("Нет подтверждённых фактов из первоисточника")
    horizon = interp.get(main, {}).get("horizon", "1-3m") if main else "1-3m"
    sections = Sections(
        what_happened=what,
        why_it_matters=why,
        consequences=Consequences(risks=risks, opportunities=opps, watch_factors=watch),
        actions=actions,
        bank_view=bank,
        evidence=Evidence(
            features=sorted(set(feature_ids)),
            materials=sorted(
                ctx.materials.keys()
                & set(s.id for c in d.claims.values() for s in c.sources if s.type == "material")
            ),
            alfa=sorted(ctx.alfa.keys() & set(s.id for c in bank for s in c.sources)),
        ),
    )
    return InsightBody(
        insight_id="pending",
        event_id=ev.id,
        company_id=ctx.company.id,
        role=ctx.role,
        title=title,
        summary=" ".join(summary_parts),
        relevance={},
        sections=sections,
        factors=used_factors,
        polarity=polarity,
        horizon=horizon,
        impact=impact,
        confidence=round(min(0.95, confidence), 2),
        verification=Verification(passed=False, iterations=0),
        status="ok",
        missing=missing,
        trace_id=ctx.trace_id,
        generated_at=datetime.utcnow().isoformat(timespec="seconds"),
        models={},
    )


ANALYST_SYSTEM = """Ты — аналитик Альфа-Банка для руководителей крупного бизнеса.
По данным контекста подготовь персональную интерпретацию события для конкретной компании и роли.
Требования:
1) Отвечай на 6 вопросов: что произошло (what_happened), почему важно этой компании (why_it_matters),
   последствия (consequences: risks, opportunities, watch_factors), действия (actions), позиция банка (bank_view).
2) Каждое утверждение — объект {"id","kind","text","sources":[{"type","id","quote"?}],"factor"?,"bank_product"?,"bank_product_title"?}.
   kind=fact — только факты из материалов (type=material, id из context.materials), с цитатой quote из текста.
   kind=bank — только то, что есть в аналитике банка (type=alfa, id из context.alfa), не приписывай банку лишнего.
   kind=ai — твои выводы о связи события с компанией (why_it_matters, actions); sources — признаки компании (type=feature) и/или id других утверждений (type=claim).
   kind=hypothesis — возможные последствия и сценарии (consequences): объясни логику и основания, без выдуманных вероятностей.
   В bank_view — только kind=bank. Если твой вывод расходится с позицией банка, прямо напиши, что это вывод модели, а не позиция банка.
3) why_it_matters ссылается минимум на 2 признака компании, а не только на отрасль.
4) Последствия учитывают минимум 2 фактора (используй связанные события), это не пересказ новости.
5) Действия конкретные, связанные с признаками компании; продукт банка — только из context.products.
6) Если данных мало — честно перечисли недостающее в missing. Ничего не выдумывай.
Верни JSON: {"title": вывод для компании (не заголовок новости), "summary": 1-2 предложения,
"sections": {...}, "factors": [...], "polarity": "risk|opportunity|mixed", "horizon": "0-1m|1-3m|3-12m",
"confidence": 0..1, "missing": [...]}"""


def llm_context(ctx: RunContext, issues: list[dict] | None) -> dict:
    roles = load_yaml("roles.yaml")["roles"]
    feats = {}
    for f in ctx.profile.get("factors", []):
        for dr in f["drivers"]:
            r = feature_ref(dr, ctx.profile)
            if r:
                feats[r[0]] = r[1]
    feats["REVENUE_COMPANY"] = f"выручка {_fmt_bn(ctx.profile['scale'].get('revenue_rub'))} млрд ₽"
    feats["REGION"] = ctx.profile["geo"].get("region")
    return {
        "role": {"code": ctx.role, **roles.get(ctx.role, {})},
        "company": {
            "industry": ctx.profile["industry"],
            "scale": ctx.profile["scale"],
            "geo": ctx.profile["geo"],
            "products": ctx.profile["products"],
            "foreign_trade": ctx.profile["foreign_trade"],
            "group": ctx.profile.get("group"),
            "client_type": ctx.profile["org"].get("client_type"),
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
            for mid, m in list(ctx.materials.items())[:8]
        },
        "alfa": {
            cid: {"report": m.report, "page": m.page, "text": m.text[:1500]} for cid, m in ctx.alfa.items()
        },
        "related_events": ctx.related[:4],
        "counterparties": ctx.counterparties[:5],
        "products": ctx.products,
        "previous_issues": issues or [],
    }


def llm_analyst(ctx: RunContext, issues: list[dict] | None = None) -> tuple[InsightBody, object]:
    from app.llm.client import get_llm

    fallback = template_analyst(ctx, issues)
    user = json.dumps(llm_context(ctx, issues), ensure_ascii=False, default=str)
    result, meta = get_llm().complete(
        "analyst",
        ANALYST_SYSTEM,
        user,
        mock=lambda: fallback.model_dump(),
        trace_id=ctx.trace_id,
        use_cache=not issues,
    )
    if meta.mode == "mock":
        return fallback, meta
    try:
        data = dict(result)
        base = fallback.model_dump()
        base.update(
            {
                k: data[k]
                for k in ("title", "summary", "factors", "polarity", "horizon", "confidence", "missing")
                if k in data
            }
        )
        base["sections"] = data.get("sections") or base["sections"]
        body = InsightBody.model_validate(base)
        return body, meta
    except Exception as exc:
        ctx.notes.append(f"analyst output rejected: {exc}")
        meta.fallback = True
        return fallback, meta
