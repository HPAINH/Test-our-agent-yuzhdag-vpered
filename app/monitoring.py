import json
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import ROOT
from app.db.models import (
    Company,
    Event,
    Feedback,
    HiddenEvent,
    Insight,
    LLMCall,
    Material,
    ServiceRequest,
    Trace,
    UIEvent,
)
from app.llm.client import get_llm


def collect_metrics(db: Session) -> dict:
    insights = db.scalars(select(Insight)).all()
    ok = [i for i in insights if i.status == "ok"]
    n = len(ok) or 1
    traces = {t.id: t for t in db.scalars(select(Trace))}
    durations = sorted(
        int((t.finished_at - t.started_at).total_seconds() * 1000) for t in traces.values() if t.finished_at
    )
    fb = db.scalars(select(Feedback)).all()
    ups = sum(1 for f in fb if f.vote > 0)
    since = datetime.utcnow() - timedelta(days=1)
    calls = db.scalars(select(LLMCall).where(LLMCall.ts >= since)).all()
    by_purpose = Counter(c.purpose for c in calls)
    by_model = Counter(c.model for c in calls)
    ui = Counter(e.kind for e in db.scalars(select(UIEvent)))
    eval_path = ROOT / "eval" / "results.json"
    data_quality = {}
    sample = db.scalar(select(Company).limit(1))
    if sample is not None:
        missing = Counter()
        companies = db.scalars(select(Company)).all()
        for c in companies:
            missing.update(c.profile.get("data_quality", {}).get("missing", []))
        data_quality = {
            "companies": len(companies),
            "large": sum(1 for c in companies if c.is_large),
            "large_by_group_revenue": sum(
                1
                for c in companies
                if c.is_large and c.profile.get("scale", {}).get("large_basis") == "group"
            ),
            "client_status": dict(
                Counter((c.profile.get("org") or {}).get("client_status", "unknown") for c in companies)
            ),
            "revenue_placeholders": sum(
                1 for c in companies if (c.profile.get("data_quality") or {}).get("revenue_placeholder")
            ),
            "missing_by_column": dict(missing.most_common(8)),
        }
    requests = db.scalars(select(ServiceRequest)).all()
    opened = ui.get("open_insight", 0)
    return {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "corpus": {
            "news": db.scalar(select(func.count(Material.id)).where(Material.kind == "news")),
            "alfa_chunks": db.scalar(select(func.count(Material.id)).where(Material.kind == "alfa")),
            "events": db.scalar(select(func.count(Event.id))),
            "unclassified_events": db.scalar(select(func.count(Event.id)).where(Event.status != "ok")),
        },
        "insights": {
            "total": len(insights),
            "by_status": dict(Counter(i.status for i in insights)),
            "verification_pass_rate": round(sum(1 for i in ok if i.body["verification"]["passed"]) / n, 3),
            "multi_factor_share": round(sum(1 for i in ok if len(i.body["factors"]) >= 2) / n, 3),
            "bank_view_coverage": round(sum(1 for i in ok if i.body["sections"]["bank_view"]) / n, 3),
            "avg_confidence": round(sum(i.body["confidence"] for i in ok) / n, 3),
            "avg_adapt_iterations": round(sum(i.body["verification"]["iterations"] for i in ok) / n, 2),
            "insufficient_share": round(
                sum(1 for i in insights if i.status == "insufficient_data") / max(1, len(insights)), 3
            ),
        },
        "agent": {
            "runs": len(traces),
            "median_ms": durations[len(durations) // 2] if durations else None,
            "p95_ms": durations[int(len(durations) * 0.95) - 1] if durations else None,
            "avg_cost_rub": round(sum(t.cost_rub for t in traces.values()) / max(1, len(traces)), 4),
            "avg_tokens": int(sum(t.tokens for t in traces.values()) / max(1, len(traces))),
        },
        "llm": {
            "budget": get_llm().budget_state(),
            "calls_24h": len(calls),
            "by_purpose": dict(by_purpose),
            "by_model": dict(by_model),
            "cache_hits_24h": sum(1 for c in calls if c.cache_hit),
            "errors_24h": sum(1 for c in calls if not c.ok),
        },
        "feedback": {
            "total": len(fb),
            "up_share": round(ups / len(fb), 3) if fb else None,
            "reasons": dict(Counter(f.reason for f in fb if f.vote < 0)),
            "recent_negative": [
                {"insight_id": f.insight_id, "reason": f.reason, "comment": f.comment, "ts": f.ts.isoformat()}
                for f in sorted(fb, key=lambda x: x.ts, reverse=True)
                if f.vote < 0
            ][:10],
        },
        "ui": dict(ui),
        "news_sources": _sources(),
        "product": {
            "impact_levels": dict(Counter((i.body.get("impact") or {}).get("level", "n/a") for i in ok)),
            "requests_total": len(requests),
            "requests_by_kind": dict(Counter(r.kind for r in requests)),
            "request_rate_per_open": round(len(requests) / opened, 3) if opened else None,
            "hidden_total": db.scalar(select(func.count()).select_from(HiddenEvent)),
            "hide_rate_per_open": round(ui.get("hide_event", 0) / opened, 3) if opened else None,
            "source_open_rate": round(ui.get("open_source", 0) / opened, 3) if opened else None,
        },
        "data_quality": data_quality,
        "offline_eval": json.loads(eval_path.read_text(encoding="utf-8")) if eval_path.exists() else None,
    }


def _sources() -> dict:
    from app.config import get_settings
    from app.ingest.news import SOURCE_STATUS
    from app.worker import LAST_CYCLE

    s = get_settings()
    return {
        "rss_enabled": s.rss_enabled,
        "refresh_min": s.news_refresh_min,
        "last_cycle": dict(LAST_CYCLE) or None,
        "sources": SOURCE_STATUS,
    }
