from __future__ import annotations

import asyncio
import logging
from typing import Optional
import os

import geoip2.database
from geoip2.errors import AddressNotFoundError

logger = logging.getLogger(__name__)

# Путь к локальной базе данных
DB_PATH = "GeoLite2-Country.mmdb"

# Глобальный reader для ленивой инициализации
_reader: Optional[geoip2.database.Reader] = None

def _get_reader() -> Optional[geoip2.database.Reader]:
    """Ленивая инициализация reader'а для GeoLite2 базы."""
    global _reader
    if _reader is None:
        if not os.path.exists(DB_PATH):
            logger.error(
                "Файл базы данных %s не найден! "
                "Скачайте GeoLite2-Country.mmdb с https://dev.maxmind.com/geoip/geolite2-free-geolocation-data "
                "и положите в корень проекта.",
                DB_PATH
            )
            return None
        try:
            _reader = geoip2.database.Reader(DB_PATH)
        except Exception as e:
            logger.error("Ошибка при открытии %s: %s", DB_PATH, e)
            return None
    return _reader

async def country_by_ip(ip: str) -> str | None:
    """
    Определяет страну по IP-адресу через локальную базу GeoLite2.
    Функция остается асинхронной для совместимости, но работает синхронно.
    """
    reader = _get_reader()
    if reader is None:
        return None

    try:
        response = reader.country(ip)
        return response.country.iso_code
    except AddressNotFoundError:
        # IP адрес не найден в базе данных
        return None
    except ValueError:
        # Невалидный IP адрес
        return None
    except Exception as e:
        logger.warning("Ошибка определения страны для IP %s: %s", ip, e)
        return None
