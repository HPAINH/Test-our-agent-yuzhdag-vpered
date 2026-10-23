from pathlib import Path

import yaml
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.config import load_factor_rules
from app.db.models import AppUser, Company, CompanyFactor, CompanyLink, Insight, UserCompany
from app.profile.builder import build_profiles
from app.profile.loader import load_raw


def load_companies(session: Session, data_dir: Path) -> dict:
    result = build_profiles(load_raw(data_dir))
    profiles = result["profiles"]
    session.execute(delete(CompanyFactor))
    session.execute(delete(CompanyLink))
    existing = {c.id: c for c in session.scalars(select(Company))}
    fresh: list[dict] = []
    for cid, p in profiles.items():
        p.pop("raw", None)
        c = existing.get(cid)
        hints = (c.profile or {}).get("user_hints") if c is not None else None
        if hints:
            p["user_hints"] = hints
            p["factors_base"] = p["factors"]
            p["factors"] = apply_hints(p["factors"], hints)
        fields = {
            "profile": p,
            "is_large": p["is_large"],
            "group_id": p["group_id"],
            "okved": p["industry"]["okved"],
            "revenue_rub": p["scale"]["revenue_rub"],
        }
        if c is None:
            fresh.append({"id": cid, "raw": {}, "profile_version": 1, **fields})
            continue
        if c.profile and c.profile != p:
            c.profile_version += 1
            session.query(Insight).filter(Insight.company_id == cid).update({"stale": True})
        for k, v in fields.items():
            setattr(c, k, v)
    for i in range(0, len(fresh), 2000):
        session.execute(insert(Company), fresh[i : i + 2000])
    session.flush()
    person_links = [
        {
            "src": link["src"],
            "dst": link["dst"],
            "kind": link["kind"],
            "amount": link.get("amount", 0),
            "count": link.get("count", 0),
            "via": link.get("via"),
        }
        for link in result["links"]
        if link["kind"] == "person"
    ]
    if person_links:
        session.execute(insert(CompanyLink), person_links)
    session.flush()
    return {"companies": len(profiles), "links": len(result["links"]), "report": result["report"]}


HINT_BOOST = 0.3
HINT_NEW_WEIGHT = 0.5


def apply_hints(base: list[dict], hints: dict) -> list[dict]:
    out = []
    seen = set()
    for f in base:
        mode = hints.get(f["factor"])
        seen.add(f["factor"])
        if mode == "mute":
            continue
        if mode == "boost":
            f = {
                **f,
                "weight": round(min(1.0, f["weight"] + HINT_BOOST), 3),
                "drivers": [*f["drivers"], "USER_INPUT=boost"],
            }
        out.append(f)
    for factor, mode in hints.items():
        if mode == "boost" and factor not in seen:
            out.append(
                {
                    "factor": factor,
                    "weight": HINT_NEW_WEIGHT,
                    "polarity": "mixed",
                    "drivers": ["USER_INPUT=boost"],
                }
            )
    return sorted(out, key=lambda x: -x["weight"])


def factor_rows(company_id: str, factors: list[dict]) -> list[dict]:
    return [
        {
            "company_id": company_id,
            "factor": f["factor"],
            "weight": f["weight"],
            "polarity": f["polarity"],
            "drivers": f["drivers"],
        }
        for f in factors
    ]


def write_factors(session: Session, company_id: str, factors: list[dict]) -> None:
    session.execute(delete(CompanyFactor).where(CompanyFactor.company_id == company_id))
    if factors:
        session.execute(insert(CompanyFactor), factor_rows(company_id, factors))


def set_factor_hint(session: Session, company: Company, factor: str, mode: str) -> dict:
    if factor not in load_factor_rules().get("catalog", {}):
        raise KeyError(factor)
    p = dict(company.profile)
    hints = dict(p.get("user_hints") or {})
    if mode == "reset":
        hints.pop(factor, None)
    else:
        hints[factor] = mode
    base = p.get("factors_base") or p.get("factors", [])
    p["factors_base"] = base
    p["user_hints"] = hints
    p["factors"] = apply_hints(base, hints)
    company.profile = p
    company.profile_version += 1
    write_factors(session, company.id, p["factors"])
    session.query(Insight).filter(Insight.company_id == company.id).update({"stale": True})
    session.flush()
    return hints


ACCESS_DENIED_STATUSES = ("prospect", "closed")


def accessible_companies(session: Session, user: AppUser) -> list[str]:
    extra = session.scalars(select(UserCompany.company_id).where(UserCompany.login == user.login)).all()
    out = []
    for cid in [user.company_id, *[c for c in extra if c != user.company_id]]:
        company = session.get(Company, cid)
        if company is None:
            continue
        if (company.profile.get("org") or {}).get("client_status") in ACCESS_DENIED_STATUSES:
            continue
        out.append(cid)
    return out


def company_role_pairs(session: Session) -> dict[str, set]:
    pairs: dict[str, set] = {}
    for u in session.scalars(select(AppUser)):
        for cid in accessible_companies(session, u):
            pairs.setdefault(cid, set()).add(u.role)
    return pairs


DEMO_ROLES = [("cfo", "Финансовый директор"), ("ceo", "Генеральный директор"), ("treasurer", "Казначей")]


def demo_users(session: Session, n_companies: int = 3) -> list[dict]:
    companies = [
        c
        for c in session.scalars(select(Company))
        if (c.profile.get("org") or {}).get("client_status") == "client" and c.revenue_rub
    ]
    companies.sort(key=lambda c: -(c.profile["scale"].get("large_basis_revenue_rub") or c.revenue_rub or 0))
    picked, sectors = [], set()
    for c in companies:
        sector = c.profile["industry"].get("sector")
        if sector in sectors:
            continue
        picked.append(c)
        sectors.add(sector)
        if len(picked) == n_companies:
            break
    users = []
    for i, c in enumerate(picked):
        for role, title in DEMO_ROLES[: 2 if i else 3]:
            users.append(
                {
                    "login": f"{role}_{i + 1}",
                    "display_name": f"{title} · {c.id}",
                    "company_id": c.id,
                    "role": role,
                    "is_admin": i == 0 and role == "cfo",
                }
            )
    return users


def load_users(session: Session, data_dir: Path) -> int:
    path = data_dir / "users.yaml"
    if path.exists():
        users = yaml.safe_load(path.read_text(encoding="utf-8")).get("users", [])
    else:
        users = demo_users(session)
    n = 0
    for u in users:
        if session.get(Company, u["company_id"]) is None:
            continue
        session.merge(
            AppUser(
                login=u["login"],
                display_name=u.get("display_name", u["login"]),
                company_id=u["company_id"],
                role=u["role"],
                is_admin=bool(u.get("is_admin")),
            )
        )
        session.execute(delete(UserCompany).where(UserCompany.login == u["login"]))
        for cid in u.get("companies") or []:
            if cid != u["company_id"] and session.get(Company, cid) is not None:
                session.add(UserCompany(login=u["login"], company_id=cid))
        n += 1
    session.flush()
    return n
