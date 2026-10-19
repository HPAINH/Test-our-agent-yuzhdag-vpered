import json
import logging
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.analyst import llm_analyst, template_analyst
from app.agent.context import LIMITS, LimitExceeded, RunContext, Timer
from app.agent.planner import make_plan
from app.agent.tools import ToolError, call_tool
from app.agent.verifier import drop_unsupported, verify
from app.config import load_factor_rules
from app.db.models import Company, Event, Insight, Material, Relevance, Trace
from app.ranking.score import reference_time, score_event
from app.schemas import Claim, InsightBody, Sections, SourceRef, Verification

log = logging.getLogger("agent")


def _llm_step(ctx: RunContext, name: str, meta, summary: str) -> None:
    ctx.step(
        "llm",
        name,
        model=meta.model,
        tokens=meta.tokens,
        cost_rub=meta.cost_rub,
        ms=meta.ms,
        output_summary=summary,
        detail={
            "mode": meta.mode,
            "cache_hit": meta.cache_hit,
            "fallback": meta.fallback,
            "errors": meta.errors[:3],
        },
    )
    ctx.models[name] = meta.model
    ctx.charge_llm(meta)


def _not_relevant(ctx: RunContext) -> InsightBody:
    facts = []
    for i, f in enumerate(ctx.event.facts[:2], start=1):
        if f["material_id"] in ctx.materials:
            facts.append(
                Claim(
                    id=f"c{i}",
                    kind="fact",
                    text=f["text"],
                    sources=[SourceRef(type="material", id=f["material_id"], quote=f["quote"])],
                )
            )
    names = load_factor_rules().get("catalog", {})
    top = ", ".join(
        names.get(f["factor"], {}).get("name", f["factor"]) for f in ctx.profile.get("factors", [])[:4]
    )
    why = Claim(
        id="c9",
        kind="ai",
        text=(
            f"Событие не затрагивает факторы, к которым чувствительна компания ({top}), "
            f"и не касается её отрасли, региона или ключевых контрагентов. Связь не найдена — выдумывать её мы не будем."
        ),
        sources=[
            SourceRef(type="feature", id="MAINOKVED_CODE", label=ctx.profile["industry"].get("name")),
            SourceRef(type="feature", id="REGION", label=ctx.profile["geo"].get("region")),
        ],
    )
    return InsightBody(
        insight_id="pending",
        event_id=ctx.event.id,
        company_id=ctx.company.id,
        role=ctx.role,
        title="Событие не значимо для вашей компании",
        summary=why.text,
        sections=Sections(what_happened=facts, why_it_matters=[why]),
        factors=[],
        polarity="mixed",
        confidence=0.7,
        verification=Verification(passed=True, iterations=0),
        status="not_relevant",
        trace_id=ctx.trace_id,
        generated_at=datetime.utcnow().isoformat(timespec="seconds"),
    )


def _adapt(session: Session, ctx: RunContext, issues: list[dict]) -> list[str]:
    actions = []
    codes = {i.get("code") for i in issues}
    names = load_factor_rules().get("catalog", {})
    if "single_factor" in codes:
        have = {r["factor"] for r in ctx.related} | {
            c["factor"] for c in ctx.relevance.get("contributions", [])
        }
        candidates = [
            f["factor"]
            for f in ctx.profile.get("factors", [])
            if f["factor"] not in have and f["factor"] != "region"
        ]
        for factor in candidates[:2]:
            res = call_tool(session, ctx, "get_related_events", {"factor": factor, "days": 30})
            actions.append(f"get_related_events({factor}) → {len(res) if isinstance(res, list) else 0}")
            if isinstance(res, list) and res:
                break
    if any(c in codes for c in ("empty_bank_view",)) or any(i.get("check") == 2 for i in issues):
        q = " ".join(
            [ctx.event.title, ctx.profile["industry"].get("sector_name") or ""]
            + [names.get(c["factor"], {}).get("name", "") for c in ctx.relevance.get("contributions", [])[:3]]
        )
        res = call_tool(session, ctx, "search_alfa_analytics", {"query": q, "top_k": 3, "min_score": 1.5})
        actions.append(
            f"search_alfa_analytics(расширенный запрос) → {len(res) if isinstance(res, list) else 0}"
        )
    if any(i.get("check") == 1 for i in issues):
        actions.append("удалить неподтверждённые утверждения и переписать блок")
    if "few_features" in codes:
        actions.append("добавить признаки компании в «почему важно»")
    return actions


