"""Collecteur de flux RSS / Atom (blogs, médias, Reddit, passerelles X)."""
from __future__ import annotations

import re
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import feedparser

from mnews.collectors.base import CollectError, RawItem, http_get
from mnews.textutil import strip_html, struct_time_to_datetime


def collect_rss(url: str, lang: str = "en", since: datetime | None = None,
                headers: dict | None = None, max_items: int = 60) -> list[RawItem]:
    resp = http_get(url, headers=headers)
    feed = feedparser.parse(resp.content)
    if feed.bozo and not feed.entries:
        raise CollectError(f"Flux illisible : {getattr(feed, 'bozo_exception', 'erreur inconnue')}")

    is_google_news = "news.google.com/" in url
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
        extra = {"author": entry.get("author", "")}
        if is_google_news:
            # Titre « Sujet - Média » et résumé qui ne fait que répéter le titre
            publisher = (entry.get("source") or {}).get("title", "")
            if publisher and title.endswith(f" - {publisher}"):
                title = title[: -len(publisher) - 3].strip()
            summary = ""
            extra["publisher"] = publisher
        items.append(
            RawItem(
                url=link,
                title=title,
                summary=strip_html(summary),
                published_at=published,
                lang=lang,
                extra=extra,
            )
        )
    return items


REDDIT_HEADERS = {"User-Agent": "python:mnews-veille-ia:0.2 (newsletter personnelle)"}
_REDDIT_URL = re.compile(r"reddit\.com/r/([^/]+)/(\w+)\.rss", re.I)
_REDDIT_SUB = re.compile(r"reddit\.com/r/([^/]+)/", re.I)

# (subreddit, tri, query) -> éléments, ou l'erreur de la requête groupée
_reddit_cache: dict[tuple[str, str, str], list[RawItem] | CollectError] = {}


def _reddit_key(url: str) -> tuple[str, str, str] | None:
    m = _REDDIT_URL.search(url)
    if not m:
        return None
    return m.group(1).lower(), m.group(2).lower(), urlsplit(url).query


@contextmanager
def reddit_batch(urls: list[str], since: datetime | None = None):
    """Récupère tous les subreddits en une seule requête (multireddit r/a+b+c).

    Reddit limite sévèrement les requêtes anonymes, a fortiori depuis l'IP partagée
    d'un hébergeur : une requête au lieu d'une par subreddit évite les 429.
    Pendant le bloc, collect_reddit() sert les éléments depuis ce cache.
    """
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for url in urls:
        m = _REDDIT_URL.search(url)
        if m:
            groups[(m.group(2).lower(), urlsplit(url).query)].append(m.group(1))
    try:
        for (sort, query), subs in groups.items():
            if len(subs) < 2:
                continue
            params = query if "limit=" in query else "&".join(p for p in (query, "limit=100") if p)
            combined = f"https://www.reddit.com/r/{'+'.join(subs)}/{sort}.rss?{params}"
            keys = {sub.lower(): (sub.lower(), sort, query) for sub in subs}
            try:
                items = collect_rss(combined, since=since, headers=REDDIT_HEADERS, max_items=100)
            except CollectError as exc:
                # Inutile de réessayer subreddit par subreddit : la limite est la même
                for key in keys.values():
                    _reddit_cache[key] = exc
                continue
            buckets: dict[str, list[RawItem]] = {sub: [] for sub in keys}
            for it in items:
                m = _REDDIT_SUB.search(it.url)
                if m and m.group(1).lower() in buckets:
                    buckets[m.group(1).lower()].append(it)
            for sub, key in keys.items():
                _reddit_cache[key] = buckets[sub]
        yield
    finally:
        _reddit_cache.clear()


def collect_reddit(url: str, lang: str = "en", since: datetime | None = None) -> list[RawItem]:
    """Reddit expose chaque listing en RSS ; voir reddit_batch() pour la collecte groupée."""
    key = _reddit_key(url)
    cached = _reddit_cache.get(key) if key else None
    if isinstance(cached, CollectError):
        raise cached
    if cached is not None:
        items = [replace(it, lang=lang, extra=dict(it.extra)) for it in cached]
    else:
        items = collect_rss(url, lang=lang, since=since, headers=REDDIT_HEADERS)
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
