import logging
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.ingest.news import add_material
from app.ingest.ocr import IMAGE_SUFFIXES, image_text, page_png, pdf_pages
from app.processing.text import stems

log = logging.getLogger("ingest.alfa")
PAGE_BREAK = "---page---"


BOILERPLATE = (
    "все права защищены",
    "несанкционированное копирование",
    "инвестиционной рекомендацией",
    "инвестиционному консультированию",
    "генеральная лицензия",
    "исключительной собственностью",
    "строго запрещено",
    "распространяется исключительно",
    "в письменной форме",
    "не предназначен для распространения",
    "контакты и дисклеймер",
)


def _noise_line(line: str) -> bool:
    s = line.strip()
    if len(s) < 2 or "@" in s:
        return True
    letters = [ch for ch in s if ch.isalpha()]
    if not letters or len(letters) / len(s) < 0.45:
        return True
    cyr = sum("а" <= ch.lower() <= "я" or ch.lower() == "ё" for ch in letters)
    return cyr / len(letters) < 0.6


def clean_report_text(text: str) -> str:
    from app.processing.text import sentences

    low = text.lower()
    cut = low.find("контакты и дисклеймер")
    if cut != -1:
        text = text[:cut]
    paras = []
    for para in re.split(r"\n\s*\n", text):
        lines = [ln.strip() for ln in para.splitlines() if not _noise_line(ln)]
        joined = re.sub(r"\s+", " ", " ".join(lines)).strip()
        kept = [x for x in sentences(joined) if not any(b in x.lower() for b in BOILERPLATE)]
        if kept:
            paras.append(" ".join(kept))
    merged: list[str] = []
    for p in paras:
        if merged and not merged[-1].endswith((".", "!", "?", ":")):
            merged[-1] = f"{merged[-1]} {p}"
        else:
            merged.append(p)
    return "\n\n".join(m for m in merged if len(m) > 30)


def _units(text: str, target_tokens: int) -> list[str]:
    from app.processing.text import sentences

    out = []
    for p in (x.strip() for x in re.split(r"\n\s*\n", text)):
        if not p:
            continue
        if len(stems(p, drop_stop=False)) <= target_tokens:
            out.append(p)
        else:
            out.extend(s.strip() for s in sentences(p) if s.strip())
    return out


def chunk_text(text: str, target_tokens: int = 160, overlap_tokens: int = 30) -> list[str]:
    paras = _units(text, target_tokens)
    chunks, current, size = [], [], 0
    for p in paras:
        n = len(stems(p, drop_stop=False))
        if current and size + n > target_tokens:
            chunks.append(" ".join(current))
            tail = current[-1]
            current = [tail] if len(stems(tail, drop_stop=False)) <= overlap_tokens else []
            size = sum(len(stems(x, drop_stop=False)) for x in current)
        current.append(p)
        size += n
    if current:
        chunks.append(" ".join(current))
    return chunks


def _read_markdown(path: Path) -> tuple[str, datetime | None, list[str]]:
    raw = path.read_text(encoding="utf-8")
    title, date, body = path.stem, None, []
    for line in raw.splitlines():
        if line.startswith("# ") and title == path.stem:
            title = line[2:].strip()
        elif line.lower().startswith("date:") and date is None:
            try:
                date = datetime.fromisoformat(line.split(":", 1)[1].strip())
            except ValueError:
                date = None
        else:
            body.append(line)
    pages = [p.strip() for p in "\n".join(body).split(PAGE_BREAK)]
    return title, date, pages


def _read_pdf(path: Path) -> tuple[str, datetime | None, list[dict]]:
    import pymupdf

    pages = pdf_pages(path)
    meta_title = (pymupdf.open(path).metadata or {}).get("title") or ""
    first_text = pages[0]["text"] if pages else ""
    first_line = next((ln.strip() for ln in first_text.splitlines() if len(ln.strip()) > 10), "")
    meta = report_meta(path)
    title = meta.get("title") or meta_title or first_line or path.stem
    if meta.get("date"):
        return title, datetime.fromisoformat(str(meta["date"])), pages
    date = None
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", first_text)
    if m:
        try:
            date = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            date = None
    return title, date, pages


