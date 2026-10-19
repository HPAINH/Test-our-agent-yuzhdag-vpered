import json
import re
from collections import Counter

from app.config import load_factor_rules
from app.llm.client import get_llm
from app.processing.text import sentences, tokens
from app.schemas import EventClassification, EventFact, FactorEffect

FX_UP = [
    "ослаб рубл",
    "рубл ослаб",
    "курс юан вырос",
    "курс доллар вырос",
    "подорожа юан",
    "подорожа доллар",
    "вырос до",
]
FX_DOWN = ["укреп рубл", "рубл укреп", "подешевел юан", "подешевел доллар"]
TYPE_BY_FACTOR = {
    "key_rate": "monetary",
    "bonds": "monetary",
    "fx_usd": "fx",
    "fx_cny": "fx",
    "fx_eur": "fx",
    "cmd_oil": "commodity",
    "cmd_metals": "commodity",
    "cmd_coal": "commodity",
    "cmd_gold": "commodity",
    "cmd_agro": "commodity",
    "regulation": "regulation",
    "sanctions_trade": "sanctions",
    "inflation": "macro_stat",
    "labor": "macro_stat",
    "consumer_demand": "macro_stat",
    "real_estate": "industry",
    "logistics": "industry",
    "region": "industry",
}
DATE_RE = re.compile(
    r"\b\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)(?:\s+\d{4})?"
    r"|\b\d{2}\.\d{2}\.\d{4}\b"
)


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def phrase_in(toks: list[str], phrase: str) -> bool:
    parts = [p for p in _norm(phrase).split() if p]
    return all(any(t.startswith(p) for t in toks) for p in parts)


def _sent_tokens(text: str) -> list[list[str]]:
    return [[_norm(t) for t in tokens(s)] for s in sentences(text)]


def _direction(factor: str, sents: list[list[str]], keywords: list[str], rules: dict) -> str:
    dirs = rules.get("directions", {})
    extra = rules.get("catalog", {}).get(factor, {}).get("dir", {}) or {}
    heavy = rules.get("heavy_markers", [])
    price_words = rules.get("price_words", [])
    votes: Counter = Counter()
    for idx, toks in enumerate(sents):
        if not any(phrase_in(toks, k) for k in keywords):
            continue
        weight = 3 if idx == 0 else 1
        if factor.startswith("fx_"):
            for ph in FX_UP:
                votes["up"] += weight * phrase_in(toks, ph)
            for ph in FX_DOWN:
                votes["down"] += weight * phrase_in(toks, ph)
            continue
        if factor.startswith("cmd_") and not any(phrase_in(toks, w) for w in price_words):
            continue
        for d in ("flat", "up", "down"):
            for ph in dirs.get(d, []):
                if phrase_in(toks, ph):
                    votes[d] += weight * (2 if ph in heavy else 1)
            for ph in extra.get(d, []):
                if phrase_in(toks, ph):
                    votes[d] += weight * 2
    if not votes or max(votes.values()) == 0:
        return "flat"
    if votes["flat"] and votes["flat"] >= max(votes["up"], votes["down"]):
        return "flat"
    return "up" if votes["up"] >= votes["down"] else "down"


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


