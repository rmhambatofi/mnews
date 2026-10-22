"""Types communs aux collecteurs."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests

from mnews.textutil import USER_AGENT


@dataclass
class RawItem:
    """Un élément collecté, avant insertion en base."""

    url: str
    title: str
    summary: str = ""
    published_at: Optional[datetime] = None  # naïf, UTC
    score: int = 0
    lang: str = "en"
    extra: dict = field(default_factory=dict)


class CollectError(Exception):
    pass


def http_get(url: str, timeout: int = 20, headers: Optional[dict] = None,
             retries: int = 2, **kwargs) -> requests.Response:
    """GET avec User-Agent et nouvel essai sur 429 / 5xx (attente croissante)."""
    h = {"User-Agent": USER_AGENT, "Accept-Language": "fr,en;q=0.8"}
    if headers:
        h.update(headers)
    last: Optional[requests.Response] = None
    for attempt in range(retries + 1):
        resp = requests.get(url, headers=h, timeout=timeout, **kwargs)
        if resp.status_code < 400:
            return resp
        last = resp
        if resp.status_code not in (429, 500, 502, 503, 504) or attempt == retries:
            break
        wait = resp.headers.get("retry-after")
        try:
            delay = min(float(wait), 30.0) if wait else 5.0 * (attempt + 1)
        except ValueError:
            delay = 5.0 * (attempt + 1)
        time.sleep(delay)
    raise CollectError(f"HTTP {last.status_code} pour {url}")
