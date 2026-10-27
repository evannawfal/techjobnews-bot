from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class RawEvent(Base):
    __tablename__ = "raw_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(50), index=True)
    external_id: Mapped[str] = mapped_column(String(200), index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc))

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_raw_source_external_id"),
    )


class NormalizedEvent(Base):
    __tablename__ = "normalized_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(50), index=True)
    company: Mapped[str] = mapped_column(String(200), index=True)
    event_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True, index=True)
    employees: Mapped[int | None] = mapped_column(Integer, nullable=True)
    percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc))

    post_candidate: Mapped["PostCandidate"] = relationship(back_populates="normalized_event", uselist=False)
    posts: Mapped[list["Post"]] = relationship(back_populates="normalized_event")

    __table_args__ = (
        Index("ix_norm_source_company_date", "source", "company", "event_date"),
    )


class PostCandidate(Base):
    __tablename__ = "post_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    normalized_event_id: Mapped[int] = mapped_column(ForeignKey("normalized_events.id", ondelete="CASCADE"), unique=True)
    status: Mapped[str] = mapped_column(String(50), default="pending", index=True)  # pending|posted|skipped|error
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc))

    normalized_event: Mapped[NormalizedEvent] = relationship(back_populates="post_candidate")


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    normalized_event_id: Mapped[int] = mapped_column(ForeignKey("normalized_events.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(30), default="x")
    post_text: Mapped[str] = mapped_column(Text)
    platform_post_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    posted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="created", index=True)  # created|posted|dry_run|error
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    normalized_event: Mapped[NormalizedEvent] = relationship(back_populates="posts")


class HiringSnapshot(Base):
    """
    Daily snapshot of open roles for a company, used to detect hiring surges.
    """

    __tablename__ = "hiring_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company: Mapped[str] = mapped_column(String(200), index=True)
    snapshot_date: Mapped[dt.date] = mapped_column(Date, index=True)
    total_open_roles: Mapped[int] = mapped_column(Integer)
    engineering_open_roles: Mapped[int] = mapped_column(Integer)
    ai_open_roles: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc)
    )
    surge_flag: Mapped[bool] = mapped_column(default=False, index=True)
    surge_posted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("company", "snapshot_date", name="uq_hiring_company_date"),
    )


class TrendPost(Base):
    """
    Stores each trend research post produced by the agentic pipeline,
    used for deduplication and audit.
    """

    __tablename__ = "trend_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    headline_title: Mapped[str] = mapped_column(Text)
    headline_url: Mapped[str] = mapped_column(Text)
    headline_source: Mapped[str] = mapped_column(String(200))
    synthesis: Mapped[str] = mapped_column(Text)
    tweet_text: Mapped[str] = mapped_column(Text)
    reply_text: Mapped[str] = mapped_column(Text)
    video_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    platform_post_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(50), default="pending", index=True)  # pending|posted|dry_run|error
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc)
    )
    posted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OpinionPost(Base):
    """
    Stores opinion-style posts: bold claims/stances on controversial tech market topics.
    Distinct from TrendPost (analytical) — these take a side.
    """

    __tablename__ = "opinion_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    topic_headline: Mapped[str] = mapped_column(Text)       # Inspiring source headline
    topic_url: Mapped[str] = mapped_column(Text)
    stance: Mapped[str] = mapped_column(String(20))         # "for" | "against" | "reframe"
    tweet_text: Mapped[str] = mapped_column(Text)
    reply_text: Mapped[str] = mapped_column(Text)
    platform_post_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(50), default="pending", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc)
    )
    posted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

