import json
import re

from app.config import load_factor_rules, load_yaml
from app.llm.client import InvalidOutput, get_llm
from app.processing.text import sentences
from app.schemas import EventClassification, EventFact

TYPES = [
    "monetary",
    "fx",
    "commodity",
    "regulation",
    "sanctions",
    "industry",
    "company",
    "macro_stat",
    "other",
]


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def find_injection(text: str) -> list[str]:
    markers = load_factor_rules().get("injection_markers", [])
    found = []
    for sent in sentences(text):
        low = _norm(sent)
        if any(all(p in low for p in m.split()) for m in markers):
            found.append(sent)
    return found


def strip_injection(text: str) -> tuple[str, list[str]]:
    bad = find_injection(text)
    for b in bad:
        text = text.replace(b, " ")
    return re.sub(r"\s{2,}", " ", text).strip(), bad


def catalog_for_prompt() -> list[dict]:
    catalog = load_factor_rules().get("catalog", {})
    return [{"code": k, "name": v.get("name", k)} for k, v in catalog.items()]


def industries_for_prompt() -> list[dict]:
    industries = load_factor_rules().get("industries", {})
    return [{"okved": k, "name": v.get("name", k)} for k, v in industries.items()]


CLASSIFY_SYSTEM = """Ты — аналитик банка. Классифицируй деловую новость для подбора её клиентам — крупным компаниям.
Верни JSON строго по схеме:
{"type": один из %(types)s,
 "factors": [{"factor": код из каталога, "direction": "up"|"down"|"flat", "strength": 1|2|3}],
 "okved_sections": [префиксы ОКВЭД из справочника отраслей, которых новость касается напрямую],
 "regions": [регионы РФ из текста], "participants": [организации из текста], "dates": [даты из текста],
 "modality": "fact"|"forecast"|"opinion",
 "facts": [{"text": факт одним предложением своими словами, "quote": дословная цитата из текста новости}]}
Правила:
- Фактор — только если новость реально про него; не больше 5, самый важный первым. Пустой список, если новость
  не влияет на экономику компаний (спорт, культура, гаджеты).
- direction: up — показатель растёт (для курсов up = рубль слабеет; для ставки flat = сохранение);
  strength: 1 — упоминание, 2 — заметное изменение, 3 — резкое или рекордное.
- modality: fact — уже произошло; forecast — прогноз или планы; opinion — мнение.
- facts: 2–4 ключевых факта с числами и датами; quote копируй из текста дословно, без изменений.
Каталог факторов: %(catalog)s
Справочник отраслей: %(industries)s"""


def _validator(text: str):
    valid = set(load_factor_rules().get("catalog", {}))
    low = _norm(text)

    def check(result):
        if not isinstance(result, dict):
            raise InvalidOutput("ожидался JSON-объект")
        raw_facts = result.pop("facts", []) or []
        result["factors"] = [f for f in result.get("factors", []) if f.get("factor") in valid][:5]
        if result.get("type") not in TYPES:
            result["type"] = "other"
        try:
            cls = EventClassification.model_validate({**result, "method": "llm"})
        except Exception as exc:
            raise InvalidOutput(f"классификация не по схеме: {exc}") from exc
        facts = []
        for f in raw_facts:
            quote = (f.get("quote") or "").strip().strip('«»"')
            if quote and _norm(quote) in low and f.get("text"):
                facts.append({"text": f["text"].strip(), "quote": quote})
        if cls.factors and not facts:
            raise InvalidOutput("нет фактов с дословными цитатами из текста")
        return cls, facts

    return check


def classify(
    title: str, text: str, trace_id: str | None = None
) -> tuple[EventClassification, list[dict], dict]:
    text, injected = strip_injection(text)
    title, injected_title = strip_injection(title)
    injected += injected_title
    full = f"{title}. {text}"[:6000]
    system = CLASSIFY_SYSTEM % {
        "types": json.dumps(TYPES),
        "catalog": json.dumps(catalog_for_prompt(), ensure_ascii=False),
        "industries": json.dumps(industries_for_prompt(), ensure_ascii=False),
    }
    (cls, facts), meta = get_llm().complete(
        "classify",
        system,
        json.dumps({"title": title, "text": text[:6000]}, ensure_ascii=False),
        trace_id=trace_id,
        validate=_validator(full),
    )
    cls.method = f"llm:{meta.model}"
    if injected:
        cls.flags = ["prompt_injection_suspected"]
    return (
        cls,
        facts,
        {
            "injected": injected,
            "model": meta.model,
            "mode": meta.mode,
            "tokens": meta.tokens,
            "cost_rub": meta.cost_rub,
            "ms": meta.ms,
            "cache_hit": meta.cache_hit,
        },
    )


def to_facts(material_id: str, facts: list[dict], limit: int = 4) -> list[EventFact]:
    return [EventFact(text=f["text"], quote=f["quote"], material_id=material_id) for f in facts[:limit]]


def prefilter_score(title: str, text: str) -> int:
    rules = load_factor_rules()
    low = _norm(f"{title} {text}")
    words = set(re.findall(r"\w+", low))
    hits = 0
    for spec in rules.get("catalog", {}).values():
        for kw in spec.get("keywords", []):
            parts = _norm(kw).split()
            if all(any(w.startswith(p) for w in words) for p in parts):
                hits += 1
    for kws in rules.get("sector_keywords", {}).values():
        hits += sum(1 for kw in kws if _norm(kw) in low)
    return hits


def needs_llm(title: str, text: str) -> bool:
    return prefilter_score(title, text) >= load_yaml("sources.yaml").get("prefilter", {}).get("min_hits", 1)
