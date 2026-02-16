from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def retry_async(
    coro_func: Callable[..., Awaitable[T]],
    *args: Any,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    **kwargs: Any,
) -> T:
    """
    Универсальная обёртка для retry любой async функции с exponential backoff.

    Параметры:
        coro_func: Асинхронная функция (не корутина, а именно функция, которая возвращает корутину).
        *args: Позиционные аргументы для coro_func.
        max_attempts: Максимальное количество попыток (по умолчанию 3).
        base_delay: Базовая задержка для backoff в секундах (по умолчанию 1.0).
        **kwargs: Именованные аргументы для coro_func.

    Возвращает:
        Результат успешного вызова coro_func(*args, **kwargs).

    Исключения:
        Пробрасывает последнее исключение, если все попытки исчерпаны.
    """
    last_exc: BaseException | None = None

    for attempt in range(max_attempts):
        try:
            return await coro_func(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt < max_attempts - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "Попытка %d/%d для %s не удалась: %s. "
                    "Повтор через %.1f сек.",
                    attempt + 1, max_attempts,
                    getattr(coro_func, "__name__", str(coro_func)),
                    exc, delay,
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    "Все %d попыток для %s исчерпаны. Последняя ошибка: %s",
                    max_attempts,
                    getattr(coro_func, "__name__", str(coro_func)),
                    exc,
                )

    # Все попытки исчерпаны — пробрасываем последнее исключение
    raise last_exc  # type: ignore[misc]
