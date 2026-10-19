import logging
import math
import uuid
from collections import Counter
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Event, Material
from app.processing.classify import classify, extract_facts
from app.processing.text import BM25, best_sentence, stems
from app.schemas import EventClassification

log = logging.getLogger("pipeline")
CLUSTER_WINDOW = timedelta(hours=72)
CLUSTER_THRESHOLD = 0.45
ALFA_MIN_SCORE = 4.0


def cosine(a: Counter, b: Counter) -> float:
    common = set(a) & set(b)
    num = sum(a[t] * b[t] for t in common)
    den = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return num / den if den else 0.0


def material_vector(m: Material) -> Counter:
    return Counter(stems(f"{m.title} {m.title} {m.text}"))


def alfa_index(session: Session) -> tuple[BM25, dict[str, Material]]:
    chunks = {m.id: m for m in session.scalars(select(Material).where(Material.kind == "alfa"))}
    return BM25({cid: f"{m.title}\n{m.text}" for cid, m in chunks.items()}), chunks


def factor_query(cls: EventClassification) -> str:
    from app.config import load_factor_rules

    catalog = load_factor_rules().get("catalog", {})
    return " ".join(catalog.get(f.factor, {}).get("name", "") for f in cls.factors[:3])


def link_alfa(session: Session, event: Event, index: tuple[BM25, dict] | None = None) -> list[dict]:
    bm, chunks = index or alfa_index(session)
    cls = EventClassification.model_validate(event.classification)
    query = f"{event.title} {factor_query(cls)} " + " ".join(f["text"] for f in event.facts[:2])
    refs = []
    for cid, score in bm.search(query, top_k=6):
        if score < ALFA_MIN_SCORE:
            continue
        m = chunks[cid]
        if any(r["report"] == m.report for r in refs):
            continue
        refs.append(
            {
                "id": cid,
                "report": m.report,
                "page": m.page,
                "score": round(score, 2),
                "quote": best_sentence(m.text, query, 30),
            }
        )
        if len(refs) == 3:
            break
    return refs


def _event_for(
    session: Session, m: Material, cls: EventClassification, vec: Counter, cache: dict[str, Counter]
) -> Event | None:
    if m.published_at is None:
        return None
    top = cls.factors[0].factor if cls.factors else None
    q = select(Event).where(
        Event.private_to.is_(None),
        Event.published_at >= m.published_at - CLUSTER_WINDOW,
        Event.published_at <= m.published_at + CLUSTER_WINDOW,
    )
    best, best_sim = None, 0.0
    for ev in session.scalars(q):
        ev_top = (ev.classification.get("factors") or [{}])[0].get("factor")
        if top and ev_top and top != ev_top:
            continue
        if ev.id not in cache:
            mats = session.scalars(select(Material).where(Material.id.in_(ev.material_ids))).all()
            cache[ev.id] = sum((material_vector(x) for x in mats), Counter())
        sim = cosine(vec, cache[ev.id])
        if sim > best_sim:
            best, best_sim = ev, sim
    return best if best_sim >= CLUSTER_THRESHOLD else None


def process_material(
    session: Session,
    m: Material,
    *,
    private_to: str | None = None,
    index: tuple[BM25, dict] | None = None,
    cache: dict | None = None,
) -> tuple[Event, dict]:
    cls, meta = classify(m.title, m.text)
    vec = material_vector(m)
    cache = cache if cache is not None else {}
    ev = None if private_to else _event_for(session, m, cls, vec, cache)
    if ev is not None:
        ev.material_ids = [*ev.material_ids, m.id]
        facts = ev.facts + [
            f.model_dump() for f in extract_facts(m.id, m.title, m.text, cls.factors, limit=2)
        ]
        ev.facts = facts[:6]
        cache[ev.id] = cache.get(ev.id, Counter()) + vec
        m.event_id = ev.id
        return ev, {**meta, "merged": True}
    from app.processing.classify import strip_injection

    clean_title, _ = strip_injection(m.title)
    ev = Event(
        id=f"ev_{uuid.uuid4().hex[:10]}",
        title=clean_title or m.title,
        material_ids=[m.id],
        classification=cls.model_dump(),
        facts=[f.model_dump() for f in extract_facts(m.id, m.title, m.text, cls.factors)],
        published_at=m.published_at,
        status="ok" if cls.factors else "unclassified",
        private_to=private_to,
    )
    session.add(ev)
    session.flush()
    ev.alfa_refs = link_alfa(session, ev, index)
    m.event_id = ev.id
    cache[ev.id] = vec
    return ev, {**meta, "merged": False}


def process_pending(session: Session) -> dict:
    pending = session.scalars(
        select(Material)
        .where(Material.kind == "news", Material.event_id.is_(None), Material.submitted_by.is_(None))
        .order_by(Material.published_at)
    ).all()
    index = alfa_index(session)
    cache: dict = {}
    created = merged = 0
    for m in pending:
        try:
            _, meta = process_material(session, m, index=index, cache=cache)
        except Exception as exc:
            log.exception("processing failed for %s: %s", m.id, exc)
            continue
        merged += meta["merged"]
        created += not meta["merged"]
    session.flush()
    return {"processed": len(pending), "events_created": created, "merged": merged}
