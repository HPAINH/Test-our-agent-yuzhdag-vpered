import json
import queue
import threading
from collections import Counter
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.agent.chat import answer
from app.agent.orchestrator import run_agent
from app.api.auth import admin_user, audit, current_user, issue_token, limiter
from app.config import get_settings, load_factor_rules, load_yaml
from app.db.models import (
    AppUser,
    AuditLog,
    Company,
    Event,
    Feedback,
    Insight,
    LLMCall,
    Material,
    Relevance,
    Trace,
    UIEvent,
)
from app.db.session import get_db, session_scope
from app.ingest.news import UnsafeURL, add_material, fetch_url
from app.llm.client import get_llm
from app.processing.pipeline import process_material
from app.ranking.score import reference_time, score_event
from app.schemas import AnalyzeIn, ChatIn, FeedbackIn, LoginIn, UIEventIn

router = APIRouter(prefix="/api/v1")
PAGE_SIZE = 20


def err(status: int, code: str, message: str):
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def factor_names() -> dict:
    return {k: v["name"] for k, v in load_factor_rules().get("catalog", {}).items()}


def company_brief(c: Company) -> dict:
    p = c.profile
    return {
        "id": c.id,
        "industry": p["industry"].get("name"),
        "okved": p["industry"].get("okved"),
        "sector": p["industry"].get("sector_name"),
        "region": p["geo"].get("region"),
        "city": p["geo"].get("city"),
        "revenue_rub": p["scale"].get("revenue_rub"),
        "is_large": c.is_large,
        "client_type": p["org"].get("client_type"),
        "group_id": p.get("group_id"),
    }


def user_dict(u: AppUser, db: Session) -> dict:
    roles = load_yaml("roles.yaml")["roles"]
    c = db.get(Company, u.company_id)
    return {
        "login": u.login,
        "display_name": u.display_name,
        "role": u.role,
        "role_name": roles.get(u.role, {}).get("name", u.role),
        "role_focus": roles.get(u.role, {}).get("focus"),
        "is_admin": u.is_admin,
        "company": company_brief(c) if c else None,
    }


def visible_event(e: Event, user: AppUser) -> bool:
    return e.private_to is None or e.private_to == user.login


def card(ev: Event, rel: Relevance | None, ins: Insight | None, mats: dict[str, Material]) -> dict:
    names = factor_names()
    b = ins.body if ins else None
    contrib = (rel.breakdown.get("contributions") if rel else []) or []
    est = Counter(c["polarity"] for c in contrib[:2])
    sources = [{"id": m, "source": mats[m].source} for m in ev.material_ids if m in mats]
    return {
        "event_id": ev.id,
        "insight_id": ins.id if ins else None,
        "generated": ins is not None,
        "title": b["title"] if b else ev.title,
        "event_title": ev.title,
        "summary": b["summary"] if b else (ev.facts[0]["text"] if ev.facts else ""),
        "polarity": b["polarity"] if b else (est.most_common(1)[0][0] if est else "mixed"),
        "status": ins.status if ins else "pending",
        "score": round(rel.score, 3) if rel else None,
        "factors": [
            {"code": f, "name": names.get(f, f)}
            for f in (b["factors"] if b else [c["factor"] for c in contrib[:3]])
        ],
        "horizon": b["horizon"] if b else None,
        "confidence": b["confidence"] if b else None,
        "has_bank_view": bool(b and b["sections"]["bank_view"]) if b else bool(ev.alfa_refs),
        "published_at": ev.published_at.isoformat() if ev.published_at else None,
        "sources": sources,
        "private": ev.private_to is not None,
        "type": (ev.classification or {}).get("type"),
        "regions": (ev.classification or {}).get("regions", []),
        "stale": bool(ins and ins.stale),
    }


