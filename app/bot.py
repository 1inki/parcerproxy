from __future__ import annotations

import re

import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.config import Settings
from app.pipeline import Pipeline
from app.storage import Storage

REPO_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")


class AdminBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.storage = Storage(settings.db_url)
        self.storage.init_db()
        self._running = False
        self._last_run_stats: dict[str, int] = {}
        self._start_time = time.time()

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
            await query.edit_message_text(self._render_stats(), parse_mode="HTML", reply_markup=self._menu())
        elif data == "countries":
            s = self.storage.dashboard_stats()
            text = "\n".join(f"{x['country']}: {x['count']}" for x in s["countries_top"]) or "Нет данных"
            await query.edit_message_text(f"🌍 Страны (top):\n{text}", reply_markup=self._menu())
        elif data == "queue":
            q = self.storage.repo_queue_stats()
            await query.edit_message_text(
                f"📥 Очередь\npending: {q['pending']}\nprocessing: {q['processing']}\ndone: {q['done']}\nfailed: {q['failed']}",
                reply_markup=self._menu(),
            )
        elif data == "top":
            rows = self.storage.top_alive(limit=20)
            lines = [f"{idx+1}. {r.proxy_type}://{r.host}:{r.port} [{r.country or '??'}] score={r.score:.1f}" for idx, r in enumerate(rows)]
            await query.edit_message_text("🧭 Топ-20 живых:\n" + ("\n".join(lines) or "Нет данных"), reply_markup=self._menu())

    async def force_run_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update.effective_user.id if update.effective_user else None):
            return
        if self._running:
            await update.effective_message.reply_text("⚠️ Ошибка: цикл парсинга уже запущен!")
            return
            
        await update.effective_message.reply_text("⏳ Запускаю принудительный цикл...")
        self._running = True
        try:
            pipeline = Pipeline(self.settings)
            stats = await pipeline.run_once()
            self._last_run_stats = stats
            await update.effective_message.reply_text(
                f"✅ Цикл завершён: {stats.get('alive', 0)} alive из {stats.get('candidates', 0)} кандидатов, {stats.get('saved', 0)} сохранено"
            )
        finally:
            self._running = False

    async def status_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update.effective_user.id if update.effective_user else None):
            return
        upt = time.time() - self._start_time
        upt_hours = int(upt // 3600)
        upt_mins = int((upt % 3600) // 60)
        
        if not self._last_run_stats:
            res_str = "Ещё не было завершённых циклов."
        else:
            s = self._last_run_stats
            res_str = f"Alive: {s.get('alive', 0)} / {s.get('candidates', 0)}, Saved: {s.get('saved', 0)}"
            
        status_msg = (
            f"🟢 <b>Статус системы</b>\n\n"
            f"⏱ Uptime: {upt_hours}ч {upt_mins}м\n"
            f"🏃 Текущее состояние: {'В процессе парсинга...' if self._running else 'Ожидание'}\n"
            f"🔄 Последний результат: {res_str}"
        )
        await update.effective_message.reply_html(status_msg, reply_markup=self._menu())

    async def scheduled_pipeline(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if self._running:
            return
            
        self._running = True
        t0 = time.time()
        try:
            pipeline = Pipeline(self.settings)
            stats = await pipeline.run_once()
            self._last_run_stats = stats
            dt = time.time() - t0
            
            if self.settings.telegram_admin_id > 0:
                await context.bot.send_message(
                    chat_id=self.settings.telegram_admin_id,
                    text=f"🔄 Авто-цикл: {stats.get('alive', 0)}/{stats.get('candidates', 0)} alive, {stats.get('saved', 0)} saved, {dt:.1f}s"
                )
        finally:
            self._running = False

    async def periodic_report(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if self.settings.telegram_admin_id <= 0:
            return
        await context.bot.send_message(chat_id=self.settings.telegram_admin_id, text=self._render_stats(), parse_mode="HTML", reply_markup=self._menu())


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
    app.job_queue.run_repeating(bot.periodic_report, interval=settings.telegram_report_minutes * 60, first=15)
    app.run_polling(close_loop=False)

def run_all_in_one(settings: Settings) -> None:
    if not settings.telegram_bot_token or settings.telegram_admin_id <= 0:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN_ID in .env")

    bot = AdminBot(settings)
    app = Application.builder().token(settings.telegram_bot_token).build()
    
    app.add_handler(CommandHandler("start", bot.start_cmd))
    app.add_handler(CommandHandler("stats", bot.stats_cmd))
    app.add_handler(CommandHandler("addrepo", bot.addrepo_cmd))
    app.add_handler(CommandHandler("force_run", bot.force_run_cmd))
    app.add_handler(CommandHandler("status", bot.status_cmd))
    app.add_handler(CallbackQueryHandler(bot.callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.text_handler))
    
    app.job_queue.run_repeating(bot.periodic_report, interval=settings.telegram_report_minutes * 60, first=15)
    app.job_queue.run_repeating(bot.scheduled_pipeline, interval=settings.schedule_minutes * 60, first=15)
    
    app.run_polling(close_loop=False)
