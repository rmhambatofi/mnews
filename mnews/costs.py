"""Suivi des dépenses Claude : enregistrement de chaque appel et plafond mensuel."""
from __future__ import annotations

import contextvars
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select

from mnews.config import settings
from mnews.db import LlmCall, session_scope

log = logging.getLogger("mnews.costs")

# Exécution du pipeline en cours, pour rattacher les appels à leur run
_current_run_id: contextvars.ContextVar[int | None] = contextvars.ContextVar("mnews_run_id", default=None)


class BudgetExceeded(RuntimeError):
    pass


def start_run(run_id: int) -> contextvars.Token:
    return _current_run_id.set(run_id)


def end_run(token: contextvars.Token) -> None:
    _current_run_id.reset(token)


def record_call(step: str, model: str, status: str, input_tokens: int = 0,
                cache_write_tokens: int = 0, cache_read_tokens: int = 0, output_tokens: int = 0,
                cost_usd: float = 0.0, request_id: str = "", error: str | None = None) -> None:
    """Enregistre un appel. Ne doit jamais faire échouer le pipeline."""
    try:
        with session_scope() as session:
            session.add(LlmCall(
                run_id=_current_run_id.get(),
                step=step[:20],
                model=model[:60],
                status=status,
                input_tokens=input_tokens,
                cache_write_tokens=cache_write_tokens,
                cache_read_tokens=cache_read_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                request_id=request_id[:80],
                error=error,
            ))
    except Exception as exc:  # noqa: BLE001
        log.error("Impossible d'enregistrer l'appel Claude (%s, %.4f $) : %s", step, cost_usd, exc)


def month_start_utc() -> datetime:
    """Début du mois calendaire local, en UTC naïf (format des dates en base)."""
    start = datetime.now(settings.tz).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc).replace(tzinfo=None)


def month_spend_usd() -> float:
    with session_scope() as session:
        total = session.scalar(
            select(func.coalesce(func.sum(LlmCall.cost_usd), 0.0))
            .where(LlmCall.created_at >= month_start_utc())
        )
    return float(total or 0.0)


def usd_to_eur(usd: float) -> float:
    return usd * settings.usd_eur_rate


def check_budget() -> None:
    """Lève BudgetExceeded si le plafond mensuel est atteint."""
    budget = settings.monthly_budget_eur
    if budget <= 0:
        return
    spent = usd_to_eur(month_spend_usd())
    if spent >= budget:
        raise BudgetExceeded(
            f"Plafond mensuel atteint : {spent:.2f} € dépensés sur {budget:.2f} € "
            "(MONTHLY_BUDGET_EUR dans .env)"
        )