def report_meta(path: Path) -> dict:
    import yaml

    index = path.parent / "reports.yaml"
    if not index.exists():
        return {}
    data = yaml.safe_load(index.read_text(encoding="utf-8")) or {}
    return (data.get("reports") or {}).get(path.name, {})


def report_files(alfa_dir: Path) -> list[Path]:
    allowed = {".md", ".txt", ".pdf", *IMAGE_SUFFIXES}
    return [
        p
        for p in sorted(alfa_dir.iterdir())
        if p.is_file() and p.name.lower() != "readme.md" and p.suffix.lower() in allowed
    ]


def load_alfa_dir(session: Session, alfa_dir: Path, synthetic: bool) -> int:
    n = 0
    for idx, path in enumerate(report_files(alfa_dir), start=1):
        suffix = path.suffix.lower()
        try:
            if suffix == ".pdf":
                title, date, pages = _read_pdf(path)
            elif suffix in IMAGE_SUFFIXES:
                title, date = path.stem, None
                pages = [{"page": 1, "text": image_text(path.read_bytes()), "method": "ocr"}]
            else:
                title, date, texts = _read_markdown(path)
                pages = [{"page": i, "text": t, "method": "text"} for i, t in enumerate(texts, start=1)]
        except Exception as exc:
            log.warning("cannot read %s: %s", path, exc)
            continue
        for page in pages:
            page_no = page["page"]
            text = clean_report_text(page["text"]) if suffix != ".md" else page["text"]
            for c_no, chunk in enumerate(chunk_text(text), start=1):
                m = add_material(
                    session,
                    kind="alfa",
                    source="Альфа-Банк, ЦМИ" + (" (синтетический заменитель)" if synthetic else ""),
                    title=title,
                    text=chunk,
                    published_at=date,
                    synthetic=synthetic,
                    report=title,
                    page=page_no,
                    material_id=f"alfa_{idx:02d}_p{page_no}_c{c_no}",
                )
                if m:
                    m.extraction = page["method"]
                n += 1 if m else 0
    return n


VISION_PROMPT = (
    "Это страница аналитического отчёта Альфа-Банка. Если на ней есть график, диаграмма или таблица — опиши, "
    "что показано: показатель, период, ключевые значения и вывод, который из этого следует. Числа бери только "
    'с изображения. Если графиков и таблиц нет — верни пустой текст. JSON: {"text": "..."}'
)


def describe_charts(session: Session, alfa_dir: Path, synthetic: bool) -> int:
    from sqlalchemy import select

    from app.config import get_settings
    from app.db.models import Material
    from app.llm.client import LLMError, get_llm, image_message

    llm, settings = get_llm(), get_settings()
    if not llm.available or not settings.vision_enabled:
        return 0
    done = {m.id for m in session.scalars(select(Material).where(Material.extraction == "vision"))}
    n = 0
    for idx, path in enumerate(report_files(alfa_dir), start=1):
        if path.suffix.lower() != ".pdf":
            continue
        title, date, pages = _read_pdf(path)
        with_images = [p for p in pages if p.get("images")][: settings.vision_max_pages_per_report]
        for page in with_images:
            mid = f"alfa_{idx:02d}_p{page['page']}_vis"
            if mid in done:
                continue
            try:
                result, _ = llm.complete(
                    "vision", VISION_PROMPT, image_message(VISION_PROMPT, page_png(path, page["page"]))
                )
            except LLMError as exc:
                log.warning("vision failed for %s p%s: %s", path.name, page["page"], exc)
                continue
            text = (result or {}).get("text", "").strip() if isinstance(result, dict) else ""
            if len(text) < 40:
                continue
            m = add_material(
                session,
                kind="alfa",
                source="Альфа-Банк, ЦМИ — описание графика моделью",
                title=title,
                text=text,
                published_at=date,
                synthetic=synthetic,
                report=title,
                page=page["page"],
                material_id=mid,
            )
            if m:
                m.extraction = "vision"
                n += 1
    return n
