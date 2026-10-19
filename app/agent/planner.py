import json

from app.agent.context import RunContext
from app.agent.tools import REGISTRY, describe_tools
from app.config import load_factor_rules
from app.schemas import AgentPlan, PlanStep

PLANNER_SYSTEM = """Ты планируешь анализ события для конкретной компании.
Доступные инструменты (только они):
{tools}
Составь план из 3–7 шагов. Обязательно: get_event, get_company_profile, search_alfa_analytics.
Если событие касается одного фактора, добавь get_related_events по второму важному для компании фактору.
Если событие связано с отраслью контрагентов — get_counterparties. Аргументы company_id и role не передавай.
Верни JSON: {{"goal": "...", "steps": [{{"n": 1, "tool": "...", "args": {{...}}, "why": "..."}}],
"expected_outputs": ["..."]}}"""
MANDATORY = ["get_event", "get_company_profile", "search_alfa_analytics"]


def rule_plan(ctx: RunContext) -> AgentPlan:
    catalog = load_factor_rules().get("catalog", {})
    cls = ctx.event.classification or {}
    ev_factors = [f["factor"] for f in cls.get("factors", [])]
    contrib = ctx.relevance.get("contributions", [])
    names = " ".join(catalog.get(f, {}).get("name", "") for f in ev_factors[:3])
    steps = [
        PlanStep(n=1, tool="get_event", why="Факты, источники и классификация события"),
        PlanStep(n=2, tool="get_company_profile", why="Признаки компании для персонализации"),
        PlanStep(
            n=3,
            tool="get_factor_exposure",
            args={"factors": ev_factors},
            why="Чувствительность к факторам события",
        ),
        PlanStep(
            n=4,
            tool="search_alfa_analytics",
            args={"query": f"{ctx.event.title} {names}", "top_k": 2},
            why="Позиция Альфа-Банка по теме",
        ),
    ]
    company_top = [
        f["factor"]
        for f in ctx.profile.get("factors", [])
        if f["factor"] not in ev_factors[:1] and f["factor"] != "region"
    ][:3]
    primary = ev_factors[0] if ev_factors else None
    second = next(
        (c["factor"] for c in contrib if c["factor"] != primary and c.get("strength", 1) >= 2), None
    ) or next((f for f in company_top if f != primary), None)
    if second:
        steps.append(
            PlanStep(
                n=len(steps) + 1,
                tool="get_related_events",
                args={"factor": second, "days": 30},
                why="Второй фактор для многофакторной оценки",
            )
        )
    if (ctx.relevance.get("industry_match") or "").startswith("отрасль контрагента") or any(
        dr.startswith("counterparty=") for c in contrib for dr in c["drivers"]
    ):
        steps.append(
            PlanStep(
                n=len(steps) + 1,
                tool="get_counterparties",
                args={"direction": "both", "top_n": 5},
                why="Событие затрагивает ключевых контрагентов",
            )
        )
    if (ctx.profile.get("group") or {}).get("member_count", 0) > 1:
        steps.append(PlanStep(n=len(steps) + 1, tool="get_group_context", why="Влияние на группу компаний"))
    steps.append(PlanStep(n=len(steps) + 1, tool="get_bank_products", why="Продукты банка для действий"))
    return AgentPlan(
        goal=f"Оценить влияние «{ctx.event.title}» на компанию {ctx.company.id} для роли {ctx.role}",
        steps=steps,
        expected_outputs=["Insight по схеме", "трасса"],
    )


def sanitize(plan: AgentPlan, fallback: AgentPlan) -> AgentPlan:
    steps = [s for s in plan.steps if s.tool in REGISTRY][:8]
    present = {s.tool for s in steps}
    for tool in MANDATORY:
        if tool not in present:
            src = next(s for s in fallback.steps if s.tool == tool)
            steps.insert(0 if tool != "search_alfa_analytics" else len(steps), src)
    if "get_bank_products" not in {s.tool for s in steps}:
        steps.append(PlanStep(n=0, tool="get_bank_products"))
    order = {"get_event": 0, "get_company_profile": 1}
    steps.sort(key=lambda s: order.get(s.tool, 2))
    for i, s in enumerate(steps, start=1):
        s.n = i
    return AgentPlan(goal=plan.goal or fallback.goal, steps=steps, expected_outputs=plan.expected_outputs)


def make_plan(ctx: RunContext):
    from app.llm.client import get_llm

    fallback = rule_plan(ctx)
    user = json.dumps(
        {
            "event": {"title": ctx.event.title, "classification": ctx.event.classification},
            "company": {
                "industry": ctx.profile["industry"],
                "top_factors": ctx.profile.get("factors", [])[:6],
                "group": (ctx.profile.get("group") or {}).get("member_count"),
            },
            "relevance": {k: ctx.relevance.get(k) for k in ("industry_match", "region_match")},
            "role": ctx.role,
        },
        ensure_ascii=False,
    )
    result, meta = get_llm().complete(
        "planner",
        PLANNER_SYSTEM.format(tools=describe_tools()),
        user,
        mock=lambda: fallback.model_dump(),
        trace_id=ctx.trace_id,
    )
    try:
        plan = sanitize(AgentPlan.model_validate(result), fallback)
    except Exception:
        plan, meta.fallback = fallback, True
    return plan, meta