def _feed_rows(db: Session, user: AppUser, min_score: float):
    rows = db.execute(
        select(Relevance, Event)
        .join(Event, Event.id == Relevance.event_id)
        .where(
            Relevance.company_id == user.company_id,
            Relevance.role == user.role,
            Relevance.score >= min_score,
            or_(Event.private_to.is_(None), Event.private_to == user.login),
        )
        .order_by(Relevance.score.desc())
    ).all()
    ids = [e.id for _, e in rows]
    insights = {
        i.event_id: i
        for i in db.scalars(
            select(Insight).where(
                Insight.company_id == user.company_id, Insight.role == user.role, Insight.event_id.in_(ids)
            )
        )
    }
    mat_ids = [m for _, e in rows for m in e.material_ids]
    mats = {m.id: m for m in db.scalars(select(Material).where(Material.id.in_(mat_ids)))}
    return rows, insights, mats


@router.get("/health")
def health(db: Session = Depends(get_db)):
    ok_db = db.scalar(select(func.count(AppUser.login))) is not None
    return {"status": "ok" if ok_db else "degraded", "db": ok_db, "llm": get_llm().budget_state()}


@router.get("/auth/users")
def demo_users(db: Session = Depends(get_db)):
    return [user_dict(u, db) for u in db.scalars(select(AppUser).order_by(AppUser.company_id, AppUser.login))]


@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.get(AppUser, body.login)
    if user is None:
        audit(db, body.login, "login", "denied")
        err(401, "unknown_user", "Учётная запись не найдена")
    audit(db, user.login, "login", "ok")
    return {"token": issue_token(user.login), "user": user_dict(user, db)}


@router.get("/me")
def me(user: AppUser = Depends(current_user), db: Session = Depends(get_db)):
    return user_dict(user, db)


@router.get("/meta")
def meta(user: AppUser = Depends(current_user)):
    return {
        "factors": factor_names(),
        "roles": {k: v["name"] for k, v in load_yaml("roles.yaml")["roles"].items()},
        "llm_mode": get_llm().mode,
    }


@router.get("/companies/me/profile")
def my_profile(user: AppUser = Depends(current_user), db: Session = Depends(get_db)):
    c = db.get(Company, user.company_id)
    names = factor_names()
    p = dict(c.profile)
    p["factors"] = [{**f, "name": names.get(f["factor"], f["factor"])} for f in p.get("factors", [])]
    group = []
    for m in (p.get("group") or {}).get("members", []):
        gc = db.get(Company, m)
        if gc:
            group.append(company_brief(gc))
    return {
        "company": company_brief(c),
        "profile": p,
        "group_members": group,
        "summary": c.summary,
        "profile_version": c.profile_version,
    }


@router.get("/home/widget")
def widget(user: AppUser = Depends(current_user), db: Session = Depends(get_db)):
    threshold = get_settings().feed_threshold
    rows, insights, mats = _feed_rows(db, user, threshold)
    ref = reference_time(db)
    fresh = sum(1 for _, e in rows if e.published_at and e.published_at >= ref - timedelta(days=3))
    cards = [card(e, r, insights.get(e.id), mats) for r, e in rows[:3]]
    risks = sum(1 for r, e in rows if (insights.get(e.id) and insights[e.id].body.get("polarity") == "risk"))
    return {
        "top": cards,
        "total": len(rows),
        "new_count": fresh,
        "risk_count": risks,
        "as_of": ref.isoformat() if ref else None,
    }


@router.get("/feed")
def feed(
    user: AppUser = Depends(current_user),
    db: Session = Depends(get_db),
    factor: str | None = None,
    kind: str | None = Query(default=None, pattern="^(risk|opportunity|mixed)$"),
    horizon: str | None = None,
    region: str | None = None,
    min_score: float | None = None,
    page: int = 1,
):
    rows, insights, mats = _feed_rows(
        db, user, min_score if min_score is not None else get_settings().feed_threshold
    )
    cards = [card(e, r, insights.get(e.id), mats) for r, e in rows]
    if factor:
        cards = [c for c in cards if any(f["code"] == factor for f in c["factors"])]
    if kind:
        cards = [c for c in cards if c["polarity"] == kind]
    if horizon:
        cards = [c for c in cards if c["horizon"] == horizon]
    if region:
        cards = [c for c in cards if region in c["regions"]]
    total = len(cards)
    start = (max(page, 1) - 1) * PAGE_SIZE
    return {"items": cards[start : start + PAGE_SIZE], "total": total, "page": page, "page_size": PAGE_SIZE}


