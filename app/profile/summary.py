import json

from app.config import load_factor_rules
from app.db.models import Company
from app.llm.client import InvalidOutput, get_llm

SUMMARY_SYSTEM = """Опиши бизнес-контекст компании для аналитика банка в 3–4 предложениях: чем занимается,
масштаб, география, связи (группа, крупные контрагенты по платежам, ВЭД), к каким внешним факторам чувствительна и
почему. Только по данным профиля; если данных нет — так и скажи. Не называй платёжных контрагентов поставщиками или
покупателями: платёж показывает только направление денег. Верни JSON: {"text": "..."}"""


def summarize(company: Company) -> str:
    p = company.profile
    names = {k: v.get("name", k) for k, v in load_factor_rules().get("catalog", {}).items()}
    payload = {
        "industry": p["industry"],
        "scale": p["scale"],
        "geo": p["geo"],
        "org": p["org"],
        "products": p["products"],
        "foreign_trade": p["foreign_trade"],
        "group": {k: (p.get("group") or {}).get(k) for k in ("group_id", "member_count", "sectors")},
        "counterparties": [
            {k: c.get(k) for k in ("direction", "share", "okved_name", "same_group", "known")}
            for c in p.get("counterparties", [])[:6]
        ],
        "factors": [
            {"name": names.get(f["factor"], f["factor"]), "weight": f["weight"], "drivers": f["drivers"]}
            for f in p.get("factors", [])[:8]
        ],
        "data_quality": p.get("data_quality"),
    }

    def check(result):
        if not isinstance(result, dict) or len(str(result.get("text") or "").strip()) < 40:
            raise InvalidOutput("нет описания компании")
        return str(result["text"]).strip()

    text, _ = get_llm().complete(
        "profile_summary", SUMMARY_SYSTEM, json.dumps(payload, ensure_ascii=False), validate=check
    )
    return text
