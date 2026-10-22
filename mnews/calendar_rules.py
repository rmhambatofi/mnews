"""Règles d'envoi : pas le dimanche ni les jours fériés français."""
from __future__ import annotations

from datetime import date

import holidays

_FR = holidays.country_holidays("FR", language="fr")


def is_holiday(day: date) -> bool:
    return day in _FR


def holiday_name(day: date) -> str:
    return _FR.get(day) or ""


def should_send_today(day: date) -> tuple[bool, str]:
    """Renvoie (ok, raison du blocage)."""
    if day.weekday() == 6:
        return False, "dimanche"
    if is_holiday(day):
        return False, f"jour férié ({holiday_name(day)})"
    return True, ""
