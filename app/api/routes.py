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
from app.api.auth import NOT_CLIENT, act_as, admin_user, audit, current_user, issue_token, limiter
from app.api.launch import LaunchError, sign_launch, verify_launch
from app.config import get_settings, load_factor_rules, load_yaml
from app.db.models import (
    AppUser,
    AuditLog,
    Company,
    Event,
    Feedback,
    HiddenEvent,
    Insight,
    LLMCall,
    Material,
    Relevance,
    ServiceRequest,
    Trace,
    UIEvent,
)
from app.db.session import get_db, session_scope
from app.ingest.news import UnsafeURL, add_material, fetch_url
from app.llm.client import InvalidOutput, LLMError, LLMUnavailable, get_llm
from app.processing.pipeline import process_material
from app.profile.store import accessible_companies, set_factor_hint
from app.ranking.score import reference_time, score_event
from app.schemas import (
    AnalyzeIn,
    ChatIn,
    CompanySwitchIn,
    FactorHintIn,
    FeedbackIn,
    LaunchIn,
    LaunchLinkIn,
    LoginIn,
    ServiceRequestIn,
    UIEventIn,
)

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
        "companies": [
            company_brief(x) for x in (db.get(Company, cid) for cid in accessible_companies(db, u)) if x
        ],
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
        "impact": (b or {}).get("impact"),
        "hidden": False,
    }


def hidden_ids(db: Session, user: AppUser) -> set[str]:
    return set(
        db.scalars(
            select(HiddenEvent.event_id).where(
                HiddenEvent.login == user.login, HiddenEvent.company_id == user.company_id
            )
        )
    )


def _feed_rows(db: Session, user: AppUser, min_score: float, hidden: str = "exclude"):
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
    hide = hidden_ids(db, user)
    if hidden == "exclude":
        rows = [(r, e) for r, e in rows if e.id not in hide]
    elif hidden == "only":
        rows = [(r, e) for r, e in rows if e.id in hide]
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
    from app.ingest.ocr import ocr_available
    from app.worker import LAST_CYCLE

    pending = db.scalar(
        select(func.count(Material.id)).where(
            Material.kind == "news", Material.event_id.is_(None), Material.status.is_(None)
        )
    )
    return {
        "status": "ok" if ok_db else "degraded",
        "db": ok_db,
        "llm": get_llm().budget_state(),
        "ocr": ocr_available(),
        "pipeline": {"pending_news": pending, "last_cycle": dict(LAST_CYCLE) or None},
    }


@router.get("/auth/users")
def demo_users(db: Session = Depends(get_db)):
    return [user_dict(u, db) for u in db.scalars(select(AppUser).order_by(AppUser.company_id, AppUser.login))]


@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.get(AppUser, body.login)
    if user is None:
        audit(db, body.login, "login", "denied")
        err(401, "unknown_user", "Учётная запись не найдена")
    if not accessible_companies(db, user):
        audit(db, user.login, "login", "denied", "not_client")
        db.commit()
        err(403, NOT_CLIENT["code"], NOT_CLIENT["message"])
    user = act_as(db, user, None)
    audit(db, user.login, "login", "ok")
    return {"token": issue_token(user.login, user.company_id), "user": user_dict(user, db)}


@router.post("/auth/company")
def switch_company(
    body: CompanySwitchIn, user: AppUser = Depends(current_user), db: Session = Depends(get_db)
):
    if body.company_id not in accessible_companies(db, user):
        audit(db, user.login, "switch_company", "denied", body.company_id)
        db.commit()
        err(403, "forbidden", "Нет доступа к этой компании")
    user = act_as(db, user, body.company_id)
    audit(db, user.login, "switch_company", "ok", body.company_id)
    return {"token": issue_token(user.login, user.company_id), "user": user_dict(user, db)}


@router.post("/auth/launch")
def launch(body: LaunchIn, db: Session = Depends(get_db)):
    try:
        user, target, company_id = verify_launch(db, body.launch_token)
    except LaunchError as e:
        audit(db, None, "launch", "denied", e.code)
        db.commit()
        err(401, e.code, e.message)
    audit(db, user.login, "launch", "ok", target)
    user = act_as(db, user, company_id)
    return {"token": issue_token(user.login, user.company_id), "user": user_dict(user, db), "target": target}