def run_agent(
    session: Session,
    event: Event,
    company: Company,
    role: str,
    user: str | None = None,
    relevance: dict | None = None,
    persist: bool = True,
    on_step=None,
) -> Insight:
    if relevance is None:
        rel = session.scalar(
            select(Relevance).where(
                Relevance.event_id == event.id, Relevance.company_id == company.id, Relevance.role == role
            )
        )
        if rel:
            score, relevance = rel.score, rel.breakdown
        else:
            score, relevance = score_event(event, company.profile, role, reference_time(session))
    else:
        score = relevance.get("score", 0)
    ctx = RunContext(event=event, company=company, role=role, user=user, relevance=relevance, on_step=on_step)
    ctx.step(
        "plan",
        "init",
        input_summary=f"event={event.id} company={company.id} role={role}",
        output_summary=f"контекст создан, значимость {round(score, 2)}",
        detail={"limits": LIMITS},
    )
    body: InsightBody | None = None
    try:
        plan, meta = make_plan(ctx)
        ctx.plan = plan.model_dump()
        _llm_step(
            ctx, "planner", meta, f"план из {len(plan.steps)} шагов: " + ", ".join(s.tool for s in plan.steps)
        )
        for st in plan.steps:
            try:
                call_tool(session, ctx, st.tool, st.args)
            except ToolError as exc:
                ctx.notes.append(str(exc))
        if not relevance.get("contributions"):
            body = _not_relevant(ctx)
            ctx.step("finalize", "not_relevant", output_summary="событие не связано с признаками компании")
        else:
            body, meta = llm_analyst(ctx)
            _llm_step(ctx, "analyst", meta, f"черновик: {len(body.all_claims())} утверждений")
            iterations = 0
            while True:
                with Timer() as t:
                    checks, issues, vmeta = verify(body, ctx)
                ctx.step(
                    "verify",
                    f"verifier#{iterations + 1}",
                    model=vmeta.model,
                    tokens=vmeta.tokens,
                    cost_rub=vmeta.cost_rub,
                    ms=t.ms + vmeta.ms,
                    ok=not issues,
                    output_summary=f"{sum(c['pass'] for c in checks)}/{len(checks)} проверок пройдено",
                    detail={"checks": checks},
                )
                ctx.charge_llm(vmeta)
                body.verification = Verification(
                    passed=not issues, iterations=iterations, issues=issues, checks=checks
                )
                if not issues or iterations >= LIMITS["adapt_iterations"]:
                    break
                iterations += 1
                acts = _adapt(session, ctx, issues)
                ctx.step(
                    "retry",
                    f"adapt#{iterations}",
                    input_summary="; ".join(i.get("why", "") for i in issues[:4]),
                    output_summary="; ".join(acts) or "переписать черновик",
                )
                body = drop_unsupported(body, issues)
                body, meta = llm_analyst(ctx, issues)
                _llm_step(
                    ctx,
                    f"analyst#{iterations + 1}",
                    meta,
                    f"исправленный черновик: {len(body.all_claims())} утверждений",
                )
            body = drop_unsupported(body, body.verification.issues)
            if not body.sections.what_happened:
                body.status = "insufficient_data"
                body.missing.append("Не удалось подтвердить факты события первоисточником")
            elif not body.verification.passed:
                body.missing.extend(
                    sorted({i.get("why", "") for i in body.verification.issues if i.get("why")})[:4]
                )
    except LimitExceeded as exc:
        ctx.step("error", "limits", output_summary=str(exc), ok=False)
        body = body or template_analyst(ctx)
        body.status = "insufficient_data"
        body.missing.append("Исчерпан лимит запуска агента")
    except Exception as exc:
        log.exception("agent failed")
        ctx.step("error", "exception", output_summary=str(exc), ok=False)
        body = _not_relevant(ctx) if body is None else body
        body.status = "failed"

    insight_id = f"ins_{uuid.uuid4().hex[:10]}"
    body.insight_id = insight_id
    body.trace_id = ctx.trace_id
    body.relevance = {
        "score": round(score, 3),
        **{k: relevance.get(k) for k in ("F", "I", "R", "B", "T", "E", "industry_match", "region_match")},
        "contributions": relevance.get("contributions", []),
    }
    body.models = ctx.models
    body = InsightBody.model_validate(body.model_dump())
    ctx.step("finalize", "save", output_summary=f"status={body.status}, утверждений={len(body.all_claims())}")
    trace = Trace(
        id=ctx.trace_id,
        steps=[s.model_dump() for s in ctx.steps],
        tokens=ctx.tokens,
        cost_rub=round(ctx.cost_rub, 4),
        started_at=ctx.started,
        finished_at=datetime.utcnow(),
        meta={
            "plan": ctx.plan,
            "notes": ctx.notes,
            "event_id": event.id,
            "company_id": company.id,
            "role": role,
            "profile_version": company.profile_version,
            "features_used": body.sections.evidence.features,
            "materials": list(ctx.materials),
            "alfa": list(ctx.alfa),
        },
    )
    insight = Insight(
        id=insight_id,
        event_id=event.id,
        company_id=company.id,
        role=role,
        body=json.loads(body.model_dump_json()),
        status=body.status,
        score=score,
        trace_id=ctx.trace_id,
        profile_version=company.profile_version,
    )
    if persist:
        old = session.scalars(
            select(Insight).where(
                Insight.event_id == event.id, Insight.company_id == company.id, Insight.role == role
            )
        ).all()
        for o in old:
            session.delete(o)
        session.add(trace)
        session.add(insight)
        session.flush()
    return insight


def materials_for(session: Session, ids: list[str]) -> list[Material]:
    return session.scalars(select(Material).where(Material.id.in_(ids))).all()
