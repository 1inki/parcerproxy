# Proxy Intelligence Pipeline

Продвинутый парсер/агрегатор прокси с фокусом на глубокий мониторинг GitHub по ключевым словам, постоянное автообновление, фильтрацию по странам и админ-бота в Telegram.

## Что улучшено

- Глубокий GitHub мониторинг:
  - пагинация `search/code` и `search/repositories`;
  - приоритизация по обновляемым репозиториям;
  - детальный скан README + `git tree` + blob-файлов с proxy-паттернами;
  - очередь ручных GitHub repo на парсинг (из Telegram).
- Постоянный цикл обновления и сохранение метрик запусков.
- Фильтрация стран и ранжирование прокси.
- Telegram admin-only бот:
  - периодические отчёты;
  - меню статистики и кнопка обновления;
  - просмотр топ-стран, очереди и топа рабочих прокси;
  - добавление GitHub repo в очередь (`/addrepo` или просто ссылкой).

---

## ПОЛНАЯ ИНСТРУКЦИЯ С НУЛЯ (что и как запускать)

Ниже максимально практично: **какие команды вводить**, **что заполнить**, **в каком порядке запускать**.

## 1) Требования

- Linux/macOS (или WSL на Windows)
- Python 3.10+
- Git

Проверка версий:

```bash
python3 --version
git --version
```

## 2) Клонирование и вход в проект

```bash
git clone <URL_ВАШЕГО_РЕПО>
cd parcerproxy
```

Если репозиторий уже есть локально:

```bash
cd /path/to/parcerproxy
```

## 3) Виртуальное окружение и зависимости

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 4) Создание и заполнение `.env`

Скопируйте шаблон:

```bash
cp .env.example .env
```

Откройте `.env` и заполните минимум:

```env
DB_URL=sqlite:///app.db
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxx
GITHUB_QUERIES=proxy,socks5,mtproto,shadowsocks,ss,http,https
GITHUB_CODE_PAGES=5
GITHUB_REPO_PAGES=5
GITHUB_PER_PAGE=50
GITHUB_MAX_BLOB_BYTES=250000
SOURCE_URLS=
CHECK_TIMEOUT_SEC=4
MAX_CONCURRENT_CHECKS=100
COUNTRY_WHITELIST=
COUNTRY_BLACKLIST=RU,KP,IR
SCHEDULE_MINUTES=15
TELEGRAM_BOT_TOKEN=
TELEGRAM_ADMIN_ID=0
TELEGRAM_REPORT_MINUTES=30
```

### Что обязательно заполнить

1. **`GITHUB_TOKEN`** — обязательно, иначе GitHub collector не работает.
2. Для Telegram-бота обязательно:
   - **`TELEGRAM_BOT_TOKEN`**
   - **`TELEGRAM_ADMIN_ID`** (ваш числовой Telegram user id)

### Где взять значения

- `GITHUB_TOKEN`: GitHub → Settings → Developer settings → Personal access tokens.
- `TELEGRAM_BOT_TOKEN`: через `@BotFather`.
- `TELEGRAM_ADMIN_ID`: через бота типа `@userinfobot` или аналоги.

## 5) Первый пробный запуск парсера (один цикл)

```bash
python -m app.main run-once
```

Ожидаемо увидите JSON-статистику вида:
- `raw_sources`
- `candidates`
- `saved`
- `alive`

## 6) Запуск в постоянном режиме (daemon)

```bash
python -m app.main daemon
```

- Парсер будет идти по расписанию `SCHEDULE_MINUTES`.
- Останавливается `Ctrl + C`.

## 7) Запуск Telegram-бота (только админ)

Перед запуском убедитесь, что в `.env` заполнены:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ADMIN_ID`

Запуск:

```bash
python -m app.main run-bot
```

В Telegram:
1. Напишите боту `/start`
2. Откроется админ-меню:
   - Статистика
   - Обновить
   - Страны
   - Топ-20
   - Очередь GitHub

Добавление репозитория в очередь:
- командой: `/addrepo https://github.com/owner/repo`
- или просто отправьте ссылку на GitHub repo сообщением

Если репозиторий уже был:
- бот ответит, что он уже в очереди или уже анализировался.

## 8) Полезные команды проверки

Проверка, что код компилируется:

```bash
python -m compileall app tests
```

Запуск тестов:

```bash
pytest
```

## 9) Как ускорять/масштабировать

В `.env` повышайте постепенно:
- `GITHUB_CODE_PAGES`
- `GITHUB_REPO_PAGES`
- `GITHUB_PER_PAGE`
- `MAX_CONCURRENT_CHECKS`

Рекомендация: увеличивайте поэтапно и смотрите rate-limit GitHub + нагрузку сети/CPU.

## 10) Частые проблемы

1. **Пустая выдача**
   - не задан `GITHUB_TOKEN`
   - слишком узкие `GITHUB_QUERIES`

2. **Бот не отвечает**
   - неверный `TELEGRAM_BOT_TOKEN`
   - неверный `TELEGRAM_ADMIN_ID`
   - пишете боту не с админ-аккаунта

3. **Слишком медленно**
   - уменьшите `GITHUB_*_PAGES`
   - уменьшите `MAX_CONCURRENT_CHECKS`
   - увеличьте `CHECK_TIMEOUT_SEC` только если сеть нестабильна

---

## Команды кратко

```bash
python -m app.main run-once
python -m app.main daemon
python -m app.main run-bot
```

## Настройки

См. `.env.example`.

Ключевые параметры производительности GitHub:
- `GITHUB_CODE_PAGES`
- `GITHUB_REPO_PAGES`
- `GITHUB_PER_PAGE`
- `GITHUB_MAX_BLOB_BYTES`

## Идеи для next-level улучшений

- Перенос на Postgres + партиционирование таблиц наблюдений.
- Celery/RQ + Redis для масштабной очереди репозиториев.
- Prometheus/Grafana + SLO для freshness/uptime валидатора.
- HTTP API (FastAPI) для сайта/бота с key-based доступом.