def classify_rules(title: str, text: str) -> EventClassification:
    rules = load_factor_rules()
    catalog = rules.get("catalog", {})
    dirs = rules.get("directions", {})
    full = f"{title}. {text}"
    sents = _sent_tokens(full)
    title_toks = [_norm(t) for t in tokens(title)]
    all_toks = [t for s in sents for t in s]
    strong = any(phrase_in(all_toks, m) for m in rules.get("strong_markers", []))
    effects = []
    for factor, spec in catalog.items():
        kws = spec.get("keywords", [])
        hits = sum(1 for s in sents for k in kws if phrase_in(s, k))
        if not hits:
            continue
        in_title = any(phrase_in(title_toks, k) for k in kws)
        markers = [
            m for d in ("up", "down", "flat") for m in dirs.get(d, []) + (spec.get("dir") or {}).get(d, [])
        ]
        title_dir = in_title and any(phrase_in(title_toks, m) for m in markers)
        strength = 1 + int(in_title) + int(strong or hits >= 4)
        effects.append(
            (
                hits + 3 * in_title + 3 * title_dir,
                FactorEffect(
                    factor=factor, direction=_direction(factor, sents, kws, rules), strength=min(3, strength)
                ),
            )
        )
    effects.sort(key=lambda x: -x[0])
    factors = [e for _, e in effects][:5]
    okved = [
        prefix
        for prefix, kws in rules.get("sector_keywords", {}).items()
        if any(phrase_in(s, k) for s in sents for k in kws)
    ]
    regions = sorted(
        {
            name
            for stem_, name in rules.get("regions", {}).items()
            if any(t.startswith(stem_) for t in all_toks)
        }
    )
    low = _norm(full)
    participants = [
        p for p in rules.get("participants", []) if re.search(rf"(?<!\w){re.escape(_norm(p))}(?!\w)", low)
    ]
    modality = "fact"
    mod = rules.get("modality", {})
    if (
        any(phrase_in(_norm(title).split(), k) for k in mod.get("forecast", []))
        or sum(phrase_in(s, k) for s in sents for k in mod.get("forecast", [])) >= 3
    ):
        modality = "forecast"
    ctype = TYPE_BY_FACTOR.get(factors[0].factor, "other") if factors else "other"
    return EventClassification(
        type=ctype,
        factors=factors,
        okved_sections=okved,
        regions=regions,
        participants=participants,
        dates=DATE_RE.findall(full)[:5],
        modality=modality,
        method="rules",
    )


def extract_facts(
    material_id: str, title: str, text: str, factors: list[FactorEffect], limit: int = 4
) -> list[EventFact]:
    text, _ = strip_injection(text)
    rules = load_factor_rules()
    catalog = rules.get("catalog", {})
    kws = [k for f in factors for k in catalog.get(f.factor, {}).get("keywords", [])]
    scored = []
    for i, s in enumerate(sentences(text)):
        toks = [_norm(t) for t in tokens(s)]
        score = (
            2 * any(ch.isdigit() for ch in s) + sum(phrase_in(toks, k) for k in kws) + (1 if i == 0 else 0)
        )
        scored.append((score, i, s))
    picked = sorted(sorted(scored, key=lambda x: (-x[0], x[1]))[:limit], key=lambda x: x[1])
    facts = []
    for _, _, s in picked:
        words = s.split()
        quote = " ".join(words[:25]) + ("…" if len(words) > 25 else "")
        facts.append(EventFact(text=s, quote=quote, material_id=material_id))
    return facts


CLASSIFY_SYSTEM = (
    "Ты классифицируешь деловые новости для банка. Верни JSON строго по схеме: "
    '{"type": one of [monetary, fx, commodity, regulation, sanctions, industry, company, macro_stat, other], '
    '"factors": [{"factor": код из списка, "direction": "up"|"down"|"flat", "strength": 1..3}], '
    '"okved_sections": [префиксы ОКВЭД, например "24", "47", "41"], "regions": [регионы РФ], '
    '"participants": [организации], "dates": [даты], "modality": "fact"|"forecast"|"opinion"}. '
    "Коды факторов: {codes}. Для курсов direction=up означает ослабление рубля. "
    "Для ключевой ставки flat означает сохранение. Не выдумывай факторы, которых нет в тексте."
)


def classify(title: str, text: str, trace_id: str | None = None) -> tuple[EventClassification, dict]:
    text, injected = strip_injection(text)
    title, injected_title = strip_injection(title)
    injected += injected_title
    rules_result = classify_rules(title, text)
    codes = ", ".join(load_factor_rules().get("catalog", {}).keys())
    user = json.dumps({"title": title, "text": text[:4000]}, ensure_ascii=False)
    result, meta = get_llm().complete(
        "classify",
        CLASSIFY_SYSTEM.replace("{codes}", codes),
        user,
        mock=lambda: rules_result.model_dump(),
        trace_id=trace_id,
    )
    try:
        parsed = EventClassification.model_validate(result)
        valid = set(load_factor_rules().get("catalog", {}))
        parsed.factors = [f for f in parsed.factors if f.factor in valid][:5]
        if meta.mode == "live" and not meta.fallback:
            parsed.method = f"llm:{meta.model}"
        if not parsed.factors:
            parsed = rules_result
    except Exception:
        parsed = rules_result
    if injected:
        parsed.flags = ["prompt_injection_suspected"]
    return parsed, {
        "injected": injected,
        "model": meta.model,
        "mode": meta.mode,
        "tokens": meta.tokens,
        "cost_rub": meta.cost_rub,
        "ms": meta.ms,
        "fallback": meta.fallback,
    }