@router.post("/host-demo/launch-link")
def host_demo_launch_link(body: LaunchLinkIn, db: Session = Depends(get_db)):
    if not get_settings().host_demo:
        err(404, "not_found", "Демо-хост выключен")
    user = db.get(AppUser, body.login)
    if user is None:
        err(404, "unknown_user", "Учётная запись не найдена")
    access = accessible_companies(db, user)
    if not access:
        err(403, NOT_CLIENT["code"], NOT_CLIENT["message"])
    company = body.company_id if body.company_id in access else access[0]
    token = sign_launch(user.login, company, body.target)
    return {"launch_token": token, "url": f"/#/launch?t={token}", "embed_url": f"/#/embed?t={token}"}


@router.get("/me")
def me(user: AppUser = Depends(current_user), db: Session = Depends(get_db)):
    return user_dict(user, db)


@router.get("/meta")
def meta(user: AppUser = Depends(current_user)):
    return {
        "factors": factor_names(),
        "roles": {k: v["name"] for k, v in load_yaml("roles.yaml")["roles"].items()},
        "llm_mode": get_llm().mode,
        "llm": get_llm().budget_state(),
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
        "summary_by_model": bool(c.summary),
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
    hidden: str = Query(default="exclude", pattern="^(exclude|only|include)$"),
    factor: str | None = None,
    kind: str | None = Query(default=None, pattern="^(risk|opportunity|mixed)$"),
    horizon: str | None = None,
    region: str | None = None,
    min_score: float | None = None,
    page: int = 1,
):
    rows, insights, mats = _feed_rows(
        db, user, min_score if min_score is not None else get_settings().feed_threshold, hidden
    )
    hide = hidden_ids(db, user)
    cards = [{**card(e, r, insights.get(e.id), mats), "hidden": e.id in hide} for r, e in rows]
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
        "disclaimer": load_yaml("interpretation.yaml")["disclaimer"],
        "impact_scale": {
            k: {"label": v["label"], "meaning": v["meaning"]}
            for k, v in load_yaml("interpretation.yaml")["impact_scale"].items()
        },
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
        return {"text": "Значимых событий для вашей компании пока нет.", "items": [], "mode": None}
    items = [
        {
            "insight_id": i.id,
            "title": i.body["title"],
            "summary": i.body["summary"],
            "polarity": i.body["polarity"],
            "impact": (i.body.get("impact") or {}).get("level"),
            "bank_view": [c["text"] for c in i.body["sections"]["bank_view"][:1]],
        }
        for _, _, i in done
    ]
    roles = load_yaml("roles.yaml")["roles"]

    def check(result):
        if not isinstance(result, dict) or not str(result.get("text") or "").strip():
            raise InvalidOutput("нет текста сводки")
        return str(result["text"]).strip()

    if not get_llm().available:
        return {
            "text": None,
            "items": [{k: x[k] for k in ("insight_id", "title", "polarity")} for x in items],
            "mode": "off",
        }
    text, meta = get_llm().complete(
        "digest",
        DIGEST_SYSTEM,
        json.dumps(
            {"role": roles.get(user.role, {}).get("name", user.role), "insights": items}, ensure_ascii=False
        ),
        validate=check,
    )
    return {
        "text": text,
        "items": [{k: x[k] for k in ("insight_id", "title", "polarity")} for x in items],
        "mode": meta.mode,
    }


DIGEST_SYSTEM = """Составь сводку дня для руководителя по готовым инсайтам его компании: 3–5 предложений,
главное первым, сколько рисков и возможностей, что сделать сегодня. Только по данным из insights, без новых фактов.
Позицию банка упоминай только если она есть в bank_view. Верни JSON: {"text": "..."}"""


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
    if not body.text and not body.url and not body.file_b64:
        err(422, "empty", "Передайте текст, ссылку или файл с новостью")
    title, text, url = body.title or "", body.text or "", None
    if body.file_b64 and not body.text:
        import base64
        import binascii

        from app.ingest.ocr import document_text

        try:
            data = base64.b64decode(body.file_b64, validate=True)
        except (binascii.Error, ValueError):
            err(422, "bad_file", "Файл повреждён")
        try:
            text, method = document_text(body.file_name or "file.pdf", data)
        except (ValueError, RuntimeError) as exc:
            err(422, "bad_file", str(exc))
        if on_step:
            on_step(
                {
                    "kind": "ingest",
                    "name": "ocr" if method == "ocr" else "extract",
                    "output_summary": f"извлечено {len(text)} символов ({'OCR' if method == 'ocr' else 'текстовый слой'})",
                }
            )
    if body.url and not body.text and not body.file_b64:
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
    if not get_llm().available:
        raise LLMUnavailable("модели не подключены")
    if not stream:
        return _analyze(body, user)
    q: queue.Queue = queue.Queue()

    def worker():
        try:
            q.put(("result", _analyze(body, user, on_step=lambda s: q.put(("step", s)))))
        except HTTPException as exc:
            q.put(("error", exc.detail))
        except LLMUnavailable:
            q.put(("error", {"code": "llm_unavailable", "message": "Модели не подключены"}))
        except LLMError as exc:
            q.put(("error", {"code": "llm_failed", "message": str(exc)[:300]}))
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


