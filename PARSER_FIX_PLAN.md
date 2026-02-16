> Этот документ является пошаговым руководством по устранению **всех** проблем, описанных в [[🚨 Критические Проблемы, Дыры и Ограничения Парсера]].

> Архитектура системы описана в [[Схема]].

  

---

  

## 📐 Глубокий Вывод: Системный Анализ

  

Прежде чем переходить к исправлениям, зафиксируем **корневые причины** всех 14 проблем. Они сводятся к **четырём фундаментальным дефектам проектирования:**

  

### A. Отсутствие устойчивости к внешним сервисам

Парсер полностью зависит от внешних API (GitHub, ipapi.co, httpbin.org) и **не имеет ни одного механизма защиты**: нет retry, нет fallback, нет кэширования, нет обработки лимитов. Любой сбой внешнего сервиса — и цикл работает "в пустоту".

  

### B. Ложная универсальность валидации

Система заявляет поддержку шести протоколов (`http`, `https`, `socks4`, `socks5`, `mtproto`, `ss`), но валидатор реально способен проверить только **два** из них. Это не баг — это **архитектурный обман**, из-за которого 60-80% собранных данных бесполезны.

  

### C. Отсутствие наблюдаемости (Observability)

Ни логов, ни метрик, ни алертов. Единственный способ узнать, что парсер сломан — зайти в Telegram-бота и заметить, что числа не меняются. Это недопустимо для сервиса, работающего 24/7.

  

### D. Линейная (а не конвейерная) архитектура

Все этапы выполняются строго последовательно, хотя по своей природе могут работать параллельно. Это превращает 15-секундную задачу в 5-минутную.

  

---

  

## 🗓 Порядок Выполнения

  

Исправления упорядочены по **приоритету воздействия**: сначала те, что дают максимальный эффект при минимальных усилиях.

  

```mermaid

graph TD

    A["Фаза 1: Спасение данных"] --> B["Фаза 2: Устойчивость"]

    B --> C["Фаза 3: Производительность"]

    C --> D["Фаза 4: Качество кода"]

    D --> E["Фаза 5: Архитектура"]

  

    A --- A1["#2 Валидация SOCKS/MTProto"]

    A --- A2["#8 Normalizer умеет больше форматов"]

  

    B --- B1["#1 GitHub Rate Limit"]

    B --- B2["#14 Fallback endpoint-ы"]

    B --- B3["#7 Retry-логика"]

  

    C --- C1["#3 Параллельный сбор"]

    C --- C2["#4 Локальный GeoIP"]

    C --- C3["#5 Batch-запись в БД"]

  

    D --- D1["#6 Логирование"]

    D --- D2["#10 datetime fix"]

    D --- D3["#12 Тесты"]

  

    E --- E1["#9 TTL репозиториев"]

    E --- E2["#13 Очистка мёртвых прокси"]

    E --- E3["#11 Связь бота и парсера"]

```

  

---

  

## Фаза 1: Спасение Данных

  

> Цель: сделать так, чтобы данные, которые парсер **уже собирает**, не выбрасывались впустую.

  

---

  

### 🔧 Исправление #2 — Валидация SOCKS/MTProto/Shadowsocks

  

