import logging
import threading
import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler

from app.config import get_settings
from app.db.session import session_scope
from app.ingest.news import fetch_rss
from app.seed import ensure_seeded, run_ai_stage

log = logging.getLogger("worker")


_lock = threading.Lock()
LAST_CYCLE: dict = {}
STATE = {"loading_data": False}


def cycle() -> None:
    if not _lock.acquire(blocking=False):
        log.info("cycle already running, skipped")
        return
    try:
        _cycle()
    finally:
        _lock.release()


def _cycle() -> None:
    settings = get_settings()
    t0 = time.perf_counter()
    stats = {}
    if settings.rss_enabled:
        with session_scope() as s:
            stats["rss_added"] = fetch_rss(s)
    stats.update(run_ai_stage(warm=True))
    stats["seconds"] = round(time.perf_counter() - t0, 1)
    stats["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    LAST_CYCLE.clear()
    LAST_CYCLE.update(stats)
    log.info("cycle done: %s", stats)


def bootstrap() -> None:
    STATE["loading_data"] = True
    try:
        ensure_seeded()
    finally:
        STATE["loading_data"] = False
    cycle()


def start_background(minutes: int, bootstrap: bool = True) -> BackgroundScheduler:
    sched = BackgroundScheduler(daemon=True)
    if minutes > 0:
        sched.add_job(cycle, "interval", minutes=minutes, max_instances=1, coalesce=True)
    sched.add_job(globals()["bootstrap"] if bootstrap else cycle, "date")
    sched.start()
    return sched


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ensure_seeded()
    cycle()
    sched = BlockingScheduler()
    sched.add_job(cycle, "interval", minutes=30, max_instances=1, coalesce=True)
    sched.start()


if __name__ == "__main__":
    main()
