"""Étape 2 : Claude choisit les sujets du jour parmi les candidats."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from mnews.config import settings
from mnews.db import Item, Newsletter, NewsletterItem, Source, session_scope, utcnow
from mnews.llm import LLMResult, call_json
from mnews.textutil import truncate

log = logging.getLogger("mnews.select")

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

GITHUB_TYPES = ("github_trending", "github_search")

# Nombre d'appels au maximum quand le modèle renvoie une sélection vide
SELECT_ATTEMPTS = 2

SELECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "stories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "working_title": {"type": "string"},
                    "item_ids": {"type": "array", "items": {"type": "integer"}},
                    "fetch_item_ids": {"type": "array", "items": {"type": "integer"}},
                    "importance": {"type": "integer", "description": "De 1 (anecdotique) à 5 (majeur)"},
                    "category": {
                        "type": "string",
                        "enum": ["produit", "modele", "securite", "outil_dev", "recherche", "business", "reglementation", "autre"],
                    },
                    "why": {"type": "string"},
                },
                "required": ["working_title", "item_ids", "fetch_item_ids", "importance", "category", "why"],
                "additionalProperties": False,
            },
        },
        "repos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "integer"},
                    "why": {"type": "string"},
                },
                "required": ["item_id", "why"],
                "additionalProperties": False,
            },
        },
        "editor_note": {"type": "string"},
    },
    "required": ["stories", "repos", "editor_note"],
    "additionalProperties": False,
}


@dataclass
class Candidate:
    id: int
    title: str
    url: str
    summary: str
    source_name: str
    source_type: str
    lang: str
    score: int
    published_at: datetime | None
    extra: dict = field(default_factory=dict)


@dataclass
class Story:
    working_title: str
    items: list[Candidate]
    importance: int
    category: str
    why: str
    fetch_item_ids: list[int]


@dataclass
class Selection:
    stories: list[Story]
    repos: list[tuple[Candidate, str]]
    editor_note: str
    candidates_count: int
    llm: LLMResult


def load_candidates(lookback_hours: int | None = None) -> list[Candidate]:
    """Articles récents non encore utilisés dans une newsletter envoyée."""
    lookback = lookback_hours or settings.lookback_hours
    since = utcnow() - timedelta(hours=lookback)
    with session_scope() as session:
        used_ids = set(
            session.scalars(
                select(NewsletterItem.item_id)
                .join(Newsletter, Newsletter.id == NewsletterItem.newsletter_id)
                .where(Newsletter.status == "sent")
            ).all()
        )
        rows = session.execute(
            select(Item, Source)
            .join(Source, Source.id == Item.source_id)
            .where(Source.enabled.is_(True))
            .where((Item.published_at >= since) | (Item.fetched_at >= since))
            .order_by(Item.score.desc(), Item.published_at.desc())
        ).all()
        out: list[Candidate] = []
        for item, src in rows:
            if item.id in used_ids:
                continue
            out.append(
                Candidate(
                    id=item.id, title=item.title, url=item.url, summary=item.summary or "",
                    source_name=src.name, source_type=src.type, lang=item.lang,
                    score=item.score, published_at=item.published_at, extra=item.extra,
                )
            )
    return out


def recent_headlines(days: int | None = None) -> list[str]:
    """Titres des sujets des newsletters récentes (pour éviter les répétitions)."""
    days = days or settings.history_days
    since = utcnow() - timedelta(days=days)
    with session_scope() as session:
        rows = session.execute(
            select(Newsletter.date, NewsletterItem.story_title)
            .join(NewsletterItem, NewsletterItem.newsletter_id == Newsletter.id)
            .where(Newsletter.status == "sent")
            .where(Newsletter.created_at >= since)
            .order_by(Newsletter.date.desc())
        ).all()
    seen: set[str] = set()
    out: list[str] = []
    for date, title in rows:
        key = f"{date} {title}"
        if title and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _format_candidates(candidates: list[Candidate], max_chars: int = 220) -> str:
    lines = []
    for c in candidates:
        date = c.published_at.strftime("%d/%m %H:%M") if c.published_at else "?"
        meta = f"[{c.id}] ({c.source_name}, {c.lang}, {date}"
        if c.score:
            meta += f", score {c.score}"
        meta += ")"
        line = f"{meta} {c.title}\n    {c.url}"
        if c.summary:
            line += f"\n    {truncate(c.summary, max_chars)}"
        lines.append(line)
    return "\n".join(lines)


def select_stories(candidates: list[Candidate], today_label: str,
                   max_stories: int | None = None, max_repos: int | None = None) -> Selection:
    max_stories = max_stories or settings.max_stories
    max_repos = max_repos or settings.max_repos
    system = (PROMPTS_DIR / "selector_system.txt").read_text(encoding="utf-8")

    news = [c for c in candidates if c.source_type not in GITHUB_TYPES]
    repos = [c for c in candidates if c.source_type in GITHUB_TYPES]
    # Limite de sécurité sur la taille du prompt
    news = news[:400]
    repos = repos[:80]

    headlines = recent_headlines()
    user = (
        f"Date du jour : {today_label}.\n"
        f"Choisis au maximum {max_stories} sujets d'actualité et {max_repos} dépôts GitHub.\n\n"
        "## Sujets déjà traités récemment (à ne pas répéter)\n"
        + ("\n".join(f"- {h}" for h in headlines) if headlines else "(aucun)")
        + "\n\n## Articles et posts candidats\n"
        + (_format_candidates(news) or "(aucun)")
        + "\n\n## Dépôts GitHub candidats\n"
        + (_format_candidates(repos, max_chars=300) or "(aucun)")
    )

    by_id = {c.id: c for c in candidates}

    # Il arrive (rarement) que le modèle renvoie une sélection vide alors que les
    # candidats sont bons : la même requête rejouée donne une sélection normale.
    # Comme le cron ne passe qu'une fois par jour, on retente avant d'abandonner.
    stories: list[Story] = []
    picked_repos: list[tuple[Candidate, str]] = []
    attempts: list[LLMResult] = []
    for attempt in range(1, SELECT_ATTEMPTS + 1):
        result = call_json(system, user, SELECTION_SCHEMA, step="select",
                           effort=settings.effort_select, max_tokens=8000)
        attempts.append(result)
        stories, picked_repos = _parse_selection(result.data, by_id, max_stories, max_repos)
        if stories or picked_repos:
            break
        log.warning(
            "Sélection vide (essai %d/%d, %d tokens de sortie). Note de l'éditeur : %s | Réponse : %s",
            attempt, SELECT_ATTEMPTS, result.output_tokens,
            result.data.get("editor_note", "") or "(aucune)",
            truncate(json.dumps(result.data, ensure_ascii=False), 1500),
        )

    # Coût et tokens de tous les essais, pour que la newsletter affiche le vrai total
    llm = LLMResult(
        data=result.data,
        input_tokens=sum(a.input_tokens for a in attempts),
        output_tokens=sum(a.output_tokens for a in attempts),
        cost_usd=sum(a.cost_usd for a in attempts),
        model=result.model,
        request_id=result.request_id,
    )

    log.info("Sélection : %d sujets, %d dépôts sur %d candidats", len(stories), len(picked_repos), len(candidates))
    return Selection(
        stories=stories,
        repos=picked_repos,
        editor_note=result.data.get("editor_note", ""),
        candidates_count=len(candidates),
        llm=llm,
    )


def _parse_selection(data: dict, by_id: dict[int, Candidate], max_stories: int,
                     max_repos: int) -> tuple[list[Story], list[tuple[Candidate, str]]]:
    stories: list[Story] = []
    for s in data.get("stories", []):
        items = [by_id[i] for i in s.get("item_ids", []) if i in by_id]
        if not items:
            continue
        stories.append(
            Story(
                working_title=s.get("working_title", "").strip() or items[0].title,
                items=items,
                importance=min(5, max(1, int(s.get("importance", 3)))),
                category=s.get("category", "autre"),
                why=s.get("why", ""),
                fetch_item_ids=[i for i in s.get("fetch_item_ids", []) if i in by_id][:2],
            )
        )
    stories.sort(key=lambda s: -s.importance)
    stories = stories[:max_stories]

    picked_repos: list[tuple[Candidate, str]] = []
    for r in data.get("repos", []):
        c = by_id.get(r.get("item_id"))
        if c and c.source_type in GITHUB_TYPES:
            picked_repos.append((c, r.get("why", "")))
    return stories, picked_repos[:max_repos]
