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
  - добавление GitHub repo в очередь (`/addrepo` или просто ссылкой);
  - немедленный ручной запуск цикла (`/force_run`) и статус (`/status`).
- Архитектура **all-in-one**: бот и шедулер могут работать внутри единого фонового процесса с общим Event Loop.
- Уверенное покрытие тестами `pytest` (mock-тестирование маршрутизации, кэшей БД и парсинга).

## Команды

```bash
python -m app.main run-once    # Одиночный цикл парсинга
python -m app.main daemon      # Бесконечный цикл по расписанию (без бота)
python -m app.main run-bot     # Только Telegram-бот (без парсинга)
python -m app.main all-in-one  # Telegram-бот + Парсер по расписанию в одном процессе
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
