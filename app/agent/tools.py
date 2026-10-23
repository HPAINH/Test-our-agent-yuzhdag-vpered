import hashlib
import json
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.agent.context import LIMITS, LimitExceeded, RunContext, Timer
from app.config import load_factor_rules, load_yaml
from app.db.models import AuditLog, Company, Event, Material
from app.processing.text import BM25, best_sentence


class ToolError(Exception):
    pass


def _audit(session: Session, ctx: RunContext, tool: str, args: dict, result: str, detail: str = "") -> None:
    session.add(
        AuditLog(
            user=ctx.user,
            action="agent_tool",
            tool=tool,
            args_hash=hashlib.sha256(
                json.dumps(args, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()[:16],
            result=result,
            detail=detail[:500] or None,
        )
    )


def _visible_news(ctx: RunContext):
    return or_(Material.submitted_by.is_(None), Material.submitted_by == ctx.user)


def t_get_company_profile(session: Session, ctx: RunContext) -> dict:
    p = ctx.profile
    return {
        "company_id": p["company_id"],
        "industry": p["industry"],
        "scale": p["scale"],
        "geo": p["geo"],
        "products": p["products"],
        "foreign_trade": p["foreign_trade"],
        "org": p["org"],
        "group_id": p["group_id"],
        "is_large": p["is_large"],
        "summary": ctx.company.summary,
    }


def t_get_factor_exposure(session: Session, ctx: RunContext, factors: list[str] | None = None) -> list[dict]:
    allowed = set(load_factor_rules().get("catalog", {}))
    wanted = [f for f in (factors or []) if f in allowed]
    out = [f for f in ctx.profile.get("factors", []) if not wanted or f["factor"] in wanted]
    ctx.exposure = out
    return out


def t_get_counterparties(
    session: Session, ctx: RunContext, direction: str = "both", top_n: int = 5
) -> list[dict]:
    top_n = max(1, min(int(top_n), 10))
    cps = [
        c for c in ctx.profile.get("counterparties", []) if direction == "both" or c["direction"] == direction
    ]
    ctx.counterparties = sorted(cps, key=lambda c: -c["share"])[:top_n]
    return ctx.counterparties


def t_get_group_context(session: Session, ctx: RunContext) -> dict:
    g = dict(ctx.profile.get("group") or {})
    g["implicit_links"] = ctx.profile.get("implicit_links", [])[:5]
    ctx.group = g
    return g


def t_get_event(session: Session, ctx: RunContext) -> dict:
    mats = session.scalars(select(Material).where(Material.id.in_(ctx.event.material_ids))).all()
    for m in mats:
        ctx.materials[m.id] = m
    return {
        "event_id": ctx.event.id,
        "title": ctx.event.title,
        "classification": ctx.event.classification,
        "facts": ctx.event.facts,
        "materials": [
            {
                "id": m.id,
                "source": m.source,
                "title": m.title,
                "published_at": m.published_at.isoformat() if m.published_at else None,
            }
            for m in mats
        ],
        "alfa_refs": ctx.event.alfa_refs,
    }


def t_search_news(
    session: Session, ctx: RunContext, query: str, days: int = 30, top_k: int = 5
) -> list[dict]:
    days, top_k = max(1, min(int(days), 30)), max(1, min(int(top_k), 10))
    q = select(Material).where(Material.kind == "news", _visible_news(ctx))
    if ctx.event.published_at:
        q = q.where(
            Material.published_at >= ctx.event.published_at - timedelta(days=days),
            Material.published_at <= ctx.event.published_at + timedelta(days=1),
        )
    mats = {m.id: m for m in session.scalars(q)}
    hits = BM25({k: f"{m.title} {m.text}" for k, m in mats.items()}).search(query, top_k=top_k)
    out = []
    for mid, score in hits:
        m = mats[mid]
        ctx.materials[mid] = m
        out.append(
            {
                "id": mid,
                "title": m.title,
                "score": round(score, 2),
                "snippet": best_sentence(m.text, query, 30),
            }
        )
    ctx.search_hits = out
    return out


def t_search_alfa_analytics(
    session: Session, ctx: RunContext, query: str, top_k: int = 3, min_score: float = 2.5
) -> list[dict]:
    top_k = max(1, min(int(top_k), 5))
    chunks = {m.id: m for m in session.scalars(select(Material).where(Material.kind == "alfa"))}
    hits = BM25({k: f"{m.title}\n{m.text}" for k, m in chunks.items()}).search(query, top_k=top_k * 2)
    out, seen = [], set()
    for cid, score in hits:
        if score < min_score:
            continue
        m = chunks[cid]
        key = (m.report, m.page)
        if key in seen:
            continue
        seen.add(key)
        ctx.alfa[cid] = m
        out.append(
            {
                "id": cid,
                "report": m.report,
                "page": m.page,
                "score": round(score, 2),
                "quote": best_sentence(m.text, query, 35),
                "synthetic": m.synthetic,
            }
        )
        if len(out) >= top_k:
            break
    return out


def t_get_related_events(session: Session, ctx: RunContext, factor: str, days: int = 30) -> list[dict]:
    days = max(1, min(int(days), 30))
    if factor not in load_factor_rules().get("catalog", {}):
        raise ToolError(f"unknown factor {factor}")
    q = select(Event).where(
        Event.id != ctx.event.id,
        Event.status == "ok",
        or_(Event.private_to.is_(None), Event.private_to == ctx.user),
    )
    if ctx.event.published_at:
        q = q.where(
            Event.published_at >= ctx.event.published_at - timedelta(days=days),
            Event.published_at <= ctx.event.published_at + timedelta(days=days),
        )
    out = []
    for ev in session.scalars(q.order_by(Event.published_at.desc())):
        eff = next((f for f in ev.classification.get("factors", []) if f["factor"] == factor), None)
        if not eff or eff.get("strength", 1) < 2:
            continue
        if factor == "region" and ctx.profile["geo"].get("region") not in ev.classification.get(
            "regions", []
        ):
            continue
        mats = session.scalars(select(Material).where(Material.id.in_(ev.material_ids))).all()
        for m in mats:
            ctx.materials[m.id] = m
        out.append(
            {
                "event_id": ev.id,
                "title": ev.title,
                "factor": factor,
                "direction": eff["direction"],
                "strength": eff["strength"],
                "material_id": ev.material_ids[0],
                "published_at": ev.published_at.isoformat() if ev.published_at else None,
                "fact": ev.facts[0] if ev.facts else None,
            }
        )
        if len(out) >= 3:
            break
    known = {r["event_id"] for r in ctx.related}
    ctx.related.extend(r for r in out if r["event_id"] not in known)
    return out


def t_get_bank_products(session: Session, ctx: RunContext, need: str | None = None) -> list[dict]:
    flags = ctx.profile.get("flags", {})
    out = []
    for prod in load_yaml("products.yaml")["products"]:
        if need and prod["factor"] != need:
            continue
        if prod.get("needs_feature") and not flags.get(prod["needs_feature"]):
            continue
        if prod.get("unless_feature") and flags.get(prod["unless_feature"]):
            continue
        out.append(
            {
                "factor": prod["factor"],
                "kind": prod["when"],
                "code": prod["code"],
                "title": prod["title"],
                "condition": prod.get("needs_feature") or prod.get("unless_feature") or "general",
            }
        )
    ctx.products = out
    return out


REGISTRY = {
    "get_company_profile": (
        t_get_company_profile,
        "Профиль компании: отрасль, масштаб, продукты, ВЭД, группа",
    ),
    "get_factor_exposure": (
        t_get_factor_exposure,
        "Веса чувствительности компании к факторам; args: factors[]",
    ),
    "get_counterparties": (
        t_get_counterparties,
        "Ключевые контрагенты; args: direction in|out|both, top_n<=10",
    ),
    "get_group_context": (t_get_group_context, "Группа компаний и неявные связи через физлиц"),
    "get_event": (t_get_event, "Событие: факты, классификация, первоисточники"),
    "search_news": (t_search_news, "Поиск новостей; args: query, days<=30, top_k<=10"),
    "search_alfa_analytics": (
        t_search_alfa_analytics,
        "Поиск по аналитике Альфа-Банка; args: query, top_k<=5",
    ),
    "get_related_events": (t_get_related_events, "Связанные события по фактору; args: factor, days<=30"),
    "get_bank_products": (t_get_bank_products, "Продукты банка, которых нет у компании; args: need (фактор)"),
}
ALLOWED_ARGS = {
    "get_factor_exposure": {"factors"},
    "get_counterparties": {"direction", "top_n"},
    "search_news": {"query", "days", "top_k"},
    "search_alfa_analytics": {"query", "top_k", "min_score"},
    "get_related_events": {"factor", "days"},
    "get_bank_products": {"need"},
}


def call_tool(session: Session, ctx: RunContext, name: str, args: dict | None = None):
    args = dict(args or {})
    for forbidden in ("company_id", "role", "user", "login"):
        args.pop(forbidden, None)
    if name not in REGISTRY:
        _audit(session, ctx, name, args, "denied", "unknown tool")
        ctx.step(
            "error",
            name,
            input_summary=json.dumps(args, ensure_ascii=False),
            output_summary="инструмент не разрешён",
            ok=False,
        )
        raise ToolError(f"tool {name} is not allowed")
    extra = set(args) - ALLOWED_ARGS.get(name, set())
    if extra:
        for k in extra:
            args.pop(k)
    ctx.tool_calls += 1
    if ctx.tool_calls > LIMITS["tool_calls"]:
        _audit(session, ctx, name, args, "denied", "tool call limit")
        raise LimitExceeded("tool call limit reached")
    fn = REGISTRY[name][0]
    with Timer() as t:
        try:
            result = fn(session, ctx, **args)
            ok = True
        except (ToolError, TypeError, ValueError) as exc:
            result, ok = {"error": str(exc)}, False
    size = len(result) if isinstance(result, list) else 1
    _audit(session, ctx, name, args, "ok" if ok else "error")
    ctx.step(
        "tool",
        name,
        input_summary=json.dumps(args, ensure_ascii=False),
        ms=t.ms,
        ok=ok,
        output_summary=f"{size} результат(ов)" if ok else str(result),
        detail={"result_preview": _preview(result)},
    )
    return result


def _preview(result) -> object:
    try:
        text = json.dumps(result, ensure_ascii=False, default=str)
    except TypeError:
        text = str(result)
    return text[:1500]


def describe_tools() -> str:
    return "\n".join(f"- {n}: {d}" for n, (_, d) in REGISTRY.items())


def company_exists(session: Session, cid: str) -> bool:
    return session.get(Company, cid) is not None
