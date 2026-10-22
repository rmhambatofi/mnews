"""Collecteur générique pour les sites sans flux RSS (ex. anthropic.com/news).

On télécharge une page d'index et on en extrait les liens dont le chemin
correspond à un motif. Un lien jamais vu = un nouvel article.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from mnews.collectors.base import CollectError, RawItem, http_get

_LINK_RE = re.compile(r'<a\b[^>]*?href="([^"#]+)"[^>]*>(.*?)</a>', re.S | re.I)


def _clean(inner: str) -> str:
    text = re.sub(r"<[^>]+>", " ", inner)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def collect_html_links(page_url: str, pattern: str, lang: str = "en",
                       min_title_len: int = 15, exclude: list[str] | None = None,
                       max_items: int = 40) -> list[RawItem]:
    resp = http_get(page_url, headers={"Accept": "text/html"})
    rx = re.compile(pattern)
    base = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"
    excluded = set(exclude or [])
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    found: dict[str, RawItem] = {}
    for href, inner in _LINK_RE.findall(resp.text):
        path = urlparse(href).path if href.startswith("http") else href
        if not rx.match(path) or path in excluded:
            continue
        title = _clean(inner)
        if len(title) < min_title_len:
            continue
        url = urljoin(base, href)
        if url in found:
            continue
        found[url] = RawItem(url=url, title=title[:300], summary="", published_at=now,
                             lang=lang, extra={"platform": "html_index"})
        if len(found) >= max_items:
            break
    if not found:
        raise CollectError(f"Aucun lien ne correspond au motif {pattern!r} sur {page_url}")
    return list(found.values())
