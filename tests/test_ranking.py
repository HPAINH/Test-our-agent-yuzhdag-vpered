from app.eval import run


def test_offline_metrics_meet_targets():
    r = run()
    assert r["summary"]["precision@5_mean"] >= 0.8
    assert r["summary"]["ndcg@10_mean"] >= 0.75
    assert r["summary"]["divergence_min"] >= 0.6
    assert r["insights"]["multi_factor_share"] >= 0.9
    assert r["insights"]["faithfulness_checks_pass"] >= 0.95


def test_irrelevant_events_are_filtered():
    from sqlalchemy import select

    from app.db.models import Event, Relevance
    from app.db.session import session_scope

    with session_scope() as s:
        ev = s.scalar(select(Event).where(Event.title.like("%смартфон%")))
        if ev is None:
            return
        scores = s.scalars(select(Relevance.score).where(Relevance.event_id == ev.id)).all()
        assert ev.status == "unclassified" or max(scores) < 0.35
