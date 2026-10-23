import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.agent.context import RunContext
from app.agent.orchestrator import run_agent
from app.agent.tools import ToolError, call_tool
from app.agent.verifier import programmatic_checks
from app.db.models import Company, Event, Insight, Trace
from app.db.session import session_scope
from app.schemas import Claim, InsightBody, SourceRef


def test_fact_claim_requires_material_source():
    with pytest.raises(ValidationError):
        Claim(id="c1", kind="fact", text="x", sources=[SourceRef(type="feature", id="F")])
    with pytest.raises(ValidationError):
        Claim(id="c1", kind="bank", text="x", sources=[SourceRef(type="material", id="m")])


def test_warm_insights_are_valid_and_traced():
    with session_scope() as s:
        items = s.scalars(select(Insight)).all()
        assert items
        for ins in items:
            body = InsightBody.model_validate(ins.body)
            assert body.status in ("ok", "insufficient_data", "not_relevant")
            tr = s.get(Trace, ins.trace_id)
            kinds = [st["kind"] for st in tr.steps]
            assert "plan" in kinds and "tool" in kinds and "verify" in kinds


def _run(event_title_like: str, company: str = "U_DEMO_A", role: str = "cfo"):
    with session_scope() as s:
        ev = s.scalar(select(Event).where(Event.title.like(f"%{event_title_like}%")))
        c = s.get(Company, company)
        ins = run_agent(s, ev, c, role, user="test")
        return InsightBody.model_validate(ins.body), s.get(Trace, ins.trace_id).steps


def test_personalized_interpretation_differs_between_companies():
    a, _ = _run("ключевую ставку", "U_DEMO_A")
    b, _ = _run("ключевую ставку", "U_DEMO_B")
    assert {s.id for c in a.sections.why_it_matters for s in c.sources} != {
        s.id for c in b.sections.why_it_matters for s in c.sources
    }


def test_irrelevant_news_never_reaches_the_model():
    from app.db.models import Material

    with session_scope() as s:
        m = s.scalar(select(Material).where(Material.title.like("%смартфон%")))
        assert m.status == "filtered" and m.event_id is None


def test_trace_records_models_for_every_step():
    body, steps = _run("ключевую ставку", "U_DEMO_A")
    llm = [st for st in steps if st["kind"] == "llm"]
    assert {st["name"] for st in llm} >= {"planner", "analyst"} and all(st["model"] for st in llm)
    assert body.models["analyst"] == "GLM-5.3-Flash"


def test_verifier_catches_fabricated_numbers():
    body, _ = _run("пошлины", "U_DEMO_A")
    with session_scope() as s:
        ev = s.get(Event, body.event_id)
        ctx = RunContext(event=ev, company=s.get(Company, "U_DEMO_A"), role="cfo", user="t", relevance={})
        from app.agent.tools import t_get_event

        t_get_event(s, ctx)
        bad = body.model_copy(deep=True)
        bad.sections.what_happened[0].text = "Пошлина составит 99% с 2031 года."
        checks = programmatic_checks(bad, ctx)
        assert not checks[0]["pass"]


def test_tool_whitelist_and_forbidden_args():
    with session_scope() as s:
        ev = s.scalar(select(Event).limit(1))
        ctx = RunContext(event=ev, company=s.get(Company, "U_DEMO_A"), role="cfo", user="t", relevance={})
        with pytest.raises(ToolError):
            call_tool(s, ctx, "delete_everything", {})
        res = call_tool(s, ctx, "get_company_profile", {"company_id": "U_DEMO_B"})
        assert res["company_id"] == "U_DEMO_A"
