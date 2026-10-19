import csv
import json
import math
from collections import defaultdict

from sqlalchemy import select

from app.config import ROOT, get_settings
from app.db.models import Event, Insight, Relevance
from app.db.session import session_scope

GOLDEN = ROOT / "eval" / "golden.csv"
RESULTS = ROOT / "eval" / "results.json"


def load_golden() -> dict:
    labels: dict = defaultdict(dict)
    with open(GOLDEN, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            labels[(row["company_id"], row["role"])][f"m_{row['news_id']}"] = int(row["relevance"])
    return labels


def dcg(gains: list[int]) -> float:
    return sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(gains))


def ranking_metrics(session, labels: dict) -> dict:
    out = {}
    threshold = get_settings().feed_threshold
    for (company, role), gold in labels.items():
        rows = session.execute(
            select(Relevance, Event)
            .join(Event, Event.id == Relevance.event_id)
            .where(Relevance.company_id == company, Relevance.role == role, Event.private_to.is_(None))
            .order_by(Relevance.score.desc())
        ).all()
        ranked = []
        for rel, ev in rows:
            g = max((gold.get(m, 0) for m in ev.material_ids), default=0)
            ranked.append((rel.score, g, ev.title))
        gains = [g for _, g, _ in ranked]
        ideal = sorted(gains, reverse=True)
        top5 = gains[:5]
        feed = [g for s, g, _ in ranked if s >= threshold]
        relevant_total = sum(1 for g in gains if g >= 1)
        out[f"{company}/{role}"] = {
            "precision@5": round(sum(1 for g in top5 if g >= 1) / max(1, len(top5)), 3),
            "ndcg@10": round(dcg(gains[:10]) / (dcg(ideal[:10]) or 1), 3),
            "feed_precision": round(sum(1 for g in feed if g >= 1) / max(1, len(feed)), 3),
            "feed_recall": round(sum(1 for g in feed if g >= 1) / max(1, relevant_total), 3),
            "feed_size": len(feed),
            "top5": [t for _, _, t in ranked[:5]],
        }
    return out


def personalization(session) -> dict:
    tops = {}
    for company in ("U_DEMO_A", "U_DEMO_B", "U_DEMO_C"):
        tops[company] = {
            r.event_id
            for r in session.scalars(
                select(Relevance)
                .where(Relevance.company_id == company, Relevance.role == "cfo")
                .order_by(Relevance.score.desc())
                .limit(10)
            )
        }
    pairs = {}
    keys = list(tops)
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            union = tops[a] | tops[b]
            pairs[f"{a}~{b}"] = round(1 - len(tops[a] & tops[b]) / max(1, len(union)), 3)
    return pairs


def insight_quality(session) -> dict:
    items = session.scalars(select(Insight).where(Insight.status == "ok")).all()
    if not items:
        return {}
    n = len(items)
    passed = sum(1 for i in items if i.body["verification"]["passed"])
    faithful_checks = [c for i in items for c in i.body["verification"]["checks"] if c["n"] in (1, 2)]
    multi = sum(1 for i in items if len(i.body["factors"]) >= 2)
    feats = sum(
        1
        for i in items
        if len(
            {
                s["id"]
                for c in i.body["sections"]["why_it_matters"]
                for s in c["sources"]
                if s["type"] == "feature"
            }
        )
        >= 2
    )
    bank = sum(1 for i in items if i.body["sections"]["bank_view"])
    iters = sum(i.body["verification"]["iterations"] for i in items) / n
    return {
        "insights": n,
        "verification_pass_rate": round(passed / n, 3),
        "faithfulness_checks_pass": round(
            sum(c["pass"] for c in faithful_checks) / max(1, len(faithful_checks)), 3
        ),
        "multi_factor_share": round(multi / n, 3),
        "two_features_share": round(feats / n, 3),
        "bank_view_coverage": round(bank / n, 3),
        "avg_adapt_iterations": round(iters, 2),
    }


def run() -> dict:
    with session_scope() as s:
        result = {
            "ranking": ranking_metrics(s, load_golden()),
            "personalization_divergence": personalization(s),
            "insights": insight_quality(s),
        }
    rk = result["ranking"].values()
    result["summary"] = {
        "precision@5_mean": round(sum(r["precision@5"] for r in rk) / max(1, len(rk)), 3),
        "ndcg@10_mean": round(sum(r["ndcg@10"] for r in rk) / max(1, len(rk)), 3),
        "divergence_min": min(result["personalization_divergence"].values(), default=None),
    }
    RESULTS.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
