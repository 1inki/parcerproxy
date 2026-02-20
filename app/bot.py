from __future__ import annotations

import re

import io
import logging

from telegram import Document, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.config import Settings
from app.storage import Storage
from app.pipeline import Pipeline

logger = logging.getLogger(__name__)

REPO_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")


class AdminBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.storage = Storage(settings.db_url)
        self.storage.init_db()

    def _is_admin(self, user_id: int | None) -> bool:
        return bool(user_id) and user_id == self.settings.telegram_admin_id

    def _menu(self) -> InlineKeyboardMarkup:
        is_active = self.storage.get_config("parser_active", "false") == "true"
        parser_btn = InlineKeyboardButton("⏸ Стоп парсер", callback_data="stop_parser") if is_active else InlineKeyboardButton("▶️ Старт парсер", callback_data="start_parser")
        
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📊 Статистика", callback_data="stats"), InlineKeyboardButton("🔄 Обновить", callback_data="refresh")],
                [parser_btn, InlineKeyboardButton("🧪 Быстрый тест", callback_data="test_run")],
                [InlineKeyboardButton("🗑 Очистка мертвых", callback_data="prune_db"), InlineKeyboardButton("🧭 Топ-20", callback_data="top")],
                [InlineKeyboardButton("🌍 Страны", callback_data="countries"), InlineKeyboardButton("📥 Очередь", callback_data="queue")],
                [InlineKeyboardButton("💾 Живые CSV", callback_data="export_csv"), InlineKeyboardButton("💾 Сырая База", callback_data="export_raw_csv")],
            ]
        )

    def _render_stats(self) -> str:
        s = self.storage.dashboard_stats()
        countries = ", ".join(f"{x['country']}:{x['count']}" for x in s["countries_top"][:8]) or "Нет данных"
        q = s["queue"]
        latest = s["latest_run"]
        status = "🟢 <b>АКТИВЕН</b>" if self.storage.get_config("parser_active", "false") == "true" else "🔴 <b>ОСТАНОВЛЕН</b>"
        return (
            f"🛡 <b>Proxy Intelligence Dashboard</b>\n\n"
            f"Статус парсера: {status}\n\n"
            f"🔋 <b>Состояние Базы</b>\n"
            f"┣ 🟢 Живых (рабочих): <b>{s['alive_proxies']}</b>\n"
            f"┗ 🗑 Всего накоплено: <b>{s['total_proxies']}</b>\n\n"
            f"🌍 <b>Топ стран (живые)</b>\n"
            f"┗ {countries}\n\n"
            f"📥 <b>Очередь репозиториев GitHub</b>\n"
            f"┗ ⏳ Ожидают: {q['pending']} | 🔄 В работе: {q['processing']} | ✅ Готово: {q['done']}\n\n"
            f"⏱ <b>Последний цикл парсинга</b>\n"
            f"┣ 🔍 Найдено источников: {latest['raw_sources']}\n"
            f"┣ 🧩 Найдено прокси: {latest['candidates']}\n"
            f"┗ ⚡ Из них живых: <b>{latest['alive']}</b>"
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

    async def _safe_edit(self, query, text, reply_markup=None, parse_mode=None):
        try:
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                logger.error("Error editing message: %s", e)

    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query:
            return
        await query.answer()
        if not self._is_admin(query.from_user.id if query.from_user else None):
            return

        data = query.data or ""
        if data in {"stats", "refresh"}:
            await self._safe_edit(query, self._render_stats(), parse_mode="HTML", reply_markup=self._menu())
        elif data.startswith("countries"):
            s = self.storage.dashboard_stats()
            blacklist = self.storage.get_country_blacklist(self.settings.country_blacklist)
            kbd = []
            for item in s["countries_top"][:10]:
                c = item["country"]
                btn_text = f"🚫 Бан {c}" if c not in blacklist else f"✅ Разбанить {c}"
                cb_data = f"ban:{c}" if c not in blacklist else f"unban:{c}"
                kbd.append([InlineKeyboardButton(f"{c}: {item['count']} шт.", callback_data="ignore"), InlineKeyboardButton(btn_text, callback_data=cb_data)])
            
            kbd.append([InlineKeyboardButton("🔙 Назад", callback_data="stats")])
            await query.edit_message_text(f"🌍 Страны (top 10):", reply_markup=InlineKeyboardMarkup(kbd))
        elif data.startswith("ban:"):
            country = data.split(":")[1]
            self.storage.add_country_blacklist(country)
            await query.answer(f"Страна {country} добавлена в блэклист")
            query.data = "countries"
            await self._refresh_countries(query)
        elif data.startswith("unban:"):
            country = data.split(":")[1]
            self.storage.remove_country_blacklist(country)
            await query.answer(f"Страна {country} удалена из блэклиста")
            query.data = "countries"
            await self._refresh_countries(query)
        elif data == "queue":
            q = self.storage.repo_queue_stats()
            await self._safe_edit(
                query,
                f"📥 Очередь\npending: {q['pending']}\nprocessing: {q['processing']}\ndone: {q['done']}\nfailed: {q['failed']}",
                reply_markup=self._menu(),
            )
        elif data == "export_csv":
            rows = self.storage.top_alive(limit=999999)
            if not rows:
                await query.answer("Нет ЖИВЫХ прокси для экспорта. Скорее всего, проверенные прокси из базы оказались нерабочими (мертвыми) и были отфильтрованы.", show_alert=True)
                return
            csv_content = io.StringIO()
            csv_content.write("type,host,port,country,latency_ms,score,source\n")
            for r in rows:
                csv_content.write(f"{r.proxy_type},{r.host},{r.port},{r.country or ''},{r.latency_ms or ''},{r.score:.1f},{r.source}\n")
            csv_bytes = io.BytesIO(csv_content.getvalue().encode('utf-8'))
            csv_bytes.name = "proxies.csv"
            await context.bot.send_document(chat_id=query.message.chat_id, document=csv_bytes, caption="Все живые прокси")
            await query.answer()
        elif data == "export_raw_csv":
            rows = self.storage.all_proxies()
            if not rows:
                await query.answer("Сырая база пуста.", show_alert=True)
                return
            csv_content = io.StringIO()
            csv_content.write("type,host,port,country,latency_ms,score,is_alive,source\n")
            for r in rows:
                csv_content.write(f"{r.proxy_type},{r.host},{r.port},{r.country or ''},{r.latency_ms or ''},{r.score:.1f},{r.is_alive},{r.source}\n")
            csv_bytes = io.BytesIO(csv_content.getvalue().encode('utf-8'))
            csv_bytes.name = "raw_proxies.csv"
            await context.bot.send_document(chat_id=query.message.chat_id, document=csv_bytes, caption="Вся сырая база прокси")
            await query.answer()
        elif data == "test_run":
            await self._safe_edit(query, "🧪 Запускаю быстрый тест <b>(2 репо)</b>...\nОжидайте, это займет около минуты...", parse_mode="HTML")
            context.job_queue.run_once(self._run_test_job, 1, data=query.message.chat_id)
        elif data == "start_parser":
            self.storage.set_config("parser_active", "true")
            # Clear existing background jobs to avoid duplicates just in case
            for job in context.job_queue.jobs():
                if job.name == "run_parser_job":
                    job.schedule_removal()
            # Start the repeating job. The parser runs immediately after 5 seconds, then every schedule_minutes.
            context.job_queue.run_repeating(
                self.run_parser_job, 
                interval=self.settings.schedule_minutes * 60, 
                first=5, 
                name="run_parser_job"
            )
            await self._safe_edit(query, "✅ Парсер <b>ЗАПУЩЕН</b> в фоновом режиме.\n\n" + self._render_stats(), reply_markup=self._menu(), parse_mode="HTML")
        elif data == "stop_parser":
            self.storage.set_config("parser_active", "false")
            # Locate the scheduled parser job and literally cancel it so it stops hanging and consuming resources
            for job in context.job_queue.jobs():
                if job.name == "run_parser_job":
                    job.schedule_removal()
            await self._safe_edit(query, "⏸ Парсер <b>ОСТАНОВЛЕН</b>. Фоновые задачи отменены.\n\n" + self._render_stats(), reply_markup=self._menu(), parse_mode="HTML")
        elif data == "prune_db":
            deleted = self.storage.prune_dead()
            await query.answer(f"Удалено {deleted} мертвых прокси", show_alert=True)
            await self._safe_edit(query, self._render_stats(), parse_mode="HTML", reply_markup=self._menu())
        elif data == "top":
            rows = self.storage.top_alive(limit=20)
            lines = [f"{idx+1}. {r.proxy_type}://{r.host}:{r.port} [{r.country or '??'}] score={r.score:.1f}" for idx, r in enumerate(rows)]
            await self._safe_edit(query, "🧭 Топ-20 живых:\n" + ("\n".join(lines) or "Нет данных"), reply_markup=self._menu())

    async def periodic_report(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if self.settings.telegram_admin_id <= 0:
            return
        await context.bot.send_message(chat_id=self.settings.telegram_admin_id, text=self._render_stats(), parse_mode="HTML", reply_markup=self._menu())

    async def _refresh_countries(self, query):
            s = self.storage.dashboard_stats()
            blacklist = self.storage.get_country_blacklist(self.settings.country_blacklist)
            kbd = []
            for item in s["countries_top"][:10]:
                c = item["country"]
                btn_text = f"🚫 Бан {c}" if c not in blacklist else f"✅ Разбанить {c}"
                cb_data = f"ban:{c}" if c not in blacklist else f"unban:{c}"
                kbd.append([InlineKeyboardButton(f"{c}: {item['count']} шт.", callback_data="ignore"), InlineKeyboardButton(btn_text, callback_data=cb_data)])
            
            kbd.append([InlineKeyboardButton("🔙 Назад", callback_data="stats")])
            await self._safe_edit(query, f"🌍 Страны (top 10):", reply_markup=InlineKeyboardMarkup(kbd))

    async def run_parser_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if self.storage.get_config("parser_active", "false") != "true":
            logger.info("Parser job skipped because parser is inactive")
            return
        
        logger.info("Starting background parser job")
        # Сравниваем живые до и после цикла для Алерта
        stats_before = self.storage.dashboard_stats()
        alive_before = stats_before["alive_proxies"]

        pipeline = Pipeline(self.settings)
        try:
            stats = await pipeline.run_once()
            logger.info("Parser cycle completed: %s", stats)
        except Exception as e:
            logger.error("Error during parser cycle: %s", e, exc_info=True)
            return

        stats_after = self.storage.dashboard_stats()
        alive_after = stats_after["alive_proxies"]

        # Alert if drops by > 15% and has decent base (>100 to avoid flapping on empty db)
        if alive_before > 100 and alive_after < alive_before * 0.85:
            if self.settings.telegram_admin_id > 0:
                await context.bot.send_message(
                    chat_id=self.settings.telegram_admin_id,
                    text=f"🚨 <b>ВНИМАНИЕ! Резкое падение живых прокси!</b>\n"
                         f"Было: {alive_before}\n"
                         f"Стало: {alive_after}\n"
                         f"Падение: {alive_before - alive_after} шт.",
                    parse_mode="HTML",
                    reply_markup=self._menu()
                )

    async def _run_test_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = context.job.data
        pipeline = Pipeline(self.settings)
        try:
            stats = await pipeline.run_once(test_mode=True)
            text = (
                f"✅ <b>Тестовый запуск завершен!</b>\n\n"
                f"Исходников: {stats['raw_sources']}\n"
                f"Кандидатов: {stats['candidates']}\n"
                f"Сохранено: {stats['saved']}\n"
                f"Живых: {stats['alive']}\n"
            )
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=self._menu())
        except Exception as e:
            logger.error("Error during test run: %s", e, exc_info=True)
            await context.bot.send_message(chat_id=chat_id, text=f"❌ Ошибка тестового запуска: {e}", reply_markup=self._menu())

def run_bot(settings: Settings) -> None:
    if not settings.telegram_bot_token or settings.telegram_admin_id <= 0:
        raise RuntimeError(
            "Set TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN_ID in .env. "
            "If you don't know your ID, message @userinfobot on Telegram to get it."
        )

    bot = AdminBot(settings)
    bot.storage.set_config("parser_active", "false")
    
    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", bot.start_cmd))
    app.add_handler(CommandHandler("stats", bot.stats_cmd))
    app.add_handler(CommandHandler("addrepo", bot.addrepo_cmd))
    app.add_handler(CallbackQueryHandler(bot.callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.text_handler))
    
    app.job_queue.run_repeating(bot.periodic_report, interval=settings.telegram_report_minutes * 60, first=15)
    # Removing the initial run_repeating for parser. 
    # It will be triggered exclusively by the "start_parser" button.
    
    app.run_polling(close_loop=False)
