"""Journalisation : console + fichier tournant dans LOG_DIR."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from mnews.config import settings

_configured = False


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    global _configured
    logger = logging.getLogger("mnews")
    if _configured:
        return logger
    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            settings.log_dir / "mnews.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        logger.warning("Impossible d'écrire dans %s", settings.log_dir)

    _configured = True
    return logger
