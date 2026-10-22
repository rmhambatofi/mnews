"""Collecteur GitHub : page « trending » de la semaine + API de recherche."""
from __future__ import annotations

import html
import re
import time
from datetime import datetime, timedelta, timezone

from mnews.collectors.base import CollectError, RawItem, http_get

TRENDING_URL = "https://github.com/trending?since=weekly"
SEARCH_URL = "https://api.github.com/search/repositories"

_ROW_RE = re.compile(r'<article class="Box-row">(.*?)</article>', re.S)
_REPO_RE = re.compile(r'<h2[^>]*>\s*<a[^>]*href="/([^"/]+/[^"/]+)"', re.S)
_DESC_RE = re.compile(r'<p class="col-9[^"]*">(.*?)</p>', re.S)
_WEEK_STARS_RE = re.compile(r'([\d,]+)\s+stars\s+this\s+week', re.S)
_TOTAL_STARS_RE = re.compile(r'href="/[^"]+/stargazers"[^>]*>(.*?)</a>', re.S)


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def _to_int(s: str) -> int:
    try:
        return int(s.replace(",", "").strip())
    except ValueError:
        return 0


def collect_trending(language: str = "") -> list[RawItem]:
    url = TRENDING_URL + (f"&language={language}" if language else "")
    resp = http_get(url, headers={"Accept": "text/html"})
    rows = _ROW_RE.findall(resp.text)
    if not rows:
        raise CollectError("Page trending GitHub illisible (structure HTML changée ?)")
    items: list[RawItem] = []
    for row in rows:
        m = _REPO_RE.search(row)
        if not m:
            continue
        full_name = m.group(1)
        desc_match = _DESC_RE.search(row)
        desc = _clean(desc_match.group(1)) if desc_match else ""
        week = _WEEK_STARS_RE.search(row)
        total = _TOTAL_STARS_RE.search(row)
        items.append(
            RawItem(
                url=f"https://github.com/{full_name}",
                title=full_name,
                summary=desc,
                published_at=datetime.now(timezone.utc).replace(tzinfo=None),
                score=_to_int(week.group(1)) if week else 0,
                lang="en",
                extra={
                    "platform": "github",
                    "stars_week": _to_int(week.group(1)) if week else 0,
                    "stars_total": _to_int(_clean(total.group(1))) if total else 0,
                },
            )
        )
    return items


DEFAULT_TOPICS = ["ai", "llm", "agents", "claude", "mcp", "openai", "ai-agents"]


def collect_search(days: int = 7, min_stars: int = 150, token: str = "",
                   topics: list[str] | None = None) -> list[RawItem]:
    """Dépôts créés récemment et déjà populaires (complément du trending).

    L'API de recherche GitHub n'accepte pas plusieurs `topic:` reliés par OR :
    on fait une requête par thème et on fusionne.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    repos: dict[str, dict] = {}
    for topic in (topics or DEFAULT_TOPICS):
        q = f"topic:{topic} created:>{since} stars:>{min_stars}"
        resp = http_get(SEARCH_URL, headers=headers,
                        params={"q": q, "sort": "stars", "order": "desc", "per_page": 30})
        for repo in resp.json().get("items", []):
            repos.setdefault(repo["html_url"], repo)
        time.sleep(1.5)  # limite : 10 requêtes/min sans jeton
    items: list[RawItem] = []
    for repo in repos.values():
        created = repo.get("created_at")
        published = None
        if created:
            try:
                published = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                published = None
        items.append(
            RawItem(
                url=repo["html_url"],
                title=repo["full_name"],
                summary=(repo.get("description") or "")[:600],
                published_at=published,
                score=int(repo.get("stargazers_count") or 0),
                lang="en",
                extra={
                    "platform": "github",
                    "stars_total": int(repo.get("stargazers_count") or 0),
                    "topics": repo.get("topics", [])[:10],
                    "language": repo.get("language") or "",
                },
            )
        )
    return items
