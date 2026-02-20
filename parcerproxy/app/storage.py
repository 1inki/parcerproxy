from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models import Base, Observation, PipelineRun, Proxy, RepoTask


class Storage:
    def __init__(self, db_url: str) -> None:
        self.engine = create_engine(db_url, future=True)

    def init_db(self) -> None:
        Base.metadata.create_all(self.engine)

    def upsert_proxy(
        self,
        proxy_type: str,
        host: str,
        port: int,
        source: str,
        country: str | None,
        is_alive: bool,
        latency_ms: float | None,
    ) -> None:
        with Session(self.engine) as session:
            existing = session.scalar(
                select(Proxy).where(
                    Proxy.proxy_type == proxy_type,
                    Proxy.host == host,
                    Proxy.port == port,
                )
            )
            if not existing:
                existing = Proxy(
                    proxy_type=proxy_type,
                    host=host,
                    port=port,
                    source=source,
                    country=country,
                    is_alive=is_alive,
                    latency_ms=latency_ms,
                    success_rate=1.0 if is_alive else 0.0,
                    score=self._score(is_alive, latency_ms, 1.0 if is_alive else 0.0),
                    last_checked_at=datetime.utcnow(),
                )
                session.add(existing)
            else:
                old_sr = existing.success_rate
                new_sr = (old_sr * 0.8) + (1.0 if is_alive else 0.0) * 0.2
                existing.is_alive = is_alive
                existing.latency_ms = latency_ms
                existing.country = country or existing.country
                existing.source = source
                existing.success_rate = new_sr
                existing.score = self._score(is_alive, latency_ms, new_sr)
                existing.last_checked_at = datetime.utcnow()

            session.add(
                Observation(
                    proxy_type=proxy_type,
                    host=host,
                    port=port,
                    is_alive=is_alive,
                    latency_ms=latency_ms,
                    source=source,
                )
            )
            session.commit()

    def record_run(self, raw_sources: int, candidates: int, saved: int, alive: int) -> None:
        with Session(self.engine) as session:
            session.add(PipelineRun(raw_sources=raw_sources, candidates=candidates, saved=saved, alive=alive))
            session.commit()

    def enqueue_repo(self, repo_full_name: str, note: str | None = None) -> tuple[bool, str]:
        repo = repo_full_name.strip().lower()
        with Session(self.engine) as session:
            existing = session.scalar(select(RepoTask).where(RepoTask.repo_full_name == repo))
            if existing:
                if existing.status == "done":
                    return False, "already_analyzed"
                return False, "already_queued"
            session.add(RepoTask(repo_full_name=repo, status="pending", note=note))
            session.commit()
            return True, "queued"

    def get_pending_repos(self, limit: int = 100) -> list[str]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(RepoTask.repo_full_name)
                .where(RepoTask.status == "pending")
                .order_by(RepoTask.queued_at.asc())
                .limit(limit)
            ).all()
            return list(rows)

    def mark_repo_status(self, repo_full_name: str, status: str, note: str | None = None) -> None:
        repo = repo_full_name.strip().lower()
        with Session(self.engine) as session:
            row = session.scalar(select(RepoTask).where(RepoTask.repo_full_name == repo))
            if not row:
                return
            row.status = status
            row.note = note
            if status == "done":
                row.last_analyzed_at = datetime.utcnow()
            session.commit()

    def repo_queue_stats(self) -> dict[str, int]:
        with Session(self.engine) as session:
            counts = session.execute(select(RepoTask.status, func.count()).group_by(RepoTask.status)).all()
            out = {"pending": 0, "processing": 0, "done": 0, "failed": 0}
            for status, cnt in counts:
                out[status] = int(cnt)
            return out

    def top_alive(self, limit: int = 1000, countries: list[str] | None = None) -> list[Proxy]:
        with Session(self.engine) as session:
            stmt = select(Proxy).where(Proxy.is_alive.is_(True)).order_by(Proxy.score.desc())
            if countries:
                stmt = stmt.where(Proxy.country.in_(countries))
            return list(session.scalars(stmt.limit(limit)).all())

    def dashboard_stats(self) -> dict:
        with Session(self.engine) as session:
            total = session.scalar(select(func.count()).select_from(Proxy)) or 0
            alive = session.scalar(select(func.count()).select_from(Proxy).where(Proxy.is_alive.is_(True))) or 0
            countries_rows = session.execute(
                select(Proxy.country, func.count())
                .where(Proxy.is_alive.is_(True), Proxy.country.is_not(None))
                .group_by(Proxy.country)
                .order_by(func.count().desc())
                .limit(10)
            ).all()
            latest = session.scalar(select(PipelineRun).order_by(PipelineRun.created_at.desc()).limit(1))
            obs24 = session.scalar(
                select(func.count()).select_from(Observation).where(Observation.checked_at >= datetime.utcnow() - timedelta(hours=24))
            ) or 0
        return {
            "total_proxies": int(total),
            "alive_proxies": int(alive),
            "countries_top": [{"country": c, "count": int(n)} for c, n in countries_rows if c],
            "queue": self.repo_queue_stats(),
            "latest_run": {
                "raw_sources": latest.raw_sources if latest else 0,
                "candidates": latest.candidates if latest else 0,
                "saved": latest.saved if latest else 0,
                "alive": latest.alive if latest else 0,
                "at": latest.created_at.isoformat() if latest else None,
            },
            "observations_total": int(obs24),
        }

    @staticmethod
    def _score(is_alive: bool, latency_ms: float | None, success_rate: float) -> float:
        if not is_alive:
            return max(0.0, success_rate * 30)
        latency_score = max(0.0, 1000 - (latency_ms or 1000)) / 10
        return min(100.0, success_rate * 60 + latency_score)
