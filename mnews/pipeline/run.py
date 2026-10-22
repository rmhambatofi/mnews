"""Orchestration complète : collecte -> sélection -> rédaction -> envoi."""
from __future__ import annotations

import io
import logging
import os
import traceback
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from mnews.calendar_rules import should_send_today
from mnews.config import ROOT_DIR, settings
from mnews.db import Newsletter, NewsletterItem, Run, session_scope, utcnow
from mnews.logsetup import setup_logging
from mnews.mailer import send_alert, send_email
from mnews.pipeline.collect import collect_all
from mnews.pipeline.select import load_candidates, select_stories
from mnews.pipeline.write import french_date, write_newsletter

log = logging.getLogger("mnews.run")

LOCK_FILE = ROOT_DIR / "mnews.lock"


class AlreadyRunning(Exception):
    pass


def _acquire_lock() -> None:
    if LOCK_FILE.exists():
        age = utcnow().timestamp() - LOCK_FILE.stat().st_mtime
        if age < 3600:
            raise AlreadyRunning("Une exécution est déjà en cours (fichier mnews.lock présent)")
        log.warning("Verrou périmé (%d s), on le remplace", int(age))
    LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")


def _release_lock() -> None:
    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


def _already_sent(date_str: str) -> bool:
    with session_scope() as session:
        return session.scalar(
            select(Newsletter.id).where(Newsletter.date == date_str, Newsletter.status == "sent")
        ) is not None


def run_pipeline(trigger: str = "cli", force: bool = False, send: bool = True,
                 skip_collect: bool = False) -> int | None:
    """Exécute le pipeline. Renvoie l'id de la newsletter créée, ou None si rien n'a été fait.

    - force : ignore les règles « pas le dimanche / férié » et « déjà envoyée aujourd'hui ».
    - send : False -> génère seulement un aperçu (statut `preview`), sans envoyer.
    """
    setup_logging()
    now_local = datetime.now(settings.tz)
    today = now_local.date()
    date_str = today.isoformat()

    # Capture du journal de cette exécution pour l'enregistrer en base
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger("mnews").addHandler(handler)

    with session_scope() as session:
        run = Run(trigger=trigger, status="running")
        session.add(run)
        session.flush()
        run_id = run.id

    newsletter_id: int | None = None
    status = "failed"
    error: str | None = None
    try:
        _acquire_lock()
        try:
            ok, reason = should_send_today(today)
            if send and not force and not ok:
                log.info("Pas d'envoi aujourd'hui : %s", reason)
                status = "skipped"
                return None
            if send and not force and _already_sent(date_str):
                log.info("Newsletter du %s déjà envoyée, rien à faire", date_str)
                status = "skipped"
                return None

            if not skip_collect:
                reports = collect_all()
                errors = [r for r in reports if r.status == "error"]
                log.info("Collecte : %d sources, %d en erreur, %d nouveaux éléments",
                         len(reports), len(errors), sum(r.new for r in reports))
                if reports and len(errors) == len(reports):
                    raise RuntimeError("Toutes les sources ont échoué (réseau ?)")

            candidates = load_candidates()
            if not candidates:
                raise RuntimeError("Aucun article candidat trouvé dans la fenêtre de collecte")

            selection = select_stories(candidates, french_date(today))
            if not selection.stories and not selection.repos:
                raise RuntimeError("Le sélectionneur n'a retenu aucun sujet")

            draft = write_newsletter(selection, today)

            total_in = selection.llm.input_tokens + draft.llm.input_tokens
            total_out = selection.llm.output_tokens + draft.llm.output_tokens
            total_cost = selection.llm.cost_usd + draft.llm.cost_usd

            with session_scope() as session:
                nl = Newsletter(
                    date=date_str,
                    subject=draft.subject[:300],
                    body_text=draft.body,
                    status="draft",
                    model=draft.llm.model,
                    input_tokens=total_in,
                    output_tokens=total_out,
                    cost_usd=total_cost,
                    candidates_count=selection.candidates_count,
                )
                session.add(nl)
                session.flush()
                newsletter_id = nl.id
                for idx, story in enumerate(selection.stories):
                    title = draft.story_titles[idx] if idx < len(draft.story_titles) else story.working_title
                    for c in story.items:
                        session.add(NewsletterItem(newsletter_id=nl.id, item_id=c.id, story_title=title[:300]))
                for c, _why in selection.repos:
                    session.add(NewsletterItem(newsletter_id=nl.id, item_id=c.id, story_title=f"[repo] {c.title}"[:300]))

            if send:
                send_email(draft.subject, draft.body, settings.mail_recipients())
                with session_scope() as session:
                    nl = session.get(Newsletter, newsletter_id)
                    nl.status = "sent"
                    nl.sent_at = utcnow()
                log.info("Newsletter %s envoyée (coût estimé %.3f $)", date_str, total_cost)
            else:
                with session_scope() as session:
                    nl = session.get(Newsletter, newsletter_id)
                    nl.status = "preview"
                log.info("Aperçu généré (newsletter #%d, non envoyée)", newsletter_id)
            status = "success"
            return newsletter_id
        finally:
            _release_lock()
    except AlreadyRunning as exc:
        status = "skipped"
        error = str(exc)
        log.warning("%s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
        log.error("Échec du pipeline : %s", exc)
        if newsletter_id:
            with session_scope() as session:
                nl = session.get(Newsletter, newsletter_id)
                if nl and nl.status != "sent":
                    nl.status = "failed"
                    nl.error = str(exc)
        if send:
            send_alert(
                f"échec de la newsletter du {date_str}",
                f"Le pipeline a échoué ({trigger}).\n\n{error}\n\n--- Journal ---\n{buf.getvalue()[-6000:]}",
            )
        return None
    finally:
        logging.getLogger("mnews").removeHandler(handler)
        with session_scope() as session:
            run = session.get(Run, run_id)
            if run:
                run.finished_at = utcnow()
                run.status = status
                run.error = error
                run.newsletter_id = newsletter_id
                run.log = buf.getvalue()[-200_000:]
