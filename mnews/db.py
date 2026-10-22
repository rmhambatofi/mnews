"""Modèles SQLAlchemy et accès à la base MySQL."""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from mnews.config import settings


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


LongText = Text().with_variant(Text(length=4_000_000), "mysql")


class Source(Base):
    """Une source surveillée (flux RSS, Hacker News, GitHub trending, compte X...)."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # rss | reddit | hackernews | github_trending | x
    type: Mapped[str] = mapped_column(String(30), nullable=False, default="rss")
    url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    lang: Mapped[str] = mapped_column(String(5), nullable=False, default="en")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Options spécifiques au type, en JSON (ex: {"handle": "AnthropicAI"})
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_fetched_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str] = mapped_column(String(20), nullable=False, default="never")
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    items: Mapped[list["Item"]] = relationship(back_populates="source")

    @property
    def config(self) -> dict:
        try:
            return json.loads(self.config_json or "{}")
        except json.JSONDecodeError:
            return {}

    @config.setter
    def config(self, value: dict) -> None:
        self.config_json = json.dumps(value, ensure_ascii=False)


class Item(Base):
    """Un article / post / dépôt collecté."""

    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("url_hash", name="uq_items_url_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    url_hash: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content: Mapped[Optional[str]] = mapped_column(LongText, nullable=True)
    lang: Mapped[str] = mapped_column(String(5), nullable=False, default="en")
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extra_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    source: Mapped["Source"] = relationship(back_populates="items")
    newsletter_links: Mapped[list["NewsletterItem"]] = relationship(back_populates="item")

    @property
    def extra(self) -> dict:
        try:
            return json.loads(self.extra_json or "{}")
        except json.JSONDecodeError:
            return {}


class Newsletter(Base):
    """Une newsletter générée (envoyée ou non)."""

    __tablename__ = "newsletters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Date locale (Paris) de la newsletter, format YYYY-MM-DD
    date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    body_text: Mapped[str] = mapped_column(LongText, nullable=False, default="")
    # draft | sent | failed | preview
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    model: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    candidates_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    items: Mapped[list["NewsletterItem"]] = relationship(
        back_populates="newsletter", cascade="all, delete-orphan"
    )


class NewsletterItem(Base):
    """Lien newsletter <-> article utilisé (sert à ne pas se répéter)."""

    __tablename__ = "newsletter_items"
    __table_args__ = (UniqueConstraint("newsletter_id", "item_id", name="uq_newsletter_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    newsletter_id: Mapped[int] = mapped_column(ForeignKey("newsletters.id"), nullable=False, index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), nullable=False, index=True)
    story_title: Mapped[str] = mapped_column(String(300), nullable=False, default="")

    newsletter: Mapped["Newsletter"] = relationship(back_populates="items")
    item: Mapped["Item"] = relationship(back_populates="newsletter_links")


class Run(Base):
    """Journal d'exécution du pipeline."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # cron | manual | cli | web
    trigger: Mapped[str] = mapped_column(String(20), nullable=False, default="cli")
    # running | success | skipped | failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    newsletter_id: Mapped[Optional[int]] = mapped_column(ForeignKey("newsletters.id"), nullable=True)
    log: Mapped[str] = mapped_column(LongText, nullable=False, default="")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Engine / sessions
# ---------------------------------------------------------------------------

_engine = None
_SessionLocal = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        settings.check_required("database_url")
        _engine = create_engine(
            settings.database_url, pool_pre_ping=True, pool_recycle=280, future=True
        )
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    Base.metadata.create_all(get_engine())


def get_source_by_url(session: Session, url: str) -> Optional[Source]:
    return session.scalar(select(Source).where(Source.url == url))
