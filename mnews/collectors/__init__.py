"""Registre des collecteurs : un type de source -> une fonction."""
from __future__ import annotations

from datetime import datetime

from mnews.collectors.base import CollectError, RawItem
from mnews.collectors.github_trending import collect_search, collect_trending
from mnews.collectors.hackernews import collect_hackernews
from mnews.collectors.html_links import collect_html_links
from mnews.collectors.rss import collect_reddit, collect_rss, collect_x
from mnews.config import settings
from mnews.db import Source

SOURCE_TYPES = {
    "rss": "Flux RSS / Atom",
    "reddit": "Subreddit (RSS)",
    "x": "Compte X (via passerelle RSS)",
    "hackernews": "Hacker News (API Algolia)",
    "github_trending": "GitHub trending de la semaine",
    "github_search": "GitHub : nouveaux dépôts populaires",
    "html_links": "Page web sans RSS (liens filtrés par motif)",
}


def collect_source(source: Source, since: datetime) -> list[RawItem]:
    cfg = source.config
    t = source.type
    if t == "rss":
        return collect_rss(source.url, lang=source.lang, since=since)
    if t == "reddit":
        return collect_reddit(source.url, lang=source.lang, since=since)
    if t == "x":
        handle = cfg.get("handle") or source.url
        template = cfg.get("template") or settings.x_rss_template
        return collect_x(handle, template, lang=source.lang, since=since)
    if t == "hackernews":
        return collect_hackernews(since, min_points=int(cfg.get("min_points", 40)))
    if t == "github_trending":
        return collect_trending(language=cfg.get("language", ""))
    if t == "github_search":
        return collect_search(
            days=int(cfg.get("days", 7)),
            min_stars=int(cfg.get("min_stars", 150)),
            token=settings.github_token,
            topics=cfg.get("topics") or None,
        )
    if t == "html_links":
        if not cfg.get("pattern"):
            raise CollectError("Option 'pattern' manquante (ex. {\"pattern\": \"^/news/[^/]+$\"})")
        return collect_html_links(
            source.url, cfg["pattern"], lang=source.lang,
            min_title_len=int(cfg.get("min_title_len", 15)),
            exclude=cfg.get("exclude") or [],
        )
    raise CollectError(f"Type de source inconnu : {t}")


__all__ = ["collect_source", "CollectError", "RawItem", "SOURCE_TYPES"]
