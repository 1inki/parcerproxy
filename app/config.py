from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


load_dotenv()


def _csv_env(name: str) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass(slots=True)
class Settings:
    db_url: str = os.getenv("DB_URL", "sqlite:///app.db")
    github_token: str = os.getenv("GITHUB_TOKEN", "")
    github_queries: list[str] = field(default_factory=lambda: _csv_env("GITHUB_QUERIES") or ["proxy", "socks5", "mtproto", "shadowsocks"])
    github_code_pages: int = int(os.getenv("GITHUB_CODE_PAGES", "5"))
    github_repo_pages: int = int(os.getenv("GITHUB_REPO_PAGES", "5"))
    github_per_page: int = int(os.getenv("GITHUB_PER_PAGE", "50"))
    github_max_blob_bytes: int = int(os.getenv("GITHUB_MAX_BLOB_BYTES", "250000"))
    source_urls: list[str] = field(default_factory=lambda: _csv_env("SOURCE_URLS"))
    check_timeout_sec: float = float(os.getenv("CHECK_TIMEOUT_SEC", "4"))
    max_concurrent_checks: int = int(os.getenv("MAX_CONCURRENT_CHECKS", "100"))
    country_whitelist: list[str] = field(default_factory=lambda: [x.upper() for x in _csv_env("COUNTRY_WHITELIST")])
    country_blacklist: list[str] = field(default_factory=lambda: [x.upper() for x in _csv_env("COUNTRY_BLACKLIST")])
    schedule_minutes: int = int(os.getenv("SCHEDULE_MINUTES", "15"))

    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_admin_id: int = int(os.getenv("TELEGRAM_ADMIN_ID", "0"))
    telegram_report_minutes: int = int(os.getenv("TELEGRAM_REPORT_MINUTES", "30"))

    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()


settings = Settings()
