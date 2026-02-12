from __future__ import annotations

import argparse
import json
import logging
import sys

from app.bot import run_bot
from app.config import settings
from app.pipeline import run_once_sync
from app.scheduler import run_daemon

logger = logging.getLogger(__name__)


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        stream=sys.stdout,
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Proxy intelligence pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run-once", help="Run one collection cycle")
    sub.add_parser("daemon", help="Run continuously with scheduler")
    sub.add_parser("run-bot", help="Run Telegram admin bot")
    return parser


def main() -> None:
    setup_logging(settings.log_level)
    parser = build_parser()
    args = parser.parse_args()

    logger.info("Starting command: %s", args.cmd)

    try:
        if args.cmd == "run-once":
            stats = run_once_sync(settings)
            logger.info("Run finished: %s", stats)
            print(json.dumps(stats, ensure_ascii=False, indent=2))
        elif args.cmd == "daemon":
            run_daemon(settings)
        elif args.cmd == "run-bot":
            run_bot(settings)
    except KeyboardInterrupt:
        logger.warning("Interrupted by user (Ctrl+C). Graceful shutdown.")


if __name__ == "__main__":
    main()
