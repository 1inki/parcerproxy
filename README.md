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

## Команды

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
