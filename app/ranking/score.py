import math
from collections import Counter
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import load_factor_rules, load_yaml
from app.db.models import AppUser, Company, Event, Relevance
from app.processing.pipeline import cosine
from app.processing.text import stems

WEIGHTS = {"F": 0.40, "I": 0.20, "R": 0.10, "B": 0.10, "T": 0.10, "E": 0.10}
HALF_LIFE_DAYS = 3.0


def reference_time(session: Session) -> datetime:
    latest = session.scalar(select(func.max(Event.published_at)).where(Event.private_to.is_(None)))
    now = datetime.utcnow()
    return max(latest, now) if latest and latest > now else (latest or now)


def profile_vector(profile: dict) -> Counter:
    names = load_factor_rules().get("catalog", {})
    text = " ".join(
        [
            profile["industry"].get("name") or "",
            profile["industry"].get("sector_name") or "",
            profile["geo"].get("region") or "",
            *[names.get(f["factor"], {}).get("name", "") for f in profile.get("factors", [])[:6]],
            *[cp.get("okved_name") or "" for cp in profile.get("counterparties", [])[:4]],
        ]
    )
    return Counter(stems(text))


def score_event(
    event: Event,
    profile: dict,
    role: str,
    ref: datetime,
    pvec: Counter | None = None,
    ev_vec: Counter | None = None,
) -> tuple[float, dict]:
    roles = load_yaml("roles.yaml")["roles"]
    mult = roles.get(role, {}).get("multipliers", {})
    cls = event.classification or {}
    cfactors = {f["factor"]: f for f in profile.get("factors", [])}
    contributions = []
    f_raw = 0.0
    for eff in cls.get("factors", []):
        cf = cfactors.get(eff["factor"])
        if not cf:
            continue
        m = mult.get(eff["factor"], 1.0)
        val = cf["weight"] * m * eff.get("strength", 1) / 3
        f_raw += val
        contributions.append(
            {
                "factor": eff["factor"],
                "company_weight": cf["weight"],
                "role_multiplier": m,
                "strength": eff.get("strength", 1),
                "direction": eff.get("direction"),
                "value": round(val, 3),
                "drivers": cf["drivers"],
                "polarity": cf["polarity"],
            }
        )
    F = 1 - math.exp(-1.6 * f_raw)
    sections = set(cls.get("okved_sections", []))
    okved = profile["industry"].get("okved") or ""
    prefix = profile["industry"].get("prefix") or okved[:2]
    I, i_why = 0.0, None
    if any(okved.startswith(s) or prefix == s for s in sections):
        I, i_why = 1.0, f"отрасль компании ({okved})"
    else:
        for cp in profile.get("counterparties", []):
            if cp.get("okved") and any(cp["okved"].startswith(s) for s in sections) and cp["share"] >= 0.1:
                I, i_why = (
                    0.6,
                    f"отрасль контрагента {cp['company_id']} ({cp['okved']}, {round(cp['share'] * 100)}%)",
                )
                break
    regions = set(cls.get("regions", []))
    R, r_why = 0.0, None
    if profile["geo"].get("region") in regions:
        R, r_why = 1.0, f"регион компании ({profile['geo']['region']})"
    elif any(cp.get("region") in regions for cp in profile.get("counterparties", [])):
        R, r_why = 0.5, "регион контрагента"
    B = min(1.0, len(event.alfa_refs or []) / 2) if event.alfa_refs else 0.0
    age = max(0.0, (ref - event.published_at).total_seconds() / 86400) if event.published_at else 30
    T = math.exp(-math.log(2) * age / HALF_LIFE_DAYS)
    E = cosine(
        pvec or profile_vector(profile),
        ev_vec or Counter(stems(event.title + " " + " ".join(f["text"] for f in event.facts))),
    )
    E = min(1.0, E * 3)
    parts = {"F": F, "I": I, "R": R, "B": B, "T": T, "E": E}
    score = sum(WEIGHTS[k] * v for k, v in parts.items())
    if not contributions:
        score *= 0.5
    breakdown = {k: round(v, 3) for k, v in parts.items()}
    breakdown.update(
        {
            "contributions": sorted(contributions, key=lambda x: -x["value"]),
            "industry_match": i_why,
            "region_match": r_why,
            "alfa_refs": len(event.alfa_refs or []),
            "age_days": round(age, 1),
            "weights": WEIGHTS,
        }
    )
    return round(score, 4), breakdown


def rank_company(
    session: Session, company: Company, roles: list[str], events: list[Event] | None = None
) -> int:
    ref = reference_time(session)
    events = (
        events
        if events is not None
        else session.scalars(select(Event).where(Event.private_to.is_(None), Event.status == "ok")).all()
    )
    pvec = profile_vector(company.profile)
    session.execute(
        delete(Relevance).where(
            Relevance.company_id == company.id, Relevance.event_id.in_([e.id for e in events])
        )
    )
    n = 0
    for ev in events:
        ev_vec = Counter(stems(ev.title + " " + " ".join(f["text"] for f in ev.facts)))
        for role in roles:
            s, b = score_event(ev, company.profile, role, ref, pvec, ev_vec)
            session.add(Relevance(event_id=ev.id, company_id=company.id, role=role, score=s, breakdown=b))
            n += 1
    session.flush()
    return n


def rank_all(session: Session) -> int:
    pairs: dict[str, set] = {}
    for u in session.scalars(select(AppUser)):
        pairs.setdefault(u.company_id, set()).add(u.role)
    n = 0
    for cid, roles in pairs.items():
        company = session.get(Company, cid)
        if company:
            n += rank_company(session, company, sorted(roles))
    return n


def top_events(
    session: Session,
    company_id: str,
    role: str,
    threshold: float,
    limit: int = 50,
    diversity: int | None = None,
) -> list[tuple[Relevance, Event]]:
    rows = session.execute(
        select(Relevance, Event)
        .join(Event, Event.id == Relevance.event_id)
        .where(Relevance.company_id == company_id, Relevance.role == role, Relevance.score >= threshold)
        .order_by(Relevance.score.desc())
    ).all()
    if diversity is None:
        return [(r, e) for r, e in rows][:limit]
    picked, per_type = [], Counter()
    for r, e in rows:
        t = (e.classification or {}).get("type", "other")
        if per_type[t] >= diversity:
            continue
        per_type[t] += 1
        picked.append((r, e))
        if len(picked) >= limit:
            break
    return picked
