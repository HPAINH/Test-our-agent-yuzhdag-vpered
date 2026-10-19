import logging
import time

from apscheduler.schedulers.blocking import BlockingScheduler

from app.config import get_settings
from app.db.session import session_scope
from app.ingest.news import fetch_rss
from app.processing.pipeline import process_pending
from app.ranking.score import rank_all
from app.seed import ensure_seeded, warm_insights

log = logging.getLogger("worker")


def cycle() -> None:
    settings = get_settings()
    t0 = time.perf_counter()
    stats = {}
    if settings.rss_enabled:
        with session_scope() as s:
            stats["rss_added"] = fetch_rss(s)
    with session_scope() as s:
        stats["pipeline"] = process_pending(s)
    with session_scope() as s:
        stats["relevance_rows"] = rank_all(s)
    stats["insights"] = warm_insights()
    log.info("cycle done in %.1fs: %s", time.perf_counter() - t0, stats)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ensure_seeded()
    cycle()
    sched = BlockingScheduler()
    sched.add_job(cycle, "interval", minutes=30, max_instances=1, coalesce=True)
    sched.start()


if __name__ == "__main__":
    main()
