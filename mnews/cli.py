"""Ligne de commande : python -m mnews <commande>."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from mnews.logsetup import setup_logging


def cmd_init_db(args) -> int:
    from mnews.db import init_db
    from mnews.seed_sources import seed_sources

    init_db()
    print("Tables créées.")
    if not args.no_seed:
        n = seed_sources()
        print(f"{n} source(s) ajoutée(s).")
    return 0


def cmd_seed(args) -> int:
    from mnews.seed_sources import seed_sources

    print(f"{seed_sources()} source(s) ajoutée(s).")
    return 0


def cmd_collect(args) -> int:
    from mnews.pipeline.collect import collect_all

    reports = collect_all(lookback_hours=args.hours, only_source_id=args.source)
    width = max((len(r.name) for r in reports), default=10)
    for r in reports:
        state = "OK   " if r.status == "ok" else "ERREUR"
        line = f"{state} {r.name.ljust(width)}  {r.fetched:4d} récupérés  {r.new:4d} nouveaux"
        if r.error:
            line += f"  -> {r.error}"
        print(line)
    errors = sum(1 for r in reports if r.status == "error")
    print(f"\n{len(reports)} sources, {errors} en erreur, {sum(r.new for r in reports)} nouveaux éléments.")
    return 0


def cmd_check_sources(args) -> int:
    """Teste chaque source sans rien écrire en base."""
    from datetime import timedelta

    from sqlalchemy import select

    from mnews.collectors import collect_source, reddit_batch
    from mnews.db import Source, session_scope, utcnow

    since = utcnow() - timedelta(hours=args.hours)
    with session_scope() as session:
        sources = session.scalars(select(Source).order_by(Source.id)).all()
    with reddit_batch([src.url for src in sources if src.type == "reddit"], since):
        for src in sources:
            try:
                items = collect_source(src, since)
                print(f"OK     [{src.id:3d}] {src.name}: {len(items)} éléments")
                if args.verbose:
                    for it in items[:3]:
                        print(f"         - {it.title[:90]}")
            except Exception as exc:  # noqa: BLE001
                print(f"ERREUR [{src.id:3d}] {src.name}: {type(exc).__name__}: {exc}")
    return 0


def cmd_run(args) -> int:
    from mnews.pipeline.run import run_pipeline

    nl_id = run_pipeline(trigger=args.trigger, force=args.force, send=True, skip_collect=args.skip_collect)
    print(f"Newsletter #{nl_id} envoyée." if nl_id else "Rien envoyé (voir le journal).")
    return 0 if nl_id else 1


def cmd_preview(args) -> int:
    from mnews.db import Newsletter, session_scope
    from mnews.pipeline.run import run_pipeline

    nl_id = run_pipeline(trigger="cli", force=True, send=False, skip_collect=args.skip_collect)
    if not nl_id:
        print("Échec de la génération (voir le journal).")
        return 1
    with session_scope() as session:
        nl = session.get(Newsletter, nl_id)
        print("=" * 80)
        print(nl.body_text)
        print("=" * 80)
        print(f"Newsletter #{nl.id} (aperçu) — {nl.input_tokens} tokens entrée, "
              f"{nl.output_tokens} sortie, ~{nl.cost_usd:.3f} $")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(nl.body_text)
        print(f"Écrit dans {args.out}")
    return 0


def cmd_send_test(args) -> int:
    from mnews.config import settings
    from mnews.mailer import send_email

    send_email(
        "[News IA] Test d'envoi",
        f"Ceci est un email de test envoyé le {datetime.now(settings.tz):%d/%m/%Y à %H:%M}.\n"
        "Si vous le recevez, la configuration SMTP fonctionne.\nhttps://claude.com/",
        settings.mail_recipients(),
    )
    print("Email de test envoyé à", ", ".join(settings.mail_recipients()))
    return 0


def cmd_resend(args) -> int:
    from mnews.config import settings
    from mnews.db import Newsletter, session_scope, utcnow
    from mnews.mailer import send_email

    with session_scope() as session:
        nl = session.get(Newsletter, args.id)
        if not nl:
            print("Newsletter introuvable")
            return 1
        send_email(nl.subject, nl.body_text, settings.mail_recipients())
        nl.status = "sent"
        nl.sent_at = utcnow()
    print(f"Newsletter #{args.id} envoyée.")
    return 0


def cmd_web(args) -> int:
    from web.app import app

    app.run(host=args.host, port=args.port, debug=True)
    return 0


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    setup_logging()
    parser = argparse.ArgumentParser(prog="mnews", description="Agent de veille IA")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db", help="Crée les tables et les sources initiales")
    p.add_argument("--no-seed", action="store_true")
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("seed-sources", help="Ajoute les sources initiales manquantes")
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("collect", help="Collecte les sources et stocke les nouveaux articles")
    p.add_argument("--hours", type=int, default=None, help="Fenêtre de fraîcheur en heures")
    p.add_argument("--source", type=int, default=None, help="Id d'une seule source")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("check-sources", help="Teste chaque source sans écrire en base")
    p.add_argument("--hours", type=int, default=72)
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_check_sources)

    p = sub.add_parser("run", help="Pipeline complet avec envoi (commande du cron)")
    p.add_argument("--force", action="store_true", help="Ignore dimanche/férié et « déjà envoyée »")
    p.add_argument("--skip-collect", action="store_true")
    p.add_argument("--trigger", default="cron")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("preview", help="Génère la newsletter sans l'envoyer")
    p.add_argument("--skip-collect", action="store_true")
    p.add_argument("--out", default=None, help="Fichier texte de sortie")
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("send-test", help="Envoie un email de test (vérifie le SMTP)")
    p.set_defaults(func=cmd_send_test)

    p = sub.add_parser("resend", help="Renvoie une newsletter existante par email")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_resend)

    p = sub.add_parser("web", help="Lance l'interface web en local (développement)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5000)
    p.set_defaults(func=cmd_web)

    args = parser.parse_args(argv)
    sys.exit(args.func(args))
