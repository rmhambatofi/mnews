"""Étape 3 : Claude rédige la newsletter dans le style de référence."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from mnews.config import settings
from mnews.db import Item, session_scope
from mnews.llm import LLMResult, call_json
from mnews.pipeline.select import Selection, Story
from mnews.textutil import fetch_article_text, truncate

log = logging.getLogger("mnews.write")

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

WRITER_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string", "description": "Première ligne / objet de l'email"},
        "body": {"type": "string", "description": "Corps complet en texte brut, titre inclus en première ligne"},
        "story_titles": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Un titre court en français par sujet traité, dans l'ordre",
        },
    },
    "required": ["subject", "body", "story_titles"],
    "additionalProperties": False,
}

MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
             "septembre", "octobre", "novembre", "décembre"]


@dataclass
class Draft:
    subject: str
    body: str
    story_titles: list[str]
    llm: LLMResult


def french_date(day) -> str:
    return f"{day.day} {MONTHS_FR[day.month - 1]}"


def _fetch_full_texts(selection: Selection, max_chars: int = 7000) -> dict[int, str]:
    """Télécharge le texte complet des articles demandés par la sélection (avec cache en base)."""
    wanted: list[int] = []
    for story in selection.stories:
        wanted.extend(story.fetch_item_ids)
    texts: dict[int, str] = {}
    if not wanted:
        return texts
    with session_scope() as session:
        for item_id in dict.fromkeys(wanted):
            item = session.get(Item, item_id)
            if not item:
                continue
            if item.content:
                texts[item_id] = item.content[:max_chars]
                continue
            text = fetch_article_text(item.url, max_chars=max_chars)
            if text:
                item.content = text
                texts[item_id] = text
                log.info("Texte récupéré pour [%d] %s (%d caractères)", item_id, item.title, len(text))
            else:
                log.info("Pas de texte récupérable pour [%d] %s", item_id, item.title)
    return texts


def _format_story(idx: int, story: Story, texts: dict[int, str]) -> str:
    lines = [
        f"### Sujet {idx} — {story.working_title}",
        f"Importance : {story.importance}/5 · Catégorie : {story.category}",
        f"Pourquoi : {story.why}",
    ]
    for c in story.items:
        lines.append(f"- Source : {c.source_name} ({c.lang}) — {c.title}")
        lines.append(f"  URL : {c.url}")
        if c.summary:
            lines.append(f"  Résumé : {truncate(c.summary, 800)}")
        if c.extra.get("hn_url"):
            lines.append(f"  Discussion HN : {c.extra['hn_url']}")
        if c.id in texts:
            lines.append("  Texte complet :")
            lines.append("  " + texts[c.id].replace("\n", "\n  "))
    return "\n".join(lines)


def _format_repos(selection: Selection) -> str:
    if not selection.repos:
        return "(aucun dépôt cette fois-ci : omettre la section ⭐)"
    lines = []
    for c, why in selection.repos:
        stars = c.extra.get("stars_week") or c.extra.get("stars_total") or c.score
        lines.append(f"- {c.title} — {c.summary or '(pas de description)'}")
        lines.append(f"  URL : {c.url} · étoiles : {stars} · note du sélectionneur : {why}")
    return "\n".join(lines)


def write_newsletter(selection: Selection, day) -> Draft:
    style = (PROMPTS_DIR / "style_example.txt").read_text(encoding="utf-8").strip()
    system = (PROMPTS_DIR / "writer_system.txt").read_text(encoding="utf-8").replace("{style_example}", style)

    texts = _fetch_full_texts(selection)
    parts = [
        f"Date de la newsletter : {french_date(day)} ({day.isoformat()}).",
        "Rédige la newsletter complète à partir des sujets suivants, dans l'ordre d'importance.",
        "",
    ]
    for i, story in enumerate(selection.stories, start=1):
        parts.append(_format_story(i, story, texts))
        parts.append("")
    parts.append("### Dépôts GitHub de la semaine")
    parts.append(_format_repos(selection))
    if selection.editor_note:
        parts.append("")
        parts.append(f"Note du sélectionneur : {selection.editor_note}")

    result = call_json(system, "\n".join(parts), WRITER_SCHEMA, step="write", effort=settings.effort_write, max_tokens=16000)
    body = result.data.get("body", "").strip()
    subject = result.data.get("subject", "").strip() or body.splitlines()[0] if body else ""
    titles = [t.strip() for t in result.data.get("story_titles", []) if t.strip()]
    if not body:
        raise RuntimeError("Le modèle a renvoyé un corps vide")
    return Draft(subject=subject, body=body, story_titles=titles, llm=result)