@router.post("/events/{event_id}/hide", status_code=204)
def hide_event(event_id: str, user: AppUser = Depends(current_user), db: Session = Depends(get_db)):
    ev = db.get(Event, event_id)
    if ev is None or not visible_event(ev, user):
        err(404, "not_found", "Событие не найдено")
    if db.get(HiddenEvent, (user.login, user.company_id, event_id)) is None:
        db.add(HiddenEvent(login=user.login, company_id=user.company_id, event_id=event_id))
    db.add(UIEvent(user=user.login, kind="hide_event", ref=event_id))


@router.delete("/events/{event_id}/hide", status_code=204)
def unhide_event(event_id: str, user: AppUser = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(HiddenEvent, (user.login, user.company_id, event_id))
    if row is not None:
        db.delete(row)
    db.add(UIEvent(user=user.login, kind="unhide_event", ref=event_id))


REQUEST_MESSAGES = {
    "manager": "Запрос передан вашему клиентскому менеджеру. Он свяжется с вами в рабочее время.",
    "callback": "Заявка на обратный звонок принята. Менеджер перезвонит в рабочее время.",
    "application": "Заявка принята. Менеджер уточнит условия и свяжется с вами.",
}


@router.post("/requests", status_code=201)
def create_request(
    body: ServiceRequestIn, user: AppUser = Depends(current_user), db: Session = Depends(get_db)
):
    limiter.check(user.login, "requests", 20)
    if body.insight_id:
        _own_insight(db, body.insight_id, user)
    r = ServiceRequest(
        login=user.login,
        company_id=user.company_id,
        insight_id=body.insight_id,
        kind=body.kind,
        product=body.product,
        comment=body.comment,
    )
    db.add(r)
    db.flush()
    audit(db, user.login, "service_request", "ok", f"{body.kind}:{body.product or ''}")
    return {
        "id": r.id,
        "status": r.status,
        "message": REQUEST_MESSAGES[body.kind],
        "simulated": True,
    }


@router.get("/requests")
def my_requests(user: AppUser = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(ServiceRequest)
        .where(ServiceRequest.login == user.login, ServiceRequest.company_id == user.company_id)
        .order_by(ServiceRequest.ts.desc())
        .limit(50)
    ).all()
    return [
        {
            "id": r.id,
            "ts": r.ts.isoformat(),
            "kind": r.kind,
            "product": r.product,
            "insight_id": r.insight_id,
            "status": r.status,
        }
        for r in rows
    ]


@router.put("/companies/me/factors/{factor}")
def factor_hint(
    factor: str, body: FactorHintIn, user: AppUser = Depends(current_user), db: Session = Depends(get_db)
):
    from app.profile.store import company_role_pairs
    from app.ranking.score import rank_company

    limiter.check(user.login, "factor_hint", 60)
    company = db.get(Company, user.company_id)
    try:
        hints = set_factor_hint(db, company, factor, body.mode)
    except KeyError:
        err(404, "unknown_factor", "Такого фактора нет в каталоге")
    roles = sorted(company_role_pairs(db).get(company.id, {user.role}))
    rank_company(db, company, roles)
    audit(db, user.login, "factor_hint", "ok", f"{factor}:{body.mode}")
    return {"user_hints": hints, "profile_version": company.profile_version}


@router.post("/admin/news/refresh", status_code=202)
def refresh_news(user: AppUser = Depends(admin_user)):
    import threading

    from app.worker import cycle

    threading.Thread(target=cycle, daemon=True).start()
    return {"started": True, "rss_enabled": get_settings().rss_enabled}


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
