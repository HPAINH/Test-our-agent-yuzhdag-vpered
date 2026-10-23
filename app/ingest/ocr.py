import glob
import logging
import os
import shutil
from pathlib import Path

from app.config import get_settings

log = logging.getLogger("ingest.ocr")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def tessdata_dir() -> str | None:
    env = os.environ.get("TESSDATA_PREFIX")
    if env and Path(env).exists():
        return env
    found = sorted(glob.glob("/usr/share/tesseract-ocr/*/tessdata")) + sorted(
        glob.glob("/usr/share/tessdata")
    )
    return found[-1] if found else None


def ocr_available() -> bool:
    return bool(shutil.which("tesseract") and tessdata_dir())


def _ocr_page(page) -> str:
    settings = get_settings()
    tp = page.get_textpage_ocr(
        language=settings.ocr_languages, dpi=settings.ocr_dpi, full=True, tessdata=tessdata_dir()
    )
    return page.get_text("text", textpage=tp)


def pdf_pages(data: bytes | Path) -> list[dict]:
    import pymupdf

    doc = pymupdf.open(data) if isinstance(data, Path) else pymupdf.open(stream=data, filetype="pdf")
    min_chars = get_settings().ocr_min_chars
    can_ocr = ocr_available()
    out = []
    for i, page in enumerate(doc, start=1):
        text = page.get_text("text")
        method = "text"
        if len(text.strip()) < min_chars and can_ocr:
            try:
                ocr = _ocr_page(page)
                if len(ocr.strip()) > len(text.strip()):
                    text, method = ocr, "ocr"
            except Exception as exc:
                log.warning("OCR failed on page %s: %s", i, exc)
        out.append({"page": i, "text": text, "method": method, "images": len(page.get_images(full=True))})
    if not can_ocr and any(len(p["text"].strip()) < min_chars for p in out):
        log.warning("в PDF есть страницы без текстового слоя, а Tesseract не установлен")
    return out


def image_text(data: bytes) -> str:
    import pymupdf

    if not ocr_available():
        raise RuntimeError("Tesseract не установлен: распознавание изображений недоступно")
    img = pymupdf.open(stream=data, filetype="png")
    pdf = pymupdf.open("pdf", img.convert_to_pdf())
    return _ocr_page(pdf[0])


def page_png(path: Path, page_no: int, dpi: int = 130) -> bytes:
    import pymupdf

    doc = pymupdf.open(path)
    return doc[page_no - 1].get_pixmap(dpi=dpi).tobytes("png")


def document_text(name: str, data: bytes) -> tuple[str, str]:
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        pages = pdf_pages(data)
        method = "ocr" if any(p["method"] == "ocr" for p in pages) else "text"
        return "\n\n".join(p["text"] for p in pages), method
    if suffix in IMAGE_SUFFIXES:
        return image_text(data), "ocr"
    if suffix in (".txt", ".md"):
        return data.decode("utf-8", errors="replace"), "text"
    raise ValueError("поддерживаются PDF, изображения и текстовые файлы")
