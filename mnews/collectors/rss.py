"""Collecteur de flux RSS / Atom (blogs, médias, Reddit, passerelles X)."""
from __future__ import annotations

from datetime import datetime, timedelta

import feedparser

from mnews.collectors.base import CollectError, RawItem, http_get
from mnews.textutil import strip_html, struct_time_to_datetime


def collect_rss(url: str, lang: str = "en", since: datetime | None = None,
                headers: dict | None = None, max_items: int = 60) -> list[RawItem]:
    resp = http_get(url, headers=headers)
    feed = feedparser.parse(resp.content)
    if feed.bozo and not feed.entries:
        raise CollectError(f"Flux illisible : {getattr(feed, 'bozo_exception', 'erreur inconnue')}")

    items: list[RawItem] = []
    for entry in feed.entries[:max_items]:
        link = entry.get("link") or ""
        title = (entry.get("title") or "").strip()
        if not link or not title:
            continue
        published = struct_time_to_datetime(
            entry.get("published_parsed") or entry.get("updated_parsed")
        )
        if since and published and published < since:
            continue
        summary = entry.get("summary") or ""
        if not summary and entry.get("content"):
            summary = entry["content"][0].get("value", "")
        items.append(
            RawItem(
                url=link,
                title=title,
                summary=strip_html(summary),
                published_at=published,
                lang=lang,
                extra={"author": entry.get("author", "")},
            )
        )
    return items


def collect_reddit(url: str, lang: str = "en", since: datetime | None = None) -> list[RawItem]:
    """Reddit expose chaque listing en RSS ; un User-Agent explicite évite le 429."""
    items = collect_rss(url, lang=lang, since=since,
                        headers={"User-Agent": "mnews-veille-ia/0.1 (contact: admin)"})
    for it in items:
        # Le lien Reddit pointe sur le post ; on garde l'URL Reddit comme référence
        it.extra["platform"] = "reddit"
    return items


def collect_x(handle: str, template: str, lang: str = "en", since: datetime | None = None) -> list[RawItem]:
    """Compte X via une passerelle RSS (Nitter, RSSHub...)."""
    handle = handle.lstrip("@")
    url = template.format(handle=handle)
    items = collect_rss(url, lang=lang, since=since)
    for it in items:
        it.extra["platform"] = "x"
        it.extra["handle"] = handle
        if not it.title or it.title.startswith("RT by"):
            it.title = it.summary[:200] if it.summary else it.title
    return items
