from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler

from app.bot import run_all_in_one, run_bot
from app.config import settings
from app.pipeline import run_once_sync
from app.scheduler import run_daemon

def setup_logging(log_file: str = "parser.log", level: str = "INFO") -> None:
    """Настройка глубокого логирования в проекте."""
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Очистка существующих хендлеров
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    # Форматирование
    console_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
    file_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    # Консольный хендлер
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # Файловый хендлер (ротация)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5_000_000,
        backupCount=3,
        encoding="utf-8"
    )
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    logging.getLogger(__name__).info("Логирование инициализировано. Уровень: %s", level)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Proxy intelligence pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run-once", help="Run one collection cycle")
    sub.add_parser("daemon", help="Run continuously with scheduler")
    sub.add_parser("run-bot", help="Run Telegram admin bot")
    sub.add_parser("all-in-one", help="Run bot + scheduler in one process")
    return parser


def main() -> None:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args()
    logger = logging.getLogger(__name__)

    if args.cmd == "run-once":
        logger.info("Запуск единичного цикла парсинга.")
        stats = run_once_sync(settings)
        logger.info("Цикл завершен. Статистика: %s", json.dumps(stats, ensure_ascii=False))
    elif args.cmd == "daemon":
        run_daemon(settings)
    elif args.cmd == "run-bot":
        run_bot(settings)
    elif args.cmd == "all-in-one":
        run_all_in_one(settings)


if __name__ == "__main__":
    main()