def _own_insight(db: Session, insight_id: str, user: AppUser) -> Insight:
    ins = db.get(Insight, insight_id)
    if ins is None or ins.company_id != user.company_id:
        if ins is not None:
            audit(db, user.login, "foreign_insight_access", "denied", insight_id)
            db.commit()
        err(404, "not_found", "Инсайт не найден")
    return ins


def insight_payload(db: Session, ins: Insight) -> dict:
    ev = db.get(Event, ins.event_id)
    mats = db.scalars(
        select(Material).where(
            Material.id.in_(
                set(ins.body["sections"]["evidence"]["materials"])
                | set(ev.material_ids)
                | set(ins.body["sections"]["evidence"]["alfa"])
            )
        )
    ).all()
    names = factor_names()
    return {
        "insight": ins.body,
        "status": ins.status,
        "stale": ins.stale,
        "score": ins.score,
        "event": {
            "id": ev.id,
            "title": ev.title,
            "classification": ev.classification,
            "published_at": ev.published_at.isoformat() if ev.published_at else None,
            "private": ev.private_to is not None,
        },
        "factor_names": names,
        "sources": {
            m.id: {
                "id": m.id,
                "kind": m.kind,
                "source": m.source,
                "title": m.title,
                "url": m.url,
                "report": m.report,
                "page": m.page,
                "synthetic": m.synthetic,
                "published_at": m.published_at.isoformat() if m.published_at else None,
            }
            for m in mats
        },
    }


@router.get("/insights/{insight_id}")
def get_insight(insight_id: str, user: AppUser = Depends(current_user), db: Session = Depends(get_db)):
    ins = _own_insight(db, insight_id, user)
    db.add(UIEvent(user=user.login, kind="open_insight", ref=insight_id))
    return insight_payload(db, ins)


@router.get("/events/{event_id}/insight")
def event_insight(event_id: str, user: AppUser = Depends(current_user)):
    with session_scope() as db:
        ev = db.get(Event, event_id)
        if ev is None or not visible_event(ev, user):
            err(404, "not_found", "Событие не найдено")
        ins = db.scalar(
            select(Insight).where(
                Insight.event_id == event_id, Insight.company_id == user.company_id, Insight.role == user.role
            )
        )
        if ins is None or ins.stale:
            company = db.get(Company, user.company_id)
            rel = db.scalar(
                select(Relevance).where(
                    Relevance.event_id == event_id,
                    Relevance.company_id == user.company_id,
                    Relevance.role == user.role,
                )
            )
            ins = run_agent(
                db,
                ev,
                company,
                user.role,
                user=user.login,
                relevance={**rel.breakdown, "score": rel.score} if rel else None,
            )
        return insight_payload(db, ins)


@router.get("/insights/{insight_id}/trace")
def get_trace(insight_id: str, user: AppUser = Depends(current_user), db: Session = Depends(get_db)):
    ins = _own_insight(db, insight_id, user)
    tr = db.get(Trace, ins.trace_id)
    if tr is None:
        err(404, "not_found", "Трасса не найдена")
    db.add(UIEvent(user=user.login, kind="open_trace", ref=insight_id))
    return {
        "id": tr.id,
        "steps": tr.steps,
        "tokens": tr.tokens,
        "cost_rub": tr.cost_rub,
        "meta": tr.meta,
        "started_at": tr.started_at.isoformat(),
        "finished_at": tr.finished_at.isoformat() if tr.finished_at else None,
        "duration_ms": int((tr.finished_at - tr.started_at).total_seconds() * 1000)
        if tr.finished_at
        else None,
    }


