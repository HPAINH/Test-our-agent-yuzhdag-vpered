import argparse
import logging
import time

from sqlalchemy import func, select

from app.config import ROOT, get_settings
from app.db.models import AppUser, Company, Event, Insight, Material
from app.db.session import init_db, reset_db, session_scope
from app.ingest.alfa import load_alfa_dir
from app.ingest.news import fetch_rss, load_news_dir
from app.processing.pipeline import process_pending
from app.profile.store import load_companies, load_users
from app.ranking.score import rank_all, top_events

log = logging.getLogger("seed")


def warm_insights(limit: int | None = None) -> int:
    from app.agent.orchestrator import run_agent

    settings = get_settings()
    limit = limit or settings.agent_top_n
    n = 0
    with session_scope() as s:
        pairs = {(u.company_id, u.role) for u in s.scalars(select(AppUser))}
    for company_id, role in sorted(pairs):
        with session_scope() as s:
            company = s.get(Company, company_id)
            existing = {
                i.event_id
                for i in s.scalars(
                    select(Insight).where(
                        Insight.company_id == company_id, Insight.role == role, Insight.stale.is_(False)
                    )
                )
            }
            for rel, ev in top_events(s, company_id, role, settings.feed_threshold, limit=limit, diversity=2):
                if ev.id in existing:
                    continue
                run_agent(s, ev, company, role, user=None, relevance={**rel.breakdown, "score": rel.score})
                n += 1
    return n


def seed(reset: bool = False, warm: bool = True) -> dict:
    settings = get_settings()
    t0 = time.perf_counter()
    if reset:
        reset_db()
    else:
        init_db()
    stats: dict = {}
    with session_scope() as s:
        if (
            ROOT / "data_synthetic" / "df_Uid_data_hack.csv"
        ).exists() is False and settings.data_path.name == "data_synthetic":
            from app.synthetic import generate

            generate()
        stats["companies"] = load_companies(s, settings.data_path)
        stats["users"] = load_users(s, settings.data_path)
        if settings.alfa_path.exists():
            stats["alfa_chunks"] = load_alfa_dir(
                s, settings.alfa_path, synthetic="synthetic" in str(settings.alfa_path)
            )
        if settings.news_path.exists():
            stats["news"] = load_news_dir(s, settings.news_path)
        if settings.rss_enabled:
            stats["rss"] = fetch_rss(s)
    with session_scope() as s:
        stats["pipeline"] = process_pending(s)
    with session_scope() as s:
        stats["relevance_rows"] = rank_all(s)
    if warm:
        stats["insights"] = warm_insights()
    with session_scope() as s:
        stats["totals"] = {
            "materials": s.scalar(select(func.count(Material.id))),
            "events": s.scalar(select(func.count(Event.id))),
            "insights": s.scalar(select(func.count(Insight.id))),
        }
    stats["seconds"] = round(time.perf_counter() - t0, 1)
    stats["companies"].pop("report", None)
    return stats


def ensure_seeded() -> None:
    init_db()
    with session_scope() as s:
        has_users = s.scalar(select(func.count(AppUser.login))) or 0
    if not has_users:
        log.info("empty database: seeding")
        log.info("seed: %s", seed(reset=False, warm=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--no-warm", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    print(seed(reset=args.reset, warm=not args.no_warm))
