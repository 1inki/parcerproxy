from __future__ import annotations

from apscheduler.schedulers.blocking import BlockingScheduler

from app.config import Settings
from app.pipeline import run_once_sync


def run_daemon(settings: Settings) -> None:
    sched = BlockingScheduler(timezone="UTC")

    def _job() -> None:
        stats = run_once_sync(settings)
        print(f"[daemon] {stats}")

    sched.add_job(_job, "interval", minutes=settings.schedule_minutes, max_instances=1, coalesce=True)
    _job()
    sched.start()
