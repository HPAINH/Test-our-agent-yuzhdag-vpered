import ipaddress
import json
import logging
import socket
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import load_yaml
from app.db.models import Material
from app.processing.text import clean_text, text_hash

log = logging.getLogger("ingest")


class UnsafeURL(ValueError):
    pass


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def add_material(
    session: Session,
    *,
    kind: str,
    source: str,
    title: str,
    text: str,
    url: str | None = None,
    published_at=None,
    synthetic: bool = False,
    material_id: str | None = None,
    report: str | None = None,
    page: int | None = None,
    submitted_by: str | None = None,
) -> Material | None:
    text = clean_text(text)
    title = clean_text(title)[:500] or text[:120]
    if len(text) < 20:
        return None
    h = text_hash(title + " " + text)
    existing = session.scalar(select(Material).where(Material.hash == h))
    if existing:
        return existing
    m = Material(
        id=material_id or f"{'m' if kind == 'news' else 'a'}_{uuid.uuid4().hex[:10]}",
        kind=kind,
        source=source,
        url=url,
        title=title,
        text=text,
        published_at=_parse_dt(published_at),
        hash=h,
        synthetic=synthetic,
        report=report,
        page=page,
        submitted_by=submitted_by,
    )
    session.add(m)
    session.flush()
    return m


def load_news_dir(session: Session, news_dir: Path) -> int:
    n = 0
    for path in sorted(news_dir.glob("*.jsonl")):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                m = add_material(
                    session,
                    kind="news",
                    source=item.get("source", "news"),
                    title=item.get("title", ""),
                    text=item.get("text", ""),
                    url=item.get("url"),
                    published_at=item.get("published_at"),
                    synthetic="synthetic" in (item.get("url") or "") or item.get("synthetic", False),
                    material_id=f"m_{item['id']}" if item.get("id") else None,
                )
                n += 1 if m else 0
    return n


def check_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UnsafeURL("only http(s) URLs are allowed")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise UnsafeURL(f"cannot resolve host: {parsed.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise UnsafeURL("internal addresses are not allowed")


def fetch_url(url: str) -> tuple[str, str]:
    cfg = load_yaml("sources.yaml").get("fetch", {})
    max_bytes = cfg.get("max_bytes", 2_000_000)
    current = url
    with httpx.Client(
        timeout=cfg.get("timeout_s", 10),
        follow_redirects=False,
        headers={"User-Agent": cfg.get("user_agent", "ElbrusNewsInsights/1.0")},
    ) as client:
        for _ in range(4):
            check_url(current)
            with client.stream("GET", current) as r:
                if r.is_redirect:
                    current = urljoin(current, r.headers.get("location", ""))
                    continue
                r.raise_for_status()
                chunks, size = [], 0
                for chunk in r.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise UnsafeURL("page is larger than the allowed limit")
                    chunks.append(chunk)
                html = b"".join(chunks).decode(r.encoding or "utf-8", errors="replace")
                break
        else:
            raise UnsafeURL("too many redirects")
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()
    title = (soup.title.get_text(strip=True) if soup.title else "") or current
    paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    text = "\n".join(p for p in paragraphs if len(p) > 40) or soup.get_text(" ", strip=True)
    return title, clean_text(text)[:20000]


SOURCE_STATUS: dict[str, dict] = {}


def fetch_rss(session: Session) -> int:
    sources = load_yaml("sources.yaml").get("rss", [])
    added = 0
    for src in sources:
        status = SOURCE_STATUS.setdefault(src["id"], {"name": src["name"], "fails_in_row": 0})
        status["checked_at"] = datetime.utcnow().isoformat(timespec="seconds")
        try:
            check_url(src["url"])
            with httpx.Client(timeout=10, follow_redirects=True) as client:
                r = client.get(src["url"])
                r.raise_for_status()
            feed = feedparser.parse(r.content)
        except Exception as exc:
            log.warning("rss %s failed: %s", src["id"], exc)
            status.update(ok=False, error=str(exc)[:200], fails_in_row=status["fails_in_row"] + 1)
            continue
        before = added
        for entry in feed.entries[:50]:
            text = entry.get("summary") or entry.get("description") or ""
            if entry.get("content"):
                text = " ".join(c.get("value", "") for c in entry["content"]) or text
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            m = add_material(
                session,
                kind="news",
                source=src["name"],
                title=entry.get("title", ""),
                text=text,
                url=entry.get("link"),
                published_at=datetime(*published[:6]) if published else None,
            )
            added += 1 if m else 0
        status.update(ok=True, error=None, fails_in_row=0, entries=len(feed.entries), added=added - before)
        status["last_ok_at"] = status["checked_at"]
    return added
