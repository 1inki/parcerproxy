import pytest
from unittest.mock import MagicMock, patch
from app.geo import country_by_ip

@pytest.mark.asyncio
@patch("app.geo.geoip2.database.Reader")
@patch("app.geo.os.path.exists")
async def test_geo_reader_lazy_init(mock_exists, mock_reader_class):
    """
    Проверка того, что GeoLite2 Reader инициализируется только один раз
    (работает как singleton) и не пересоздаётся при повторных вызовах.
    """
    mock_exists.return_value = True
    
    # Подготовим мок для БД
    mock_db_instance = MagicMock()
    mock_country = MagicMock()
    mock_country.country.iso_code = "US"
    mock_db_instance.country.return_value = mock_country
    mock_reader_class.return_value = mock_db_instance
    
    # Сбрасываем глобальный кэш перед тестом
    import app.geo
    app.geo._reader = None
    
    # Первый вызов
    res1 = await country_by_ip("1.2.3.4")
    assert res1 == "US"
    # Constructor был вызван
    assert mock_reader_class.call_count == 1
    
    # Второй вызов
    res2 = await country_by_ip("5.6.7.8")
    assert res2 == "US"
    # Constructor БОЛЬШЕ НЕ ВЫЗЫВАЛСЯ
    assert mock_reader_class.call_count == 1
    
    # А сам метод country у reader'a был вызван 2 раза
    assert mock_db_instance.country.call_count == 2