@router.post("/insights/{insight_id}/regenerate")
def regenerate(insight_id: str, user: AppUser = Depends(current_user)):
    limiter.check(user.login, "regenerate", 3)
    with session_scope() as db:
        ins = _own_insight(db, insight_id, user)
        ev, company = db.get(Event, ins.event_id), db.get(Company, user.company_id)
        new = run_agent(db, ev, company, user.role, user=user.login)
        return insight_payload(db, new)


@router.get("/digest")
def digest(user: AppUser = Depends(current_user), db: Session = Depends(get_db)):
    rows, insights, _ = _feed_rows(db, user, get_settings().feed_threshold)
    done = [(r, e, insights[e.id]) for r, e in rows if e.id in insights and insights[e.id].status == "ok"][:5]
    if not done:
        return {"text": "Значимых событий для вашей компании пока нет.", "items": []}
    risks = [i for _, _, i in done if i.body["polarity"] == "risk"]
    opps = [i for _, _, i in done if i.body["polarity"] == "opportunity"]
    names = factor_names()
    top_factors = Counter(f for _, _, i in done for f in i.body["factors"]).most_common(3)
    parts = [f"Главное для вас: «{done[0][2].body['title']}»."]
    if risks:
        parts.append(f"Рисков в фокусе: {len(risks)}, ключевой — «{risks[0].body['title']}».")
    if opps:
        parts.append(f"Возможностей: {len(opps)}, например «{opps[0].body['title']}».")
    if top_factors:
        parts.append(
            "Чаще всего затронуты факторы: " + ", ".join(names.get(f, f) for f, _ in top_factors) + "."
        )
    bank = next(
        (i.body["sections"]["bank_view"][0]["text"] for _, _, i in done if i.body["sections"]["bank_view"]),
        None,
    )
    if bank:
        parts.append(f"Позиция банка: {bank}")
    return {
        "text": " ".join(parts),
        "items": [
            {"insight_id": i.id, "title": i.body["title"], "polarity": i.body["polarity"]} for _, _, i in done
        ],
    }


@router.get("/materials/{material_id}")
def material(material_id: str, user: AppUser = Depends(current_user), db: Session = Depends(get_db)):
    m = db.get(Material, material_id)
    if m is None or (m.submitted_by and m.submitted_by != user.login):
        err(404, "not_found", "Материал не найден")
    db.add(UIEvent(user=user.login, kind="open_source", ref=material_id))
    return {
        "id": m.id,
        "kind": m.kind,
        "source": m.source,
        "title": m.title,
        "text": m.text,
        "url": m.url,
        "report": m.report,
        "page": m.page,
        "synthetic": m.synthetic,
        "published_at": m.published_at.isoformat() if m.published_at else None,
    }


def _analyze(body: AnalyzeIn, user: AppUser, on_step=None) -> dict:
    if not body.text and not body.url:
        err(422, "empty", "Передайте текст или ссылку на новость")
    title, text, url = body.title or "", body.text or "", None
    if body.url and not body.text:
        try:
            title2, text = fetch_url(body.url)
            title, url = title or title2, body.url
        except UnsafeURL as exc:
            with session_scope() as db:
                audit(db, user.login, "analyze_url", "denied", str(exc))
            err(400, "unsafe_url", str(exc))
        except Exception as exc:
            err(400, "fetch_failed", f"Не удалось загрузить страницу: {exc}")
    if len(text.strip()) < 40:
        err(422, "too_short", "Текст новости слишком короткий для анализа")
    if not title:
        from app.processing.classify import strip_injection
        from app.processing.text import sentences

        clean, _ = strip_injection(text)
        first = (sentences(clean) or [clean])[0]
        title = first if len(first) <= 140 else first[:137].rsplit(" ", 1)[0] + "…"
    with session_scope() as db:
        m = add_material(
            db,
            kind="news",
            source="Добавлено пользователем",
            title=title,
            text=text,
            url=url,
            published_at=None,
            submitted_by=user.login,
        )
        if m.published_at is None:
            m.published_at = reference_time(db)
        if m.event_id and db.get(Event, m.event_id):
            ev = db.get(Event, m.event_id)
        else:
            ev, _ = process_material(db, m, private_to=user.login)
        if ev.classification.get("flags"):
            audit(db, user.login, "prompt_injection_suspected", "sanitized", m.id)
        if on_step:
            on_step(
                {
                    "kind": "ingest",
                    "name": "classify",
                    "output_summary": ", ".join(f["factor"] for f in ev.classification.get("factors", []))
                    or "факторы не найдены",
                }
            )
        company = db.get(Company, user.company_id)
        score, breakdown = score_event(ev, company.profile, user.role, reference_time(db))
        old = db.scalar(
            select(Relevance).where(
                Relevance.event_id == ev.id, Relevance.company_id == company.id, Relevance.role == user.role
            )
        )
        if old:
            db.delete(old)
        db.add(
            Relevance(event_id=ev.id, company_id=company.id, role=user.role, score=score, breakdown=breakdown)
        )
        db.flush()
        ins = run_agent(
            db,
            ev,
            company,
            user.role,
            user=user.login,
            relevance={**breakdown, "score": score},
            on_step=(lambda s: on_step(s.model_dump())) if on_step else None,
        )
        return insight_payload(db, ins)