**Проблема**: [[PARSER_ISSUES#2. Валидатор Проверяет Только HTTP — socks5/mtproto/ss Невалидны]]

  

**Файлы для изменения:**

- `requirements.txt` — добавить зависимости

- `app/validator.py` — переписать логику проверки

  

#### Шаг 1: Добавить зависимости

  

```txt

# requirements.txt — добавить:

httpx-socks[asyncio]==0.9.1

```

  

`httpx-socks` — это расширение для `httpx`, добавляющее поддержку SOCKS4/SOCKS5-прокси. Устанавливается как drop-in transport.

  

#### Шаг 2: Создать стратегию валидации по типу протокола

  

Текущий код:

```python

# app/validator.py — БЫЛО:

proxy_url = f"{candidate.proxy_type}://{candidate.host}:{candidate.port}"

async with httpx.AsyncClient(proxy=proxy_url, timeout=timeout_sec) as client:

    r = await client.get("https://httpbin.org/ip")

```

  

Нужно заменить на мультипротокольную проверку:

  

```python

# app/validator.py — СТАЛО:

import asyncio

import time

from dataclasses import dataclass

import httpx

from httpx_socks import AsyncProxyTransport

from app.normalizer import ProxyCandidate

  

CHECK_URLS = [

    "https://httpbin.org/ip",

    "https://ifconfig.me/ip",

    "https://api.ipify.org",

]

  

@dataclass(slots=True)

class ValidationResult:

    candidate: ProxyCandidate

    is_alive: bool

    latency_ms: float | None

  
  

async def _check_http(candidate: ProxyCandidate, timeout_sec: float) -> ValidationResult:

    """Проверка HTTP/HTTPS прокси через стандартный httpx."""

    proxy_url = f"{candidate.proxy_type}://{candidate.host}:{candidate.port}"

    for url in CHECK_URLS:

        t0 = time.perf_counter()

        try:

            async with httpx.AsyncClient(proxy=proxy_url, timeout=timeout_sec, follow_redirects=True) as client:

                r = await client.get(url)

                if r.status_code < 500:

                    dt = (time.perf_counter() - t0) * 1000

                    return ValidationResult(candidate=candidate, is_alive=True, latency_ms=dt)

        except Exception:

            continue

    return ValidationResult(candidate=candidate, is_alive=False, latency_ms=None)

  
  

async def _check_socks(candidate: ProxyCandidate, timeout_sec: float) -> ValidationResult:

    """Проверка SOCKS4/SOCKS5 прокси через httpx-socks."""

    proxy_url = f"{candidate.proxy_type}://{candidate.host}:{candidate.port}"

    transport = AsyncProxyTransport.from_url(proxy_url)

    for url in CHECK_URLS:

        t0 = time.perf_counter()

        try:

            async with httpx.AsyncClient(transport=transport, timeout=timeout_sec) as client:

                r = await client.get(url)

                if r.status_code < 500:

                    dt = (time.perf_counter() - t0) * 1000

                    return ValidationResult(candidate=candidate, is_alive=True, latency_ms=dt)

        except Exception:

            continue

    return ValidationResult(candidate=candidate, is_alive=False, latency_ms=None)

  
  

async def _check_tcp_only(candidate: ProxyCandidate, timeout_sec: float) -> ValidationResult:

    """Проверка MTProto/Shadowsocks — только TCP-connectivity."""

    t0 = time.perf_counter()

    try:

        reader, writer = await asyncio.wait_for(

            asyncio.open_connection(candidate.host, candidate.port),

            timeout=timeout_sec,

        )

        dt = (time.perf_counter() - t0) * 1000

        writer.close()

        await writer.wait_closed()

        return ValidationResult(candidate=candidate, is_alive=True, latency_ms=dt)

    except Exception:

        return ValidationResult(candidate=candidate, is_alive=False, latency_ms=None)

  
  

async def _check(candidate: ProxyCandidate, timeout_sec: float) -> ValidationResult:

    """Маршрутизатор: выбирает стратегию проверки по типу протокола."""

    if candidate.proxy_type in ("http", "https"):

        return await _check_http(candidate, timeout_sec)

    elif candidate.proxy_type in ("socks4", "socks5"):

        return await _check_socks(candidate, timeout_sec)

    elif candidate.proxy_type in ("mtproto", "ss"):

        return await _check_tcp_only(candidate, timeout_sec)

    else:

        return await _check_http(candidate, timeout_sec)  # fallback

  
  

async def validate_many(candidates: list[ProxyCandidate], timeout_sec: float, max_concurrent: int) -> list[ValidationResult]:

    sem = asyncio.Semaphore(max_concurrent)

    async def run_one(c: ProxyCandidate) -> ValidationResult:

        async with sem:

            return await _check(c, timeout_sec)

    return await asyncio.gather(*(run_one(c) for c in candidates))

```

  

**Почему так:** Три стратегии проверки покрывают все 6 протоколов. Для MTProto и Shadowsocks невозможно сделать полноценную проверку протокола без специфических библиотек, но TCP-connectivity — это уже **гораздо лучше**, чем текущий "всегда False".

  

---

  

### 🔧 Исправление #8 — Расширение Normalizer

  

**Проблема**: [[PARSER_ISSUES#8. Normalizer Пропускает Распространённые Форматы]]

  

**Файлы для изменения:**

- `app/normalizer.py` — добавить паттерны

  

#### Шаг 1: Расширить RegEx для поддержки аутентификации

  

```python

# app/normalizer.py — ДОБАВИТЬ новые паттерны:

  

# Основной RegEx — теперь ловит user:pass@host:port

PROXY_RE = re.compile(

    r"(?P<scheme>socks5|socks4|http|https|mtproto|ss)://"

    r"(?:[\w.~%-]+:[\w.~%-]+@)?"  # опциональный user:pass@

    r"(?P<host>[a-zA-Z0-9_.-]+):(?P<port>\d{2,5})",

    re.IGNORECASE,

)

  

# Shadowsocks URI формат: ss://base64...

SS_URI_RE = re.compile(r"ss://([A-Za-z0-9+/=]+)(?:#\S+)?")

  

# JSON-формат: {"server": "1.2.3.4", "server_port": 1080}

JSON_SERVER_RE = re.compile(

    r'"server"\s*:\s*"(?P<host>[^"]+)"\s*,\s*'

    r'"server_port"\s*:\s*(?P<port>\d{2,5})',

)

# VMess URI формат: vmess://base64...

VMESS_URI_RE = re.compile(r"vmess://([A-Za-z0-9+/=]+)")

# Формат через пробел/табуляцию: host port или host\tport

SPACE_RE = re.compile(

    r"^(?P<host>\d{1,3}(?:\.\d{1,3}){3})[\s\t]+(?P<port>\d{2,5})$",

    re.MULTILINE,

)

```

  

#### Шаг 2: Добавить парсинг дополнительных форматов в `parse_candidates()`

  

```python

import base64

import json

  

def parse_candidates(text: str, source: str, default_scheme: str = "http") -> list[ProxyCandidate]:

    out: list[ProxyCandidate] = []

    seen = set()

  

    # 1) Стандартный формат protocol://host:port

    for m in PROXY_RE.finditer(text):

        proxy_type = m.group("scheme").lower()

        host = m.group("host")

        port = int(m.group("port"))

        key = (proxy_type, host, port)

        if key not in seen:

            seen.add(key)

            out.append(ProxyCandidate(proxy_type=proxy_type, host=host, port=port, source=source))

  

    # 2) Shadowsocks URI: ss://base64...

    for m in SS_URI_RE.finditer(text):

        try:

            decoded = base64.b64decode(m.group(1)).decode("utf-8", errors="ignore")

            # Формат: method:password@host:port

            if "@" in decoded:

                addr_part = decoded.split("@", 1)[1]

                host, port_str = addr_part.rsplit(":", 1)

                port = int(port_str)

                key = ("ss", host, port)

                if key not in seen:

                    seen.add(key)

                    out.append(ProxyCandidate(proxy_type="ss", host=host, port=port, source=source))

        except Exception:

            pass

  

    # 3) JSON-формат {"server": ..., "server_port": ...}

    for m in JSON_SERVER_RE.finditer(text):

        host = m.group("host")

        port = int(m.group("port"))

        key = ("ss", host, port)

        if key not in seen:

            seen.add(key)

            out.append(ProxyCandidate(proxy_type="ss", host=host, port=port, source=source))

  

    # 4) VMess URI: vmess://base64...

    for m in VMESS_URI_RE.finditer(text):

        try:

            decoded = base64.b64decode(m.group(1)).decode("utf-8", errors="ignore")

            data = json.loads(decoded)

            host = data.get("add", "")

            port = int(data.get("port", 0))

            if host and port:

                key = ("vmess", host, port)

                if key not in seen:

                    seen.add(key)

                    out.append(ProxyCandidate(proxy_type="vmess", host=host, port=port, source=source))

        except Exception:

            pass

  

    # 5) Формат через пробел/табуляцию: 1.2.3.4 1080 или 1.2.3.4\t1080

    for m in SPACE_RE.finditer(text):

        host = m.group("host")

        port = int(m.group("port"))

        key = (default_scheme.lower(), host, port)

        if key not in seen:

            seen.add(key)

            out.append(ProxyCandidate(proxy_type=default_scheme.lower(), host=host, port=port, source=source))

  

    # 6) Fallback: голый ip:port

    for m in FALLBACK_RE.finditer(text):

        proxy_type = default_scheme.lower()

        host = m.group("host")

        port = int(m.group("port"))

        key = (proxy_type, host, port)

        if key not in seen:

            seen.add(key)

            out.append(ProxyCandidate(proxy_type=proxy_type, host=host, port=port, source=source))

  

    return out

```

  

**Почему так:** Теперь парсер ловит **6 форматов** вместо 2:

1. `protocol://host:port` (стандартный, с опциональным `user:pass@`)

2. `ss://base64...` (Shadowsocks URI)

3. `{"server": ..., "server_port": ...}` (JSON-конфиг)

4. `vmess://base64...` (V2Ray VMess URI)

5. `host port` / `host\tport` (пробельно-табличный формат)

6. `ip:port` (fallback)

Каждый новый формат добавляется изолированно, не ломая предыдущие, и дедупликация через `seen` предотвращает дубли.

  

---

  

## Фаза 2: Устойчивость к Сбоям

  

> Цель: парсер продолжает работать, даже если внешние сервисы сбоят или лимитируют.

  

---

  

### 🔧 Исправление #1 — Обработка GitHub Rate Limit

  

**Проблема**: [[PARSER_ISSUES#1. GitHub API Rate Limit — Нет Обработки]]

  

**Файлы для изменения:**

- `app/collectors/github.py` — добавить rate-limit handling

  

#### Шаг 1: Создать вспомогательную функцию для обработки лимитов

  

```python

# app/collectors/github.py — ДОБАВИТЬ в начало файла:

import asyncio

import logging

  

logger = logging.getLogger(__name__)

  

async def _rate_limited_get(

    client: httpx.AsyncClient,

    url: str,

    **kwargs,

) -> httpx.Response | None:

    """GET-запрос с обработкой GitHub Rate Limit.

  

    При получении 403 с X-RateLimit-Remaining == 0:

    - Логирует предупреждение

    - Ждёт до X-RateLimit-Reset

    - Повторяет запрос

    При 5xx ошибках — retry до 3 раз с exponential backoff.

    """

    max_retries = 3

    for attempt in range(max_retries):

        resp = await client.get(url, **kwargs)

  

        if resp.status_code == 200:

            return resp

  

        if resp.status_code == 403:

            remaining = int(resp.headers.get("X-RateLimit-Remaining", "1"))

            if remaining == 0:

                reset_ts = int(resp.headers.get("X-RateLimit-Reset", "0"))

                import time as _time

                wait = max(1, reset_ts - int(_time.time())) + 1

                logger.warning(f"GitHub rate limit hit. Sleeping {wait}s until reset.")

                await asyncio.sleep(min(wait, 300))  # Не более 5 минут

                continue

            else:

                logger.warning(f"GitHub 403 (not rate limit): {url}")

                return None

  

        if resp.status_code >= 500:

            wait = 2 ** attempt

            logger.warning(f"GitHub 5xx ({resp.status_code}), retry in {wait}s")

            await asyncio.sleep(wait)

            continue

  

        # 404, 422 и т.д. — пропускаем

        return None

  

    return None

```

  

#### Шаг 2: Заменить все `client.get(...)` на `_rate_limited_get(client, ...)`

  

Во всех местах, где сейчас:

```python

search = await client.get("https://api.github.com/search/code", params={...})

if search.status_code != 200:

    break

```

Заменить на:

```python

search = await _rate_limited_get(client, "https://api.github.com/search/code", params={...})

if search is None:

    break

```

  

Аналогично для: `search/repositories`, `repos/{repo}`, `repos/{repo}/readme`, `repos/{repo}/git/trees/...`, `repos/{repo}/git/blobs/...`.

  

> [!TIP]

> Функция `_rate_limited_get` уже включает retry с exponential backoff для 5xx ошибок (до 3 попыток), что решает также часть проблемы #7 (Retry-логика) для GitHub коллектора. Для сетевых таймаутов и `ConnectionError` нужно добавить обработку в тот же метод:

  

```python

# app/collectors/github.py — ДОПОЛНИТЬ _rate_limited_get():
# Добавить перехват сетевых ошибок в основной цикл retry:

async def _rate_limited_get(
    client: httpx.AsyncClient,
    url: str,
    **kwargs,
) -> httpx.Response | None:
    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = await client.get(url, **kwargs)
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as e:
            # Сетевая ошибка — retry с backoff
            wait = 2 ** attempt
            logger.warning(f"Network error on {url}: {e}. Retry in {wait}s (attempt {attempt+1}/{max_retries})")
            await asyncio.sleep(wait)
            continue

        if resp.status_code == 200:
            return resp

        if resp.status_code == 403:
            remaining = int(resp.headers.get("X-RateLimit-Remaining", "1"))
            if remaining == 0:
                reset_ts = int(resp.headers.get("X-RateLimit-Reset", "0"))
                import time as _time
                wait = max(1, reset_ts - int(_time.time())) + 1
                logger.warning(f"GitHub rate limit hit. Sleeping {wait}s until reset.")
                await asyncio.sleep(min(wait, 300))
                continue
            else:
                logger.warning(f"GitHub 403 (not rate limit): {url}")
                return None

        if resp.status_code >= 500:
            wait = 2 ** attempt
            logger.warning(f"GitHub 5xx ({resp.status_code}), retry in {wait}s")
            await asyncio.sleep(wait)
            continue

        return None

    return None
```

  

---

  

### 🔧 Исправление #14 — Fallback Endpoints для Валидации

  

**Проблема**: [[PARSER_ISSUES#14. `httpbin.org` Как Единственный Endpoint для Проверки]]

  

**Уже решено** в рамках исправления #2 — список `CHECK_URLS` содержит 3 endpoint-а, и `_check_http` итерирует по ним. Если `httpbin.org` недоступен, проверка идёт через `ifconfig.me` и `api.ipify.org`.

  

---

  

### 🔧 Исправление #7 — Retry-Логика для Всех Сетевых Запросов

  

**Проблема**: [[PARSER_ISSUES#7. Нет Retry-логики Нигде]]

  

**Файлы для изменения:**

- `app/collectors/url_list.py` — добавить retry

- `app/geo.py` — добавить retry

  

#### Универсальный retry-декоратор

  

Создать новый файл `app/retry.py`:

  

```python

# app/retry.py — НОВЫЙ ФАЙЛ

import asyncio

import logging

from functools import wraps

  

logger = logging.getLogger(__name__)

  
  

async def retry_async(

    coro_func,

    *args,

    max_attempts: int = 3,

    base_delay: float = 1.0,

    **kwargs,

):

    """Выполняет async-функцию с retry и exponential backoff."""

    for attempt in range(max_attempts):

        try:

            return await coro_func(*args, **kwargs)

        except Exception as e:

            if attempt == max_attempts - 1:

                logger.warning(f"All {max_attempts} attempts failed for {coro_func.__name__}: {e}")

                raise

            delay = base_delay * (2 ** attempt)

            logger.debug(f"Attempt {attempt+1} failed, retrying in {delay}s: {e}")

            await asyncio.sleep(delay)

```

  

#### Применить в `url_list.py`:

  

```python

# app/collectors/url_list.py — СТАЛО:

import logging

import httpx

from app.collectors.base import Collector

from app.retry import retry_async

  

logger = logging.getLogger(__name__)

  

class URLListCollector(Collector):

    def __init__(self, urls: list[str]) -> None:

        self.urls = urls

  

    async def collect(self) -> list[tuple[str, str]]:

        out: list[tuple[str, str]] = []

        if not self.urls:

            return out

        async with httpx.AsyncClient(timeout=10) as client:

            for url in self.urls:

                try:

                    r = await retry_async(client.get, url, max_attempts=3)

                    if r.status_code == 200 and r.text:

                        out.append((url, r.text))

                except Exception:

                    logger.warning(f"Failed to fetch {url} after retries")

        return out

```

  

#### Применить в `geo.py`:

  

```python

# app/geo.py — СТАЛО:

import logging

import httpx

  

logger = logging.getLogger(__name__)

  

# Кэш IP -> страна (живёт в памяти процесса)

_geo_cache: dict[str, str | None] = {}

  

async def country_by_ip(ip: str) -> str | None:

    if ip in _geo_cache:

        return _geo_cache[ip]

  

    url = f"https://ipapi.co/{ip}/country/"

    for attempt in range(2):

        try:

            async with httpx.AsyncClient(timeout=3) as client:

                resp = await client.get(url)

                if resp.status_code == 429:  # Rate limited

                    logger.warning("ipapi.co rate limit hit")

                    _geo_cache[ip] = None

                    return None

                if resp.status_code != 200:

                    continue

                cc = resp.text.strip().upper()

                result = cc if len(cc) == 2 else None

                _geo_cache[ip] = result

                return result

        except Exception:

            continue

  

    _geo_cache[ip] = None

    return None

```

  

**Почему так:** Добавлен in-memory кэш (`_geo_cache`) — один IP никогда не запрашивается дважды. Это решает и проблему #4 (лимит API), и проблему скорости.

  

---

  

## Фаза 3: Производительность

  

> Цель: ускорить каждый этап пайплайна.

  

---

  

### 🔧 Исправление #3 — Параллельный Сбор Данных

  

**Проблема**: [[PARSER_ISSUES#3. Все Запросы Последовательные — Критическая Потеря Скорости]]

  

**Файлы для изменения:**

- `app/collectors/github.py` — параллельные запросы с Semaphore

- `app/collectors/url_list.py` — параллельные запросы

- `app/pipeline.py` — параллельный запуск коллекторов

- `app/pipeline.py` — параллельное определение гео

  

#### В GitHubCollector — параллельный сбор через Semaphore:

  

> [!IMPORTANT]

> GitHub API имеет жёсткие лимиты, поэтому нельзя бездумно распараллеливать все запросы. Используем `asyncio.Semaphore` для ограничения одновременных запросов (3-5 параллельных), сохраняя rate limit обработку.

  

```python

# app/collectors/github.py — ДОБАВИТЬ параллельный сбор ключевых слов:

import asyncio

  

# Лимит параллельных запросов к GitHub API

_GITHUB_SEMAPHORE = asyncio.Semaphore(3)

  

async def _search_keyword(

    client: httpx.AsyncClient,

    keyword: str,

    search_type: str,  # "code" или "repositories"

    pages: int,

) -> list[dict]:

    """Поиск по одному ключевому слову с ограничением через Semaphore."""

    results = []

    async with _GITHUB_SEMAPHORE:

        for page in range(1, pages + 1):

            endpoint = f"https://api.github.com/search/{search_type}"

            params = {"q": keyword, "per_page": 30, "page": page}

            resp = await _rate_limited_get(client, endpoint, params=params)

            if resp is None:

                break

            items = resp.json().get("items", [])

            if not items:

                break

            results.extend(items)

    return results

  

async def collect(self) -> list[tuple[str, str]]:

    """Параллельный сбор по всем ключевым словам."""

    async with httpx.AsyncClient(

        headers=self._headers(), timeout=15

    ) as client:

        # Запускаем поиск по всем keywords параллельно

        code_tasks = [

            _search_keyword(client, kw, "code", pages=5)

            for kw in self.keywords

        ]

        repo_tasks = [

            _search_keyword(client, kw, "repositories", pages=5)

            for kw in self.keywords

        ]

        all_results = await asyncio.gather(

            *code_tasks, *repo_tasks,

            return_exceptions=True,

        )

  

        # Собираем результаты, пропуская ошибки

        code_items = []

        repo_items = []

        for i, result in enumerate(all_results):

            if isinstance(result, Exception):

                logger.warning(f"Search task {i} failed: {result}")

                continue

            if i < len(code_tasks):

                code_items.extend(result)

            else:

                repo_items.extend(result)

  

        # Далее обработка code_items и repo_items

        # (загрузка файлов тоже через Semaphore — см. далее)

        out = []

        file_tasks = []

        for item in code_items:

            url = item.get("download_url") or item.get("html_url")

            if url:

                file_tasks.append(_fetch_file(client, url))

        file_results = await asyncio.gather(*file_tasks, return_exceptions=True)

        for r in file_results:

            if isinstance(r, tuple):

                out.append(r)

        return out

  

async def _fetch_file(

    client: httpx.AsyncClient, url: str

) -> tuple[str, str] | None:

    """Загрузка одного файла с ограничением через Semaphore."""

    async with _GITHUB_SEMAPHORE:

        resp = await _rate_limited_get(client, url)

        if resp and resp.text:

            return (url, resp.text)

    return None

```

  

**Эффект:** Вместо последовательного сбора 5 keywords × 5 pages × 2 types = 50 запросов (10+ секунд), теперь запросы идут по 3 параллельно, сокращая время до ~3-4 секунд, сохраняя rate-limit безопасность.

  

#### В URLListCollector — параллельная загрузка:

  

```python

# app/collectors/url_list.py — метод collect():

async def collect(self) -> list[tuple[str, str]]:

    if not self.urls:

        return []

  

    async def _fetch_one(client: httpx.AsyncClient, url: str) -> tuple[str, str] | None:

        try:

            r = await client.get(url)

            if r.status_code == 200 and r.text:

                return (url, r.text)

        except Exception:

            logger.warning(f"Failed to fetch {url}")

        return None

  

    out: list[tuple[str, str]] = []

    async with httpx.AsyncClient(timeout=10) as client:

        results = await asyncio.gather(*(_fetch_one(client, u) for u in self.urls))

        out = [r for r in results if r is not None]

    return out

```

  

#### В Pipeline — параллельно запускать коллекторы и гео-запросы:

  

```python

# app/pipeline.py — run_once():

# БЫЛО:

#     for collector in collectors:

#         raws.extend(await collector.collect())

# СТАЛО:

    results = await asyncio.gather(*(c.collect() for c in collectors))

    raws: list[tuple[str, str]] = []

    for result in results:

        raws.extend(result)

  

# БЫЛО (гео-определение):

#     for item in validated:

#         country = await country_by_ip(item.candidate.host)

# СТАЛО:

    # Предварительно определяем страны для всех уникальных IP параллельно

    unique_ips = {item.candidate.host for item in validated if _is_ip(item.candidate.host)}

    geo_tasks = {ip: country_by_ip(ip) for ip in unique_ips}

    geo_results = await asyncio.gather(*geo_tasks.values())

    ip_to_country = dict(zip(geo_tasks.keys(), geo_results))

  

    for item in validated:

        country = ip_to_country.get(item.candidate.host) if _is_ip(item.candidate.host) else None

        # ... остальная фильтрация ...

```

  

---

  

### 🔧 Исправление #4 — Локальный GeoIP Вместо API

  

**Проблема**: [[PARSER_ISSUES#4. Geo-API Один Запрос на IP — Критическое Бутылочное Горло]]

  

**Идеальное решение** (вместо ipapi.co):

  

```txt

# requirements.txt — добавить:

geoip2==4.8.1

```

  

```python

# app/geo.py — полная замена:

import geoip2.database

import logging

  

logger = logging.getLogger(__name__)

  

_reader = None

  

def _get_reader():

    global _reader

    if _reader is None:

        try:

            _reader = geoip2.database.Reader("GeoLite2-Country.mmdb")

        except Exception:

            logger.error("GeoLite2-Country.mmdb not found. Download from MaxMind.")

    return _reader

  

async def country_by_ip(ip: str) -> str | None:

    reader = _get_reader()

    if reader is None:

        return None

    try:

        response = reader.country(ip)

        return response.country.iso_code

    except Exception:

        return None

```

  

**Почему так:** Локальная база — **нулевая задержка**, **нулевые лимиты**, работает оффлайн. Файл `GeoLite2-Country.mmdb` (∼6MB) скачивается бесплатно с сайта MaxMind.

  

> [!TIP]

> Можно оставить `ipapi.co` как fallback, а локальную базу использовать как основной источник.

  

---

  

### 🔧 Исправление #5 — Batch-Запись в БД

  

**Проблема**: [[PARSER_ISSUES#5. SQLite + Синхронные Операции — Плохо Масштабируется]]

  

**Файлы для изменения:**

- `app/storage.py` — добавить batch-метод

- `app/pipeline.py` — использовать batch вместо цикла

  

```python

# app/storage.py — ДОБАВИТЬ метод:

def batch_upsert_proxies(self, items: list[dict]) -> int:

    """Массовый upsert. items = список словарей с ключами:

    proxy_type, host, port, source, country, is_alive, latency_ms

    """

    saved = 0

    with Session(self.engine) as session:

        for item in items:

            existing = session.scalar(

                select(Proxy).where(

                    Proxy.proxy_type == item["proxy_type"],

                    Proxy.host == item["host"],

                    Proxy.port == item["port"],

                )

            )

            if not existing:

                existing = Proxy(

                    proxy_type=item["proxy_type"],

                    host=item["host"],

                    port=item["port"],

                    source=item["source"],

                    country=item["country"],

                    is_alive=item["is_alive"],

                    latency_ms=item["latency_ms"],

                    success_rate=1.0 if item["is_alive"] else 0.0,

                    score=self._score(item["is_alive"], item["latency_ms"], 1.0 if item["is_alive"] else 0.0),

                    last_checked_at=datetime.utcnow(),

                )

                session.add(existing)

            else:

                old_sr = existing.success_rate

                new_sr = (old_sr * 0.8) + (1.0 if item["is_alive"] else 0.0) * 0.2

                existing.is_alive = item["is_alive"]

                existing.latency_ms = item["latency_ms"]

                existing.country = item["country"] or existing.country

                existing.source = item["source"]

                existing.success_rate = new_sr

                existing.score = self._score(item["is_alive"], item["latency_ms"], new_sr)

                existing.last_checked_at = datetime.utcnow()

  

            session.add(Observation(

                proxy_type=item["proxy_type"],

                host=item["host"],

                port=item["port"],

                is_alive=item["is_alive"],

                latency_ms=item["latency_ms"],

                source=item["source"],

            ))

            saved += 1

  

        session.commit()  # ОДИН коммит на всю пачку

    return saved

```

  

**Эффект:** Вместо 500 транзакций — одна. На SQLite это ускорение в **10-50 раз** на этапе записи.

  

---

  

## Фаза 4: Качество Кода

  

> Цель: сделать систему отлаживаемой и поддерживаемой.

  

---

  

### 🔧 Исправление #6 — Добавить Логирование

  

**Проблема**: [[PARSER_ISSUES#6. Нет Логирования — Вообще Никакого]]

  

**Файлы для изменения:**

- `app/main.py` — настройка корневого logger

- Все прочие файлы — замена `print()` на `logger.*`

  

#### Шаг 1: Настроить в `main.py`

  

```python

# app/main.py — ДОБАВИТЬ в начало main():

import logging

  

logging.basicConfig(

    level=logging.INFO,

    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",

    datefmt="%Y-%m-%d %H:%M:%S",

)

```

  

#### Шаг 2: Добавить логирование в ключевые точки

  

| Файл | Что логировать |

|------|---------------|

| `pipeline.py` | Начало/конец цикла, количество raw/candidates/alive |

| `github.py` | Rate limit warnings, количество найденных файлов/repos |

| `validator.py` | Общая статистика проверки (X из Y живых) |

| `geo.py` | Rate limit и ошибки API |

| `scheduler.py` | Заменить `print()` на `logger.info()` |

  

#### Шаг 3: Примеры кода логирования для каждого ключевого файла

  

```python

# app/pipeline.py — ДОБАВИТЬ в начало файла и в run_once():

import logging

logger = logging.getLogger(__name__)

  

async def run_once(self) -> dict:
    logger.info("=== Pipeline cycle started ===")
    # ... сбор данных ...
    logger.info(f"Collected {len(raws)} raw texts from {len(collectors)} collectors")
    # ... нормализация ...
    logger.info(f"Normalized into {len(candidates)} proxy candidates")
    # ... валидация ...
    alive_count = sum(1 for v in validated if v.is_alive)
    logger.info(f"Validation complete: {alive_count}/{len(candidates)} alive")
    # ... гео ...
    logger.info(f"Geo resolved for {len(unique_ips)} unique IPs")
    # ... запись ...
    logger.info(f"Saved {saved} proxies to database")
    logger.info(f"=== Pipeline cycle finished in {elapsed:.1f}s ===")
    return {"raw": len(raws), "candidates": len(candidates), "alive": alive_count, "saved": saved}

```

  

```python

# app/collectors/github.py — ДОБАВИТЬ логирование результатов:

import logging

logger = logging.getLogger(__name__)

  

# В collect():
    logger.info(f"GitHub search: {len(self.keywords)} keywords, searching...")
    # ... после сбора ...
    logger.info(f"GitHub: found {len(code_items)} code results, {len(repo_items)} repos")
    # ... после загрузки файлов ...
    logger.info(f"GitHub: downloaded {len(out)} files with proxy data")

```

  

```python

# app/validator.py — ДОБАВИТЬ статистику:

import logging

logger = logging.getLogger(__name__)

  

# В validate_many(), после завершения:
    alive = sum(1 for r in results if r.is_alive)
    dead = len(results) - alive
    avg_latency = sum(r.latency_ms for r in results if r.latency_ms) / max(alive, 1)
    logger.info(
        f"Validation: {alive} alive, {dead} dead out of {len(results)} "
        f"(avg latency: {avg_latency:.0f}ms)"
    )

```

  

```python

# app/scheduler.py — ЗАМЕНИТЬ print() на logger:

import logging

logger = logging.getLogger(__name__)

  

# БЫЛО:
#     print(f"Starting scheduled run...")
# СТАЛО:
    logger.info(f"Scheduler: starting pipeline run (interval: {interval_min}m)")

```

  

> [!TIP]

> Для production-среды рекомендуется добавить **ротацию логов** через `logging.handlers.RotatingFileHandler` и опциональный **JSON-формат** для интеграции с системами мониторинга:

  

```python

# app/main.py — ОПЦИОНАЛЬНАЯ расширенная настройка (вместо basicConfig):

import logging

from logging.handlers import RotatingFileHandler

  

def setup_logging(log_file: str = "parser.log", level: str = "INFO"):
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper()))
  
    # Формат для консоли
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(console)
  
    # Формат для файла (с ротацией, макс 5MB × 3 файла)
    file_handler = RotatingFileHandler(
        log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(file_handler)

```

  

---

  

### 🔧 Исправление #10 — Замена `datetime.utcnow()`

  

**Проблема**: [[PARSER_ISSUES#10. `datetime.utcnow()` — Deprecated и Неправильный]]

  

**Файлы для изменения:**

- `app/storage.py`

- `app/models.py`

  

Глобальная замена:

```python

# БЫЛО:

datetime.utcnow()

# СТАЛО:

from datetime import datetime, timezone

datetime.now(timezone.utc)

```

  

В `models.py` — заменить `default=datetime.utcnow` на `default=lambda: datetime.now(timezone.utc)`.

  

---

  

### 🔧 Исправление #12 — Расширение Тестов

  

**Проблема**: [[PARSER_ISSUES#12. Тесты — Покрытие Минимальное]]

  

**Минимальный план тестирования:**

  

| Тест | Файл | Что проверяет |

|------|------|---------------|

| `test_normalizer_extended.py` | `normalizer.py` | SS URI, VMess URI, JSON format, user:pass, space/tab, дубли, пустой текст |

| `test_validator_routing.py` | `validator.py` | Правильный выбор стратегии по типу протокола |

| `test_score.py` | `storage.py` | Формула скоринга для edge cases |

| `test_pipeline_stats.py` | `pipeline.py` | Корректность counts (mock collectors) |

| `test_geo_cache.py` | `geo.py` | Кэш работает, повторные вызовы не делают HTTP |

  

#### Примеры тестов

  

```python

# tests/test_normalizer_extended.py — НОВЫЙ ФАЙЛ

import pytest

from app.normalizer import parse_candidates

  

class TestStandardFormat:

    def test_http_proxy(self):

        text = "http://1.2.3.4:8080"

        result = parse_candidates(text, "test")

        assert len(result) == 1

        assert result[0].proxy_type == "http"

        assert result[0].host == "1.2.3.4"

        assert result[0].port == 8080

  

    def test_socks5_with_auth(self):

        text = "socks5://user:password@1.2.3.4:1080"

        result = parse_candidates(text, "test")

        assert len(result) == 1

        assert result[0].proxy_type == "socks5"

        assert result[0].host == "1.2.3.4"

  

    def test_deduplication(self):

        text = "http://1.2.3.4:8080\nhttp://1.2.3.4:8080"

        result = parse_candidates(text, "test")

        assert len(result) == 1

  

class TestShadowsocksURI:

    def test_ss_base64(self):

        import base64

        payload = base64.b64encode(b"aes-256-cfb:password@5.6.7.8:443").decode()

        text = f"ss://{payload}"

        result = parse_candidates(text, "test")

        assert any(r.proxy_type == "ss" and r.host == "5.6.7.8" and r.port == 443 for r in result)

  

class TestVMessURI:

    def test_vmess_base64(self):

        import base64, json

        config = json.dumps({"add": "9.8.7.6", "port": 443, "id": "xxx"})

        payload = base64.b64encode(config.encode()).decode()

        text = f"vmess://{payload}"

        result = parse_candidates(text, "test")

        assert any(r.proxy_type == "vmess" and r.host == "9.8.7.6" and r.port == 443 for r in result)

  

class TestSpaceTabFormat:

    def test_space_separated(self):

        text = "1.2.3.4 8080"

        result = parse_candidates(text, "test")

        assert len(result) >= 1

        assert any(r.host == "1.2.3.4" and r.port == 8080 for r in result)

  

    def test_tab_separated(self):

        text = "1.2.3.4\t8080"

        result = parse_candidates(text, "test")

        assert len(result) >= 1

        assert any(r.host == "1.2.3.4" and r.port == 8080 for r in result)

  

class TestJSONFormat:

    def test_json_server_port(self):

        text = '{"server": "10.0.0.1", "server_port": 1080}'

        result = parse_candidates(text, "test")

        assert any(r.host == "10.0.0.1" and r.port == 1080 for r in result)

  

class TestEdgeCases:

    def test_empty_text(self):

        result = parse_candidates("", "test")

        assert result == []

  

    def test_garbage_text(self):

        result = parse_candidates("hello world no proxies here!", "test")

        assert result == []

```

  

```python

# tests/test_validator_routing.py — НОВЫЙ ФАЙЛ

import pytest

import asyncio

from unittest.mock import AsyncMock, patch

from app.validator import _check

from app.normalizer import ProxyCandidate

  

class TestValidatorRouting:

    """Проверяет, что валидатор выбирает правильную стратегию по типу протокола."""

  

    @pytest.mark.asyncio

    @patch("app.validator._check_http")

    async def test_http_uses_http_strategy(self, mock_http):

        c = ProxyCandidate(proxy_type="http", host="1.2.3.4", port=8080, source="test")

        await _check(c, timeout_sec=5.0)

        mock_http.assert_called_once_with(c, 5.0)

  

    @pytest.mark.asyncio

    @patch("app.validator._check_socks")

    async def test_socks5_uses_socks_strategy(self, mock_socks):

        c = ProxyCandidate(proxy_type="socks5", host="1.2.3.4", port=1080, source="test")

        await _check(c, timeout_sec=5.0)

        mock_socks.assert_called_once_with(c, 5.0)

  

    @pytest.mark.asyncio

    @patch("app.validator._check_tcp_only")

    async def test_mtproto_uses_tcp_strategy(self, mock_tcp):

        c = ProxyCandidate(proxy_type="mtproto", host="1.2.3.4", port=443, source="test")

        await _check(c, timeout_sec=5.0)

        mock_tcp.assert_called_once_with(c, 5.0)

  

    @pytest.mark.asyncio

    @patch("app.validator._check_tcp_only")

    async def test_ss_uses_tcp_strategy(self, mock_tcp):

        c = ProxyCandidate(proxy_type="ss", host="1.2.3.4", port=443, source="test")

        await _check(c, timeout_sec=5.0)

        mock_tcp.assert_called_once_with(c, 5.0)

```

  

```python

# tests/test_geo_cache.py — НОВЫЙ ФАЙЛ

import pytest

from unittest.mock import patch, AsyncMock

  

class TestGeoCache:

    """Проверяет, что кэш работает и повторные вызовы не делают HTTP."""

  

    @pytest.mark.asyncio

    async def test_cache_prevents_duplicate_requests(self):

        # Тестируем API-версию geo.py (до перехода на MaxMind)

        from app import geo

        geo._geo_cache.clear()

  

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:

            mock_get.return_value.status_code = 200

            mock_get.return_value.text = "US"

  

            result1 = await geo.country_by_ip("1.2.3.4")

            result2 = await geo.country_by_ip("1.2.3.4")  # должен взяться из кэша

  

            assert result1 == "US"

            assert result2 == "US"

            assert mock_get.call_count == 1  # Только один HTTP-запрос!

  

        geo._geo_cache.clear()

```

  

---

  

## Фаза 5: Архитектурные Улучшения

  

> Цель: долгосрочная устойчивость системы.

  

---

  

### 🔧 Исправление #9 — TTL для Репозиториев

  

**Проблема**: [[PARSER_ISSUES#9. Повторное Сканирование Репозиториев Заблокировано Навсегда]]

  

**Файлы для изменения:**

- `app/config.py` — новый параметр `REPO_RESCAN_HOURS`

- `app/storage.py` — изменить `enqueue_repo()`

  

```python

# app/config.py — ДОБАВИТЬ:

repo_rescan_hours: int = int(os.getenv("REPO_RESCAN_HOURS", "48"))

```

  

```python

# app/storage.py — ИЗМЕНИТЬ enqueue_repo():

def enqueue_repo(self, repo_full_name: str, note: str | None = None, force: bool = False) -> tuple[bool, str]:

    repo = repo_full_name.strip().lower()

    with Session(self.engine) as session:

        existing = session.scalar(select(RepoTask).where(RepoTask.repo_full_name == repo))

        if existing:

            if existing.status == "done":

                # Проверяем TTL

                from app.config import settings

                if existing.last_analyzed_at and not force:

                    age_hours = (datetime.now(timezone.utc) - existing.last_analyzed_at).total_seconds() / 3600

                    if age_hours < settings.repo_rescan_hours:

                        return False, "already_analyzed"

                # TTL истёк — переставляем в очередь

                existing.status = "pending"

                existing.note = note or "rescan_ttl"

                session.commit()

                return True, "requeued"

            return False, "already_queued"

        session.add(RepoTask(repo_full_name=repo, status="pending", note=note))

        session.commit()

        return True, "queued"

```

  

---

  

### 🔧 Исправление #13 — Очистка и Ротация Мёртвых Прокси

  

**Проблема**: [[PARSER_ISSUES#13. Нет Очистки Мёртвых Прокси]]

  

**Файлы для изменения:**

- `app/config.py` — параметры TTL

- `app/storage.py` — методы очистки

- `app/pipeline.py` — вызов очистки после каждого цикла

  

```python

# app/config.py — ДОБАВИТЬ:

proxy_dead_ttl_days: int = int(os.getenv("PROXY_DEAD_TTL_DAYS", "7"))

observation_ttl_days: int = int(os.getenv("OBSERVATION_TTL_DAYS", "30"))

```

  

```python

# app/storage.py — ДОБАВИТЬ методы:

def cleanup_dead_proxies(self, dead_ttl_days: int) -> int:

    """Удалить прокси, мёртвые более N дней."""

    cutoff = datetime.now(timezone.utc) - timedelta(days=dead_ttl_days)

    with Session(self.engine) as session:

        count = session.query(Proxy).filter(

            Proxy.is_alive.is_(False),

            Proxy.last_checked_at < cutoff,

        ).delete()

        session.commit()

        return count

  

def cleanup_old_observations(self, ttl_days: int) -> int:

    """Удалить observation записи старше N дней."""

    cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)

    with Session(self.engine) as session:

        count = session.query(Observation).filter(

            Observation.checked_at < cutoff,

        ).delete()

        session.commit()

        return count

```

  

```python

# app/pipeline.py — ДОБАВИТЬ в конец run_once():

    # Очистка

    dead_removed = self.storage.cleanup_dead_proxies(self.settings.proxy_dead_ttl_days)

    obs_removed = self.storage.cleanup_old_observations(self.settings.observation_ttl_days)

    logger.info(f"Cleanup: {dead_removed} dead proxies, {obs_removed} old observations removed")

```

  

---

  

### 🔧 Исправление #11 — Связь Бота и Парсера

  

**Проблема**: [[PARSER_ISSUES#11. Бот и Парсер — Два Отдельных Процесса Без Связи]]

  

**Рекомендуемый подход:** Объединить бота и парсер в один процесс. `python-telegram-bot` использует `asyncio`, и `APScheduler` можно встроить внутрь event loop бота.

  

```python

# app/main.py — ДОБАВИТЬ новую команду:

sub.add_parser("all-in-one", help="Run bot + scheduler in one process")

  

# В main():

elif args.cmd == "all-in-one":

    run_all_in_one(settings)

```

  

```python

# app/bot.py — ДОБАВИТЬ run_all_in_one():

async def run_all_in_one_job(context: ContextTypes.DEFAULT_TYPE) -> None:

    """Job, вызываемый из event loop бота."""

    from app.pipeline import Pipeline

    from app.config import settings

    pipeline = Pipeline(settings)

    stats = await pipeline.run_once()

    logger.info(f"Pipeline run: {stats}")

  

def run_all_in_one(settings: Settings) -> None:

    bot = AdminBot(settings)

    app = Application.builder().token(settings.telegram_bot_token).build()

    # ... все handlers как в run_bot() ...

    # Добавляем парсер как repeating job

    app.job_queue.run_repeating(

        run_all_in_one_job,

        interval=settings.schedule_minutes * 60,

        first=15,

    )

    app.run_polling(close_loop=False)

```

  

**Эффект:** Одна команда запуска, бот может принудительно тригерить цикл, видеть real-time статус.

  

---

  

## 📊 Итоговая Оценка Трудозатрат

  

| Фаза | Описание | Оценка (часы) | Эффект |

|------|----------|:------------:|--------|

| **Фаза 1** | Валидация + Normalizer | 3-4 | 🔥 +300% полезных данных |

| **Фаза 2** | Rate limit + retry + fallback | 2-3 | 🛡 Устойчивость к сбоям |

| **Фаза 3** | Параллельность + GeoIP + batch | 3-4 | ⚡ Ускорение в 5-10 раз |

| **Фаза 4** | Логи + datetime + тесты | 2-3 | 🔍 Отлаживаемость |

| **Фаза 5** | TTL + очистка + all-in-one | 2-3 | 🏗 Долгосрочная стабильность |

| **Итого** | | **12-17** | Полноценный парсер |

  

---

  

> **Связанные документы:**

> - [[Схема]] — архитектура и алгоритмы

> - [[🚨 Критические Проблемы, Дыры и Ограничения Парсера]] — полный список проблем