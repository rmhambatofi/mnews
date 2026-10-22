"""Configuration chargée depuis les variables d'environnement (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    val = _env(name, "1" if default else "0").lower()
    return val in ("1", "true", "yes", "on")


@dataclass
class Settings:
    tz_name: str = field(default_factory=lambda: _env("TZ", "Europe/Paris"))
    log_dir: Path = field(default_factory=lambda: ROOT_DIR / _env("LOG_DIR", "logs"))

    database_url: str = field(default_factory=lambda: _env("DATABASE_URL"))

    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    claude_model: str = field(default_factory=lambda: _env("CLAUDE_MODEL", "claude-opus-5-5"))
    effort_select: str = field(default_factory=lambda: _env("CLAUDE_EFFORT_SELECT", "medium"))
    effort_write: str = field(default_factory=lambda: _env("CLAUDE_EFFORT_WRITE", "high"))

    smtp_host: str = field(default_factory=lambda: _env("SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: _env_int("SMTP_PORT", 465))
    smtp_ssl: bool = field(default_factory=lambda: _env_bool("SMTP_SSL", True))
    smtp_user: str = field(default_factory=lambda: _env("SMTP_USER"))
    smtp_password: str = field(default_factory=lambda: _env("SMTP_PASSWORD"))
    mail_from: str = field(default_factory=lambda: _env("MAIL_FROM"))
    mail_from_name: str = field(default_factory=lambda: _env("MAIL_FROM_NAME", "News IA"))
    mail_to: str = field(default_factory=lambda: _env("MAIL_TO"))
    alert_to: str = field(default_factory=lambda: _env("ALERT_TO"))

    admin_password: str = field(default_factory=lambda: _env("ADMIN_PASSWORD"))
    secret_key: str = field(default_factory=lambda: _env("SECRET_KEY"))
    trigger_token: str = field(default_factory=lambda: _env("TRIGGER_TOKEN"))

    lookback_hours: int = field(default_factory=lambda: _env_int("LOOKBACK_HOURS", 36))
    max_stories: int = field(default_factory=lambda: _env_int("MAX_STORIES", 8))
    max_repos: int = field(default_factory=lambda: _env_int("MAX_REPOS", 5))
    history_days: int = field(default_factory=lambda: _env_int("HISTORY_DAYS", 10))
    x_rss_template: str = field(
        default_factory=lambda: _env("X_RSS_TEMPLATE", "https://nitter.net/{handle}/rss")
    )
    github_token: str = field(default_factory=lambda: _env("GITHUB_TOKEN"))

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)

    def mail_recipients(self) -> list[str]:
        return [a.strip() for a in self.mail_to.split(",") if a.strip()]

    def alert_recipients(self) -> list[str]:
        return [a.strip() for a in (self.alert_to or self.mail_to).split(",") if a.strip()]

    def check_required(self, *names: str) -> None:
        missing = [n for n in names if not getattr(self, n)]
        if missing:
            raise RuntimeError(
                "Variables manquantes dans .env : " + ", ".join(m.upper() for m in missing)
            )


settings = Settings()
