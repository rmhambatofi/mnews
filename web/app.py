"""Interface web Flask : consultation des newsletters, gestion des sources, déclenchement manuel."""
from __future__ import annotations

import hmac
import json
import subprocess
import sys
from datetime import datetime
from functools import wraps

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import desc, func, select

from mnews.collectors import SOURCE_TYPES
from mnews.config import ROOT_DIR, settings
from mnews.costs import month_spend_usd, usd_to_eur
from mnews.db import Item, LlmCall, Newsletter, NewsletterItem, Run, Source, init_db, session_scope
from mnews.logsetup import setup_logging
from mnews.mailer import text_to_html
from mnews.pipeline.run import LOCK_FILE

setup_logging()

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = settings.secret_key or "dev-secret-change-me"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


@app.template_filter("dt")
def _fmt_dt(value, fmt="%d/%m/%Y %H:%M"):
    if not value:
        return "—"
    if isinstance(value, datetime):
        # Les dates en base sont en UTC naïf
        from datetime import timezone
        return value.replace(tzinfo=timezone.utc).astimezone(settings.tz).strftime(fmt)
    return str(value)


@app.template_filter("nl2html")
def _nl2html(value):
    return text_to_html(value or "")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("auth"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------------------
# Authentification
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        pwd = request.form.get("password", "")
        if settings.admin_password and hmac.compare_digest(pwd, settings.admin_password):
            session["auth"] = True
            session.permanent = True
            return redirect(request.args.get("next") or url_for("index"))
        flash("Mot de passe incorrect.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Newsletters
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
def index():
    with session_scope() as s:
        newsletters = s.scalars(select(Newsletter).order_by(desc(Newsletter.created_at)).limit(60)).all()
        last_run = s.scalars(select(Run).order_by(desc(Run.started_at)).limit(1)).first()
        sources_err = s.scalar(select(func.count(Source.id)).where(Source.enabled.is_(True), Source.last_status == "error"))
    # Tous les appels Claude du mois, y compris ceux des générations en échec
    month_cost_eur = usd_to_eur(month_spend_usd())
    budget_eur = settings.monthly_budget_eur
    return render_template(
        "index.html",
        newsletters=newsletters,
        last_run=last_run,
        month_cost_eur=month_cost_eur,
        budget_eur=budget_eur,
        over_budget=budget_eur > 0 and month_cost_eur >= budget_eur,
        sources_err=sources_err or 0,
        running=LOCK_FILE.exists(),
    )


@app.route("/newsletter/<int:nl_id>")
@login_required
def newsletter(nl_id: int):
    with session_scope() as s:
        nl = s.get(Newsletter, nl_id)
        if not nl:
            abort(404)
        items = s.execute(
            select(NewsletterItem, Item, Source)
            .join(Item, Item.id == NewsletterItem.item_id)
            .join(Source, Source.id == Item.source_id)
            .where(NewsletterItem.newsletter_id == nl_id)
        ).all()
    return render_template("newsletter.html", nl=nl, items=items)


@app.route("/newsletter/<int:nl_id>/resend", methods=["POST"])
@login_required
def newsletter_resend(nl_id: int):
    from mnews.db import utcnow
    from mnews.mailer import send_email

    with session_scope() as s:
        nl = s.get(Newsletter, nl_id)
        if not nl:
            abort(404)
        try:
            send_email(nl.subject, nl.body_text, settings.mail_recipients())
            nl.status = "sent"
            nl.sent_at = utcnow()
            flash("Newsletter envoyée.", "ok")
        except Exception as exc:  # noqa: BLE001
            flash(f"Échec de l'envoi : {exc}", "error")
    return redirect(url_for("newsletter", nl_id=nl_id))


# ---------------------------------------------------------------------------
# Déclenchement manuel
# ---------------------------------------------------------------------------

def _spawn_run(args: list[str]) -> None:
    """Lance le pipeline dans un processus séparé pour ne pas bloquer la requête web."""
    log_path = settings.log_dir / "web-trigger.log"
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as out:
        subprocess.Popen(
            [sys.executable, "-m", "mnews", *args],
            cwd=str(ROOT_DIR),
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


@app.route("/generate", methods=["POST"])
@login_required
def generate():
    mode = request.form.get("mode", "preview")
    if LOCK_FILE.exists():
        flash("Une génération est déjà en cours.", "error")
        return redirect(url_for("index"))
    if mode == "send":
        _spawn_run(["run", "--force", "--trigger", "web"])
        flash("Génération et envoi lancés. Rechargez la page dans une à trois minutes.", "ok")
    else:
        _spawn_run(["preview"])
        flash("Aperçu lancé (sans envoi). Rechargez la page dans une à trois minutes.", "ok")
    return redirect(url_for("index"))


@app.route("/trigger")
def trigger():
    """Déclenchement par URL secrète : /trigger?token=...&mode=send|preview"""
    token = request.args.get("token", "")
    if not settings.trigger_token or not hmac.compare_digest(token, settings.trigger_token):
        abort(403)
    if LOCK_FILE.exists():
        return jsonify({"ok": False, "error": "déjà en cours"}), 409
    mode = request.args.get("mode", "preview")
    if mode == "send":
        _spawn_run(["run", "--force", "--trigger", "web"])
    else:
        _spawn_run(["preview"])
    return jsonify({"ok": True, "mode": mode})


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

@app.route("/sources")
@login_required
def sources():
    with session_scope() as s:
        rows = s.scalars(select(Source).order_by(Source.type, Source.name)).all()
    return render_template("sources.html", sources=rows, types=SOURCE_TYPES)


@app.route("/sources/new", methods=["GET", "POST"])
@app.route("/sources/<int:src_id>/edit", methods=["GET", "POST"])
@login_required
def source_form(src_id: int | None = None):
    with session_scope() as s:
        src = s.get(Source, src_id) if src_id else None
        if src_id and not src:
            abort(404)
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            type_ = request.form.get("type", "rss")
            url = request.form.get("url", "").strip()
            lang = request.form.get("lang", "en")
            cfg_raw = request.form.get("config_json", "").strip() or "{}"
            try:
                cfg = json.loads(cfg_raw)
                if not isinstance(cfg, dict):
                    raise ValueError("doit être un objet JSON")
            except ValueError as exc:
                flash(f"Config JSON invalide : {exc}", "error")
                return render_template("source_form.html", src=src, types=SOURCE_TYPES, form=request.form)
            if type_ == "x" and not cfg.get("handle"):
                cfg["handle"] = url.lstrip("@")
            if not name or (not url and type_ not in ("hackernews",)):
                flash("Nom et URL sont obligatoires.", "error")
                return render_template("source_form.html", src=src, types=SOURCE_TYPES, form=request.form)
            if src is None:
                src = Source()
                s.add(src)
            src.name, src.type, src.url, src.lang = name, type_, url, lang
            src.enabled = request.form.get("enabled") == "on"
            src.config = cfg
            s.flush()
            flash("Source enregistrée.", "ok")
            return redirect(url_for("sources"))
    return render_template("source_form.html", src=src, types=SOURCE_TYPES, form=None)


@app.route("/sources/<int:src_id>/toggle", methods=["POST"])
@login_required
def source_toggle(src_id: int):
    with session_scope() as s:
        src = s.get(Source, src_id)
        if not src:
            abort(404)
        src.enabled = not src.enabled
    return redirect(url_for("sources"))


@app.route("/sources/<int:src_id>/delete", methods=["POST"])
@login_required
def source_delete(src_id: int):
    with session_scope() as s:
        src = s.get(Source, src_id)
        if not src:
            abort(404)
        has_items = s.scalar(select(func.count(Item.id)).where(Item.source_id == src_id))
        if has_items:
            src.enabled = False
            flash("Cette source a déjà des articles : elle a été désactivée plutôt que supprimée.", "ok")
        else:
            s.delete(src)
            flash("Source supprimée.", "ok")
    return redirect(url_for("sources"))


@app.route("/sources/<int:src_id>/test", methods=["POST"])
@login_required
def source_test(src_id: int):
    from datetime import timedelta

    from mnews.collectors import collect_source
    from mnews.db import utcnow

    with session_scope() as s:
        src = s.get(Source, src_id)
        if not src:
            abort(404)
        try:
            items = collect_source(src, utcnow() - timedelta(hours=72))
            sample = "; ".join(i.title[:60] for i in items[:3])
            flash(f"{src.name} : {len(items)} élément(s) sur 72 h. {sample}", "ok")
        except Exception as exc:  # noqa: BLE001
            flash(f"{src.name} : ERREUR {type(exc).__name__}: {exc}", "error")
    return redirect(url_for("sources"))


# ---------------------------------------------------------------------------
# Journal des exécutions
# ---------------------------------------------------------------------------

@app.route("/runs")
@login_required
def runs():
    with session_scope() as s:
        rows = s.scalars(select(Run).order_by(desc(Run.started_at)).limit(100)).all()
    return render_template("runs.html", runs=rows)


@app.route("/runs/<int:run_id>")
@login_required
def run_detail(run_id: int):
    with session_scope() as s:
        run = s.get(Run, run_id)
        if not run:
            abort(404)
        calls = s.scalars(select(LlmCall).where(LlmCall.run_id == run_id).order_by(LlmCall.id)).all()
    return render_template("run.html", run=run, calls=calls, usd_eur_rate=settings.usd_eur_rate)


@app.route("/items")
@login_required
def items():
    with session_scope() as s:
        rows = s.execute(
            select(Item, Source).join(Source, Source.id == Item.source_id)
            .order_by(desc(Item.fetched_at)).limit(200)
        ).all()
    return render_template("items.html", rows=rows)


@app.route("/health")
def health():
    return jsonify({"ok": True, "running": LOCK_FILE.exists()})


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