@router.post("/analyze")
def analyze(body: AnalyzeIn, user: AppUser = Depends(current_user), stream: bool = False):
    limiter.check(user.login, "analyze", 20)
    if not stream:
        return _analyze(body, user)
    q: queue.Queue = queue.Queue()

    def worker():
        try:
            q.put(("result", _analyze(body, user, on_step=lambda s: q.put(("step", s)))))
        except HTTPException as exc:
            q.put(("error", exc.detail))
        except Exception as exc:
            q.put(("error", {"code": "failed", "message": str(exc)}))
        q.put(("end", None))

    threading.Thread(target=worker, daemon=True).start()

    def events():
        while True:
            kind, data = q.get()
            if kind == "end":
                break
            yield f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.post("/chat")
def chat(body: ChatIn, user: AppUser = Depends(current_user)):
    limiter.check(user.login, "chat", 60)
    with session_scope() as db:
        if body.insight_id:
            _own_insight(db, body.insight_id, user)
        return answer(db, user, body.message, body.insight_id, body.history)


@router.post("/feedback", status_code=204)
def feedback(body: FeedbackIn, user: AppUser = Depends(current_user), db: Session = Depends(get_db)):
    _own_insight(db, body.insight_id, user)
    db.add(
        Feedback(
            insight_id=body.insight_id,
            user=user.login,
            vote=body.vote,
            reason=body.reason,
            comment=body.comment,
        )
    )


@router.post("/ui-events", status_code=204)
def ui_event(body: UIEventIn, user: AppUser = Depends(current_user), db: Session = Depends(get_db)):
    db.add(UIEvent(user=user.login, kind=body.kind, ref=body.ref))


@router.get("/admin/metrics")
def metrics(user: AppUser = Depends(admin_user), db: Session = Depends(get_db)):
    from app.monitoring import collect_metrics

    return collect_metrics(db)


@router.get("/admin/audit")
def audit_log(user: AppUser = Depends(admin_user), db: Session = Depends(get_db), limit: int = 100):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.ts.desc()).limit(min(limit, 500))).all()
    return [
        {
            "ts": r.ts.isoformat(),
            "user": r.user,
            "action": r.action,
            "tool": r.tool,
            "result": r.result,
            "detail": r.detail,
        }
        for r in rows
    ]


@router.get("/admin/llm-calls")
def llm_calls(user: AppUser = Depends(admin_user), db: Session = Depends(get_db), limit: int = 100):
    rows = db.scalars(select(LLMCall).order_by(LLMCall.ts.desc()).limit(min(limit, 500))).all()
    return [
        {
            "ts": r.ts.isoformat(),
            "model": r.model,
            "purpose": r.purpose,
            "tokens_in": r.tokens_in,
            "tokens_out": r.tokens_out,
            "cost_rub": r.cost_rub,
            "cache_hit": r.cache_hit,
            "ok": r.ok,
            "mode": r.mode,
        }
        for r in rows
    ]
