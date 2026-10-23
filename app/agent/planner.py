import json

from app.agent.context import RunContext
from app.agent.tools import REGISTRY, describe_tools
from app.llm.client import InvalidOutput
from app.schemas import AgentPlan

PLANNER_SYSTEM = """Ты планируешь анализ события для конкретной компании и роли руководителя.
Доступные инструменты (только они):
{tools}
Составь план из 3–8 шагов: какие данные нужны, чтобы объяснить влияние события именно на эту компанию.
Обязательно: get_event, get_company_profile, search_alfa_analytics (запрос сформулируй по сути события).
Подумай, нужны ли: экспозиция к факторам, связанные события по другим важным для компании факторам,
контрагенты (если событие касается их отраслей), контекст группы, продукты банка.
Аргументы company_id и role не передавай. Для каждого шага объясни зачем.
Верни JSON: {{"goal": "...", "steps": [{{"n": 1, "tool": "...", "args": {{...}}, "why": "..."}}],
"expected_outputs": ["..."]}}"""
MANDATORY = ["get_event", "get_company_profile", "search_alfa_analytics"]


def _validator(ctx: RunContext):
    def check(result):
        try:
            plan = AgentPlan.model_validate(result)
        except Exception as exc:
            raise InvalidOutput(f"план не по схеме: {exc}") from exc
        unknown = [s.tool for s in plan.steps if s.tool not in REGISTRY]
        if unknown:
            raise InvalidOutput(f"неизвестные инструменты: {unknown}")
        missing = [t for t in MANDATORY if t not in {s.tool for s in plan.steps}]
        if missing:
            raise InvalidOutput(f"в плане нет обязательных шагов: {missing}")
        steps = plan.steps[:8]
        order = {"get_event": 0, "get_company_profile": 1}
        steps.sort(key=lambda s: order.get(s.tool, 2))
        for i, s in enumerate(steps, start=1):
            s.n = i
            s.args = {k: v for k, v in (s.args or {}).items() if k not in ("company_id", "role")}
        alfa = next(s for s in steps if s.tool == "search_alfa_analytics")
        if not (alfa.args or {}).get("query"):
            alfa.args = {**(alfa.args or {}), "query": ctx.event.title}
        return AgentPlan(goal=plan.goal, steps=steps, expected_outputs=plan.expected_outputs)

    return check


def make_plan(ctx: RunContext):
    from app.llm.client import get_llm

    user = json.dumps(
        {
            "event": {"title": ctx.event.title, "classification": ctx.event.classification},
            "company": {
                "industry": ctx.profile["industry"],
                "top_factors": ctx.profile.get("factors", [])[:6],
                "group_members": (ctx.profile.get("group") or {}).get("member_count"),
                "counterparties": len(ctx.profile.get("counterparties", [])),
            },
            "relevance": {
                k: ctx.relevance.get(k) for k in ("industry_match", "region_match", "contributions")
            },
            "role": ctx.role,
        },
        ensure_ascii=False,
        default=str,
    )
    return get_llm().complete(
        "planner",
        PLANNER_SYSTEM.format(tools=describe_tools()),
        user,
        trace_id=ctx.trace_id,
        validate=_validator(ctx),
    )
