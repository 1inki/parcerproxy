# Proxy Intelligence Pipeline

Продвинутый парсер/агрегатор прокси с фокусом на глубокий мониторинг GitHub по ключевым словам, постоянное автообновление, фильтрацию по странам и админ-бота в Telegram.

## Что улучшено

- **Глубокий GitHub мониторинг**:
  - пагинация `search/code` и `search/repositories`;
  - приоритизация по обновляемым репозиториям;
  - детальный скан README + `git tree` + blob-файлов с proxy-паттернами;
  - очередь ручных GitHub repo на парсинг (из Telegram).
- **Расширенный парсинг**:
  - поддержка протоколов: HTTP, HTTPS, SOCKS4, SOCKS5, MTProto, Shadowsocks (ss://), V2Ray (vmess://).
  - парсинг форматов: URI, JSON-конфиги, Base64-обёртки, таблицы (IP Port).
- **Высокая производительность**:
  - параллельный сбор данных и валидация через `asyncio`.
  - локальное определение геолокации через базу GeoLite2 (без лимитов API).
  - пакетное сохранение данных (batch upsert) в SQLite.
- **Telegram admin-only бот**:
  - периодические отчёты, меню статистики, просмотр топа живых прокси.
  - управление очередью сканирования и ручной запуск цикла.

## Команды

```bash
python -m app.main run-once                 # Одиночный запуск (стандартный)
python -m app.main run-once --test          # Тестовый режим (5 репозиториев)
python -m app.main run-once --test --fast-test # Быстрый тест (1 репозиторий + запасной URL)
python -m app.main daemon                   # Бесконечный цикл по расписанию (без бота)
python -m app.main run-bot                  # Только Telegram-бот (без парсинга)
python -m app.main all-in-one               # Бот + Парсер в одном процессе
```

## Настройки

См. `.env.example`.

### Геолокация
Для быстрого определения страны по IP без лимитов парсер использует локальную базу GeoLite2:
1. Зарегистрируйтесь на сайте MaxMind и скачайте бесплатную базу: [GeoLite2-Country](https://dev.maxmind.com/geoip/geolite2-free-geolocation-data)
2. Распакуйте архив и положите файл `GeoLite2-Country.mmdb` в корень проекта.

Ключевые параметры производительности GitHub:
- `GITHUB_CODE_PAGES`
- `GITHUB_REPO_PAGES`
- `GITHUB_PER_PAGE`
- `GITHUB_MAX_BLOB_BYTES`

## Тестирование
Для запуска тестов установите зависимости из `requirements-dev.txt` и запустите pytest:
```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

## Идеи для next-level улучшений

- Перенос на Postgres + партиционирование таблиц наблюдений.
- Celery/RQ + Redis для масштабной очереди репозиториев.
- Prometheus/Grafana + SLO для freshness/uptime валидатора.
- HTTP API (FastAPI) для сайта/бота с key-based доступом.
