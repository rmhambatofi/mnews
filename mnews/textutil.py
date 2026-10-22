"""Utilitaires texte : normalisation d'URL, extraction de texte HTML, dates."""
from __future__ import annotations

import calendar
import hashlib
import html
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 mnews/0.1"
)

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "s", "igshid",
}


def normalize_url(url: str) -> str:
    """Supprime les paramètres de tracking, le fragment et les slashs finaux."""
    url = (url or "").strip()
    if not url:
        return ""
    try:
        parts = urlparse(url)
    except ValueError:
        return url
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
             if k.lower() not in _TRACKING_PARAMS]
    path = re.sub(r"/+$", "", parts.path) or "/"
    return urlunparse((scheme, netloc, path, "", urlencode(query), ""))


def url_hash(url: str) -> str:
    return hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()


class _TextExtractor(HTMLParser):
    """Extraction de texte minimaliste, sans dépendance externe."""

    _SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header",
             "aside", "form", "iframe", "template"}
    _BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr",
              "section", "article", "blockquote", "pre", "ul", "ol", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.parts: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title and not self.title:
            self.title = data.strip()
        if self._skip_depth == 0:
            self.parts.append(data)


def html_to_text(raw_html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(raw_html)
    except Exception:
        pass
    text = "".join(parser.parts)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def strip_html(fragment: str, max_len: int = 600) -> str:
    """Nettoie un résumé HTML de flux RSS en texte court."""
    text = html_to_text(fragment or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        text = text[: max_len - 1].rsplit(" ", 1)[0] + "…"
    return text


def fetch_article_text(url: str, max_chars: int = 7000, timeout: int = 15) -> Optional[str]:
    """Télécharge une page et renvoie son texte principal (tronqué)."""
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "fr,en;q=0.8"},
            timeout=timeout,
            allow_redirects=True,
        )
        ctype = resp.headers.get("content-type", "")
        if resp.status_code >= 400:
            return None
        if not any(t in ctype for t in ("html", "xml", "text")):
            return None
        text = html_to_text(resp.text)
        if len(text) > max_chars:
            text = text[:max_chars] + "…"
        return text or None
    except requests.RequestException:
        return None


def struct_time_to_datetime(st) -> Optional[datetime]:
    """feedparser renvoie des time.struct_time en UTC ; on convertit en datetime naïf UTC."""
    if not st:
        return None
    try:
        ts = calendar.timegm(st)
        return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
    except (OverflowError, ValueError, TypeError):
        return None


def utc_naive(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def truncate(text: str, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[: n - 1] + "…"
