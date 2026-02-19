from __future__ import annotations

import logging
from apscheduler.schedulers.blocking import BlockingScheduler

from app.config import Settings
from app.pipeline import run_once_sync

logger = logging.getLogger(__name__)


def run_daemon(settings: Settings) -> None:
    sched = BlockingScheduler(timezone="UTC")

    def _job() -> None:
        logger.info("Запуск задачи по расписанию (daemon mode).")
        stats = run_once_sync(settings)
        logger.info("[daemon] Результаты цикла: %s", stats)

    sched.add_job(_job, "interval", minutes=settings.schedule_minutes, max_instances=1, coalesce=True)
    _job()
    sched.start()
