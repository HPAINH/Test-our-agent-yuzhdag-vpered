import hashlib
import math
import re
from collections import Counter
from functools import lru_cache

import snowballstemmer
from bs4 import BeautifulSoup

_stem_ru = snowballstemmer.stemmer("russian")
_stem_en = snowballstemmer.stemmer("english")
TOKEN_RE = re.compile(r"[a-zа-яё0-9]+(?:[.,][0-9]+)?", re.IGNORECASE)
SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z0-9«\"])")
STOP = set(
    "и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от "
    "меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж "
    "вам ведь там потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без "
    "будто чего раз тоже себе под будет ж тогда кто этот того потому этого какой совсем ним здесь этом один "
    "почти мой тем чтобы нее сейчас были куда зачем всех никогда можно при наконец два об другой хоть после над "
    "больше тот через эти нас про всего них какая много разве три эту моя впрочем хорошо свою этой перед иногда "
    "лучше чуть том нельзя такой им более всегда конечно всю между это также года году году".split()
)


def clean_text(text: str) -> str:
    if not text:
        return ""
    if "<" in text and ">" in text:
        text = BeautifulSoup(text, "html.parser").get_text(" ")
    text = re.sub(r"[ \t\r]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ ]{2,}", " ", text)
    return text.strip()


def text_hash(text: str) -> str:
    norm = re.sub(r"\W+", " ", text.lower()).strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


@lru_cache(maxsize=50000)
def stem(word: str) -> str:
    w = word.lower().replace("ё", "е")
    if re.match(r"^[a-z]+$", w):
        return _stem_en.stemWord(w)
    return _stem_ru.stemWord(w)


def tokens(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text or "")]


def stems(text: str, drop_stop: bool = True) -> list[str]:
    return [stem(t) for t in tokens(text) if not (drop_stop and t in STOP) and len(t) > 1]


def sentences(text: str) -> list[str]:
    parts = []
    for para in re.split(r"\n+", text or ""):
        parts.extend(s.strip() for s in SENT_RE.split(para) if s.strip())
    return parts


def shingles(text: str, k: int = 3) -> set:
    st = stems(text)
    return {" ".join(st[i : i + k]) for i in range(max(0, len(st) - k + 1))}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def stem_match(text_stems: set, phrase: str) -> bool:
    parts = [p for p in phrase.lower().split() if p]
    return all(any(s.startswith(p) or p.startswith(s) and len(s) >= 4 for s in text_stems) for p in parts)


def contains_stem(text_lower: str, phrase: str) -> bool:
    parts = [p for p in phrase.lower().split() if p]
    return all(p in text_lower for p in parts)


class BM25:
    def __init__(self, docs: dict[str, str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.ids = list(docs)
        self.tf = [Counter(stems(docs[i])) for i in self.ids]
        self.len = [sum(c.values()) for c in self.tf]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 0
        df: Counter = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = len(self.ids)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, top_k: int = 10, allowed: set | None = None) -> list[tuple[str, float]]:
        q = stems(query)
        if not q or not self.ids:
            return []
        scores = []
        for idx, doc_id in enumerate(self.ids):
            if allowed is not None and doc_id not in allowed:
                continue
            tf, dl, s = self.tf[idx], self.len[idx], 0.0
            for t in q:
                f = tf.get(t)
                if not f:
                    continue
                s += (
                    self.idf.get(t, 0)
                    * f
                    * (self.k1 + 1)
                    / (f + self.k1 * (1 - self.b + self.b * dl / (self.avg or 1)))
                )
            if s > 0:
                scores.append((doc_id, s))
        scores.sort(key=lambda x: -x[1])
        return scores[:top_k]


def best_sentence(text: str, query: str, max_words: int = 40) -> str:
    sents = sentences(text)
    if not sents:
        return ""
    q = set(stems(query))
    best = max(sents, key=lambda s: len(q & set(stems(s))))
    words = best.split()
    return " ".join(words[:max_words]) + ("…" if len(words) > max_words else "")
