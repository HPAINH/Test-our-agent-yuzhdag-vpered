import logging
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.ingest.news import add_material
from app.processing.text import stems

log = logging.getLogger("ingest.alfa")
PAGE_BREAK = "---page---"


def chunk_text(text: str, target_tokens: int = 160, overlap_tokens: int = 30) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, current, size = [], [], 0
    for p in paras:
        n = len(stems(p, drop_stop=False))
        if current and size + n > target_tokens:
            chunks.append("\n\n".join(current))
            tail = current[-1]
            current = [tail] if len(stems(tail, drop_stop=False)) <= overlap_tokens else []
            size = sum(len(stems(x, drop_stop=False)) for x in current)
        current.append(p)
        size += n
    if current:
        chunks.append("\n\n".join(current))
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


def _read_pdf(path: Path) -> tuple[str, datetime | None, list[str]]:
    import pymupdf

    doc = pymupdf.open(path)
    pages = [page.get_text("text") for page in doc]
    meta_title = (doc.metadata or {}).get("title") or ""
    first_line = next(
        (ln.strip() for ln in (pages[0] if pages else "").splitlines() if len(ln.strip()) > 10), ""
    )
    title = meta_title or first_line or path.stem
    date = None
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", pages[0] if pages else "")
    if m:
        try:
            date = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            date = None
    return title, date, pages


def load_alfa_dir(session: Session, alfa_dir: Path, synthetic: bool) -> int:
    n = 0
    for idx, path in enumerate(sorted(alfa_dir.iterdir()), start=1):
        if path.name.lower() == "readme.md" or path.suffix.lower() not in (".md", ".txt", ".pdf"):
            continue
        try:
            title, date, pages = _read_pdf(path) if path.suffix.lower() == ".pdf" else _read_markdown(path)
        except Exception as exc:
            log.warning("cannot read %s: %s", path, exc)
            continue
        for page_no, page in enumerate(pages, start=1):
            for c_no, chunk in enumerate(chunk_text(page), start=1):
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
                n += 1 if m else 0
    return n
