from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Proxy(Base):
    __tablename__ = "proxies"
    __table_args__ = (UniqueConstraint("proxy_type", "host", "port", name="uq_proxy"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proxy_type: Mapped[str] = mapped_column(String(16), index=True)
    host: Mapped[str] = mapped_column(String(255), index=True)
    port: Mapped[int] = mapped_column(Integer)
    country: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(255))
    is_alive: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    success_rate: Mapped[float] = mapped_column(Float, default=0.0)
    score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class Observation(Base):
    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proxy_type: Mapped[str] = mapped_column(String(16), index=True)
    host: Mapped[str] = mapped_column(String(255), index=True)
    port: Mapped[int] = mapped_column(Integer)
    is_alive: Mapped[bool] = mapped_column(Boolean, index=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(255))
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class RepoTask(Base):
    __tablename__ = "repo_tasks"
    __table_args__ = (UniqueConstraint("repo_full_name", name="uq_repo_task"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_full_name: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    last_analyzed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_sources: Mapped[int] = mapped_column(Integer, default=0)
    candidates: Mapped[int] = mapped_column(Integer, default=0)
    saved: Mapped[int] = mapped_column(Integer, default=0)
    alive: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
