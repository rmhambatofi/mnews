"""Étape 1 : collecte de toutes les sources actives et insertion en base."""
from __future__ import annotations

import json
import logging
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from mnews.collectors import collect_source
from mnews.config import settings
from mnews.db import Item, Source, session_scope, utcnow
from mnews.textutil import normalize_url, truncate, url_hash

log = logging.getLogger("mnews.collect")


@dataclass
class SourceReport:
    source_id: int
    name: str
    status: str  # ok | error
    fetched: int = 0
    new: int = 0
    error: str = ""


def collect_all(lookback_hours: int | None = None, only_source_id: int | None = None) -> list[SourceReport]:
    lookback = lookback_hours or settings.lookback_hours
    since = utcnow() - timedelta(hours=lookback)
    reports: list[SourceReport] = []

    with session_scope() as session:
        query = select(Source).where(Source.enabled.is_(True))
        if only_source_id:
            query = select(Source).where(Source.id == only_source_id)
        sources = session.scalars(query).all()

    for src in sources:
        report = SourceReport(source_id=src.id, name=src.name, status="ok")
        if src.type == "reddit":
            time.sleep(4)  # Reddit limite fortement les requêtes rapprochées
        try:
            raw_items = collect_source(src, since)
            report.fetched = len(raw_items)
            report.new = _store_items(src, raw_items, since)
            _update_source(src.id, "ok", None, report.fetched)
            log.info("%s : %d récupérés, %d nouveaux", src.name, report.fetched, report.new)
        except Exception as exc:  # noqa: BLE001 - une source en panne ne bloque pas les autres
            report.status = "error"
            report.error = f"{type(exc).__name__}: {exc}"
            _update_source(src.id, "error", report.error, 0)
            log.warning("%s : échec (%s)", src.name, report.error)
            log.debug(traceback.format_exc())
        reports.append(report)
    return reports


def _store_items(source: Source, raw_items, since: datetime) -> int:
    new_count = 0
    with session_scope() as session:
        for raw in raw_items:
            url = normalize_url(raw.url)
            if not url or not raw.title:
                continue
            # Les dépôts GitHub n'ont pas de date de publication pertinente : on les garde.
            if raw.published_at and raw.published_at < since and source.type not in ("github_trending", "github_search"):
                continue
            h = url_hash(url)
            exists = session.scalar(select(Item.id).where(Item.url_hash == h))
            if exists:
                # Met à jour le score (points HN, étoiles) sans recréer l'élément
                item = session.get(Item, exists)
                if item and raw.score > item.score:
                    item.score = raw.score
                continue
            item = Item(
                source_id=source.id,
                url=url[:1000],
                url_hash=h,
                title=truncate(raw.title.strip(), 500),
                summary=truncate(raw.summary or "", 2000),
                lang=raw.lang or source.lang,
                score=int(raw.score or 0),
                extra_json=json.dumps(raw.extra or {}, ensure_ascii=False)[:5000],
                published_at=raw.published_at or utcnow(),
            )
            try:
                with session.begin_nested():
                    session.add(item)
                    session.flush()
                new_count += 1
            except IntegrityError:
                pass  # doublon inséré entre-temps : ignoré
    return new_count


def _update_source(source_id: int, status: str, error: str | None, count: int) -> None:
    with session_scope() as session:
        src = session.get(Source, source_id)
        if src:
            src.last_fetched_at = utcnow()
            src.last_status = status
            src.last_error = error
            src.last_item_count = count
