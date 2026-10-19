from pathlib import Path

import yaml
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import AppUser, Company, CompanyFactor, CompanyLink, Insight
from app.profile.builder import build_profiles
from app.profile.loader import load_raw


def load_companies(session: Session, data_dir: Path) -> dict:
    result = build_profiles(load_raw(data_dir))
    profiles = result["profiles"]
    session.execute(delete(CompanyFactor))
    session.execute(delete(CompanyLink))
    existing = {c.id: c for c in session.scalars(select(Company))}
    for cid, p in profiles.items():
        raw = p.pop("raw")
        factors = p["factors"]
        c = existing.get(cid)
        if c is None:
            c = Company(id=cid)
            session.add(c)
        elif c.profile and c.profile != p:
            c.profile_version += 1
            session.query(Insight).filter(Insight.company_id == cid).update({"stale": True})
        c.raw = {k: (None if v == "" else v) for k, v in raw.items()}
        c.profile = p
        c.is_large = p["is_large"]
        c.group_id = p["group_id"]
        c.okved = p["industry"]["okved"]
        c.revenue_rub = p["scale"]["revenue_rub"]
        for f in factors:
            session.add(
                CompanyFactor(
                    company_id=cid,
                    factor=f["factor"],
                    weight=f["weight"],
                    polarity=f["polarity"],
                    drivers=f["drivers"],
                )
            )
    for link in result["links"]:
        session.add(
            CompanyLink(
                src=link["src"],
                dst=link["dst"],
                kind=link["kind"],
                amount=link.get("amount", 0),
                count=link.get("count", 0),
                via=link.get("via"),
            )
        )
    session.flush()
    return {"companies": len(profiles), "links": len(result["links"]), "report": result["report"]}


def load_users(session: Session, data_dir: Path) -> int:
    path = data_dir / "users.yaml"
    if not path.exists():
        return 0
    users = yaml.safe_load(path.read_text(encoding="utf-8")).get("users", [])
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
        n += 1
    session.flush()
    return n
