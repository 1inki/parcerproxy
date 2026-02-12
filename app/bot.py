from __future__ import annotations

import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.config import Settings
from app.storage import Storage

REPO_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")
logger = logging.getLogger(__name__)


def _is_not_modified_error(exc: Exception) -> bool:
    return isinstance(exc, BadRequest) and "Message is not modified" in str(exc)


class AdminBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.storage = Storage(settings.db_url)
        self.storage.init_db()

    def _is_admin(self, user_id: int | None) -> bool:
        return bool(user_id) and user_id == self.settings.telegram_admin_id

    def _menu(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📊 Статистика", callback_data="stats"), InlineKeyboardButton("🔄 Обновить", callback_data="refresh")],
                [InlineKeyboardButton("🌍 Страны", callback_data="countries"), InlineKeyboardButton("🧭 Топ-20", callback_data="top")],
                [InlineKeyboardButton("📥 Очередь GitHub", callback_data="queue")],
            ]
        )

    def _render_stats(self) -> str:
        s = self.storage.dashboard_stats()
        countries = ", ".join(f"{x['country']}:{x['count']}" for x in s["countries_top"][:8]) or "n/a"
        q = s["queue"]
        latest = s["latest_run"]
        return (
            "<b>Proxy Parser Dashboard</b>\n"
            f"Всего прокси: <b>{s['total_proxies']}</b>\n"
            f"Живых: <b>{s['alive_proxies']}</b>\n"
            f"Очередь repos — pending:{q['pending']} processing:{q['processing']} done:{q['done']} failed:{q['failed']}\n"
            f"Последний цикл: sources={latest['raw_sources']} candidates={latest['candidates']} saved={latest['saved']} alive={latest['alive']}\n"
            f"Топ стран: {countries}"
        )

    async def _safe_edit_text(
        self,
        query,
        text: str,
        parse_mode: str | None = None,
    ) -> None:
        try:
            await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=self._menu())
        except Exception as exc:
            if _is_not_modified_error(exc):
                await query.answer("Данные не изменились", show_alert=False)
                return
            raise

    async def start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update.effective_user.id if update.effective_user else None):
            return
        await update.effective_message.reply_text(
            "Админ-панель парсера. Можно отправить GitHub ссылку для постановки в очередь.",
            reply_markup=self._menu(),
        )

    async def stats_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update.effective_user.id if update.effective_user else None):
            return
        await update.effective_message.reply_html(self._render_stats(), reply_markup=self._menu())

    async def addrepo_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update.effective_user.id if update.effective_user else None):
            return
        arg = " ".join(context.args).strip()
        if not arg:
            await update.effective_message.reply_text("Использование: /addrepo https://github.com/owner/repo")
            return
        await self._enqueue_by_text(update, arg)

    async def text_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update.effective_user.id if update.effective_user else None):
            return
        text = update.effective_message.text or ""
        if "github.com/" in text:
            await self._enqueue_by_text(update, text)

    async def _enqueue_by_text(self, update: Update, text: str) -> None:
        m = REPO_RE.search(text)
        if not m:
            await update.effective_message.reply_text("Не нашёл корректный GitHub repo URL.")
            return
        repo = m.group(1).rstrip("/").lower()
        created, reason = self.storage.enqueue_repo(repo, note="from_telegram_admin")
        if created:
            await update.effective_message.reply_text(f"✅ Репозиторий {repo} добавлен в очередь.")
        elif reason == "already_analyzed":
            await update.effective_message.reply_text(f"ℹ️ {repo} уже был проанализирован ранее.")
        else:
            await update.effective_message.reply_text(f"ℹ️ {repo} уже есть в очереди.")

    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query:
            return
        await query.answer()
        if not self._is_admin(query.from_user.id if query.from_user else None):
            return

        data = query.data or ""
        if data in {"stats", "refresh"}:
            await self._safe_edit_text(query, self._render_stats(), parse_mode="HTML")
        elif data == "countries":
            s = self.storage.dashboard_stats()
            text = "\n".join(f"{x['country']}: {x['count']}" for x in s["countries_top"]) or "Нет данных"
            await self._safe_edit_text(query, f"🌍 Страны (top):\n{text}")
        elif data == "queue":
            q = self.storage.repo_queue_stats()
            await self._safe_edit_text(
                query,
                f"📥 Очередь\npending: {q['pending']}\nprocessing: {q['processing']}\ndone: {q['done']}\nfailed: {q['failed']}",
            )
        elif data == "top":
            rows = self.storage.top_alive(limit=20)
            lines = [f"{idx+1}. {r.proxy_type}://{r.host}:{r.port} [{r.country or '??'}] score={r.score:.1f}" for idx, r in enumerate(rows)]
            await self._safe_edit_text(query, "🧭 Топ-20 живых:\n" + ("\n".join(lines) or "Нет данных"))

    async def periodic_report(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if self.settings.telegram_admin_id <= 0:
            return
        await context.bot.send_message(chat_id=self.settings.telegram_admin_id, text=self._render_stats(), parse_mode="HTML", reply_markup=self._menu())


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled bot error. update=%s", update, exc_info=context.error)


def run_bot(settings: Settings) -> None:
    if not settings.telegram_bot_token or settings.telegram_admin_id <= 0:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN_ID in .env")

    bot = AdminBot(settings)
    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", bot.start_cmd))
    app.add_handler(CommandHandler("stats", bot.stats_cmd))
    app.add_handler(CommandHandler("addrepo", bot.addrepo_cmd))
    app.add_handler(CallbackQueryHandler(bot.callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.text_handler))
    app.add_error_handler(_on_error)
    app.job_queue.run_repeating(bot.periodic_report, interval=settings.telegram_report_minutes * 60, first=15)
    app.run_polling(close_loop=False)
