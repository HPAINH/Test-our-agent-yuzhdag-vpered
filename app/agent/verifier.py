import json
import re

from app.agent.context import RunContext
from app.schemas import InsightBody

NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
GENERIC_ACTIONS = ("следите за ситуацией", "мониторить ситуацию", "отслеживать ситуацию")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().replace("ё", "е")).strip(" .…")


def _numbers(s: str) -> set[str]:
    return {n.replace(",", ".") for n in NUM_RE.findall(s)}


def programmatic_checks(body: InsightBody, ctx: RunContext) -> list[dict]:
    checks = []
    claims = {c.id: c for c in body.all_claims()}

    bad_fact = []
    for c in body.all_claims():
        for s in c.sources:
            if s.type == "material":
                m = ctx.materials.get(s.id)
                if m is None:
                    bad_fact.append({"claim": c.id, "why": f"источник {s.id} не найден"})
                    continue
                if c.kind == "fact":
                    text = _norm(m.title + " " + m.text)
                    if s.quote and _norm(s.quote).rstrip("…") not in text:
                        bad_fact.append({"claim": c.id, "why": "цитата не найдена в первоисточнике"})
                    missing_nums = _numbers(c.text) - _numbers(m.title + " " + m.text)
                    if missing_nums:
                        bad_fact.append(
                            {"claim": c.id, "why": f"числа {sorted(missing_nums)} нет в источнике"}
                        )
            if s.type == "claim" and s.id not in claims:
                bad_fact.append({"claim": c.id, "why": f"ссылка на несуществующее утверждение {s.id}"})
    checks.append(
        {"n": 1, "name": "Факты подтверждаются первоисточником", "pass": not bad_fact, "issues": bad_fact}
    )

    bad_bank = []
    for c in body.sections.bank_view:
        for s in c.sources:
            m = ctx.alfa.get(s.id) if s.type == "alfa" else None
            if s.type == "alfa" and m is None:
                bad_bank.append({"claim": c.id, "why": f"отчёт {s.id} не найден"})
            elif m is not None:
                extra = _numbers(c.text) - _numbers(m.text)
                if extra:
                    bad_bank.append({"claim": c.id, "why": f"числа {sorted(extra)} нет в отчёте банка"})
    for c in body.sections.bank_view:
        if c.kind != "bank":
            bad_bank.append({"claim": c.id, "why": "в разделе банка утверждение не из аналитики банка"})
    checks.append(
        {"n": 2, "name": "Позиция банка подтверждается отчётом", "pass": not bad_bank, "issues": bad_bank}
    )

    features = {s.id for c in body.sections.why_it_matters for s in c.sources if s.type == "feature"}
    checks.append(
        {
            "n": 3,
            "name": "«Почему важно» опирается на ≥ 2 признака компании",
            "pass": len(features) >= 2,
            "issues": []
            if len(features) >= 2
            else [{"code": "few_features", "why": f"признаков: {len(features)}"}],
        }
    )

    cons = body.sections.consequences
    factors = {c.factor for c in [*cons.risks, *cons.opportunities, *cons.watch_factors] if c.factor}
    checks.append(
        {
            "n": 4,
            "name": "Последствия учитывают ≥ 2 фактора",
            "pass": len(factors) >= 2,
            "issues": []
            if len(factors) >= 2
            else [{"code": "single_factor", "why": f"факторов: {len(factors)}"}],
        }
    )

    contradictions = []
    risk_f = {c.factor for c in cons.risks if c.factor}
    opp_f = {c.factor for c in cons.opportunities if c.factor}
    for f in risk_f & opp_f:
        pol = next((x["polarity"] for x in ctx.profile.get("factors", []) if x["factor"] == f), None)
        if pol != "mixed":
            contradictions.append(
                {"factor": f, "why": "фактор одновременно риск и возможность без пояснения"}
            )
    checks.append(
        {
            "n": 5,
            "name": "Нет противоречий между блоками",
            "pass": not contradictions,
            "issues": contradictions,
        }
    )

    bad_actions = [
        {"claim": a.id, "why": "общая рекомендация"}
        for a in body.sections.actions
        if any(g in _norm(a.text) for g in GENERIC_ACTIONS)
    ]
    if not body.sections.actions:
        bad_actions.append({"code": "no_actions", "why": "нет действий"})
    checks.append(
        {
            "n": 6,
            "name": "Действия конкретны и связаны с компанией",
            "pass": not bad_actions,
            "issues": bad_actions,
        }
    )

    empty = [
        name
        for name, items in (
            ("what_happened", body.sections.what_happened),
            ("why_it_matters", body.sections.why_it_matters),
            ("consequences", [*cons.risks, *cons.opportunities, *cons.watch_factors]),
            ("actions", body.sections.actions),
            ("bank_view", body.sections.bank_view),
        )
        if not items
    ]
    unexplained = [e for e in empty if not (e == "bank_view" and any("банка" in m for m in body.missing))]
    checks.append(
        {
            "n": 7,
            "name": "Все 6 блоков заполнены или помечены «нет данных»",
            "pass": not unexplained,
            "issues": [{"code": f"empty_{e}", "why": f"пустой блок {e}"} for e in unexplained],
        }
    )
    return checks


VERIFIER_SYSTEM = """Ты — независимый проверяющий. Тебе дают черновик инсайта и первоисточники.
Проверь каждое утверждение kind=fact и kind=bank: подтверждается ли оно указанным источником (числа, даты, названия).
Проверь утверждения kind=ai: не противоречат ли фактам и позиции банка, не пересказ ли это новости.
Верни JSON: {"issues": [{"claim": id, "code": "unsupported|contradiction|retelling|other", "why": "..."}]}.
Если проблем нет — {"issues": []}."""


def llm_check(body: InsightBody, ctx: RunContext):
    from app.llm.client import get_llm

    payload = {
        "draft": body.sections.model_dump(),
        "materials": {k: m.text[:2000] for k, m in list(ctx.materials.items())[:8]},
        "alfa": {k: m.text[:1500] for k, m in ctx.alfa.items()},
    }
    result, meta = get_llm().complete(
        "verifier",
        VERIFIER_SYSTEM,
        json.dumps(payload, ensure_ascii=False),
        mock=lambda: {"issues": []},
        trace_id=ctx.trace_id,
    )
    issues = result.get("issues", []) if isinstance(result, dict) else []
    known = {c.id for c in body.all_claims()}
    return [i for i in issues if isinstance(i, dict) and i.get("claim") in known], meta


def verify(body: InsightBody, ctx: RunContext) -> tuple[list[dict], list[dict], object]:
    checks = programmatic_checks(body, ctx)
    llm_issues, meta = llm_check(body, ctx)
    if llm_issues:
        checks[0]["issues"].extend(i for i in llm_issues if i.get("code") in ("unsupported", None))
        checks[4]["issues"].extend(i for i in llm_issues if i.get("code") == "contradiction")
        checks[0]["pass"] = not checks[0]["issues"]
        checks[4]["pass"] = not checks[4]["issues"]
    issues = []
    for ch in checks:
        if ch["pass"]:
            continue
        for i in ch["issues"] or [{"why": ch["name"]}]:
            issues.append({"check": ch["n"], "code": i.get("code") or f"check_{ch['n']}", **i})
    return checks, issues, meta


def drop_unsupported(body: InsightBody, issues: list[dict]) -> InsightBody:
    bad = {i["claim"] for i in issues if i.get("check") in (1, 2) and i.get("claim")}
    if not bad:
        return body
    s = body.sections
    s.what_happened = [c for c in s.what_happened if c.id not in bad]
    s.bank_view = [c for c in s.bank_view if c.id not in bad]
    s.why_it_matters = [c for c in s.why_it_matters if c.id not in bad]
    s.consequences.risks = [c for c in s.consequences.risks if c.id not in bad]
    s.consequences.opportunities = [c for c in s.consequences.opportunities if c.id not in bad]
    s.consequences.watch_factors = [c for c in s.consequences.watch_factors if c.id not in bad]
    s.actions = [c for c in s.actions if c.id not in bad]
    return body
