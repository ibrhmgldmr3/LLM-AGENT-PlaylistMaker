"""Uygulamanin kendi kayitlari uretimde gorunmeli.

uvicorn yalnizca `uvicorn*` logger'larini yapilandiriyor, KOK logger'a
dokunmuyor. `api` ve `src.*` altindaki kayitlar oraya propagate olup handler
bulamayinca Python'in `lastResort` handler'ina dusuyordu: SADECE WARNING ve
ustu, zaman damgasiz ve maskelemesiz. Yani acilisin dogru gecip gecmedigini
soyleyen INFO satirlarinin tamami uretimde kayboluyordu.
"""

from __future__ import annotations

import io
import logging

import pytest

from src.utils.logging_utils import configure_root_logging

SIZAN_URL = "https://www.googleapis.com/youtube/v3/search?key=AIzaSyTOPSECRETVALUE123&q=x"


@pytest.fixture
def kok_logger():
    """Kok logger'i testten sonra eski haline dondurur.

    Handler'lari BURADA temizlemek yetmiyor: pytest'in gunluk eklentisi kendi
    yakalama handler'ini fixture kurulumundan SONRA, cagri asamasi baslarken
    takiyor. Fonksiyon da tam olarak "handler varsa dokunma" diye tasarlandigi
    icin temizlik test govdesinin ilk satirinda yapilmali.
    """
    root = logging.getLogger()
    onceki_handlers = list(root.handlers)
    onceki_seviye = root.level
    try:
        yield root
    finally:
        root.handlers = onceki_handlers
        root.setLevel(onceki_seviye)


def test_info_records_reach_a_handler(kok_logger):
    kok_logger.handlers.clear()  # bkz. fixture: pytest cagri asamasinda kendi handler'ini takiyor
    assert configure_root_logging() is True

    tampon = io.StringIO()
    kok_logger.handlers[0].stream = tampon
    logging.getLogger("api").info("acilis tamam")

    # Onemli olan seviyenin kendisi: `lastResort` bu satiri HIC yazmiyordu.
    assert "acilis tamam" in tampon.getvalue()
    assert kok_logger.level == logging.INFO


def test_secrets_are_redacted_by_the_root_handler(kok_logger):
    """Maskeleme TEK yerde olmali; her logger'in hatirlamasi gerekmemeli."""
    kok_logger.handlers.clear()
    configure_root_logging()
    tampon = io.StringIO()
    kok_logger.handlers[0].stream = tampon

    logging.getLogger("src.demo").warning("istek basarisiz: %s", SIZAN_URL)

    cikti = tampon.getvalue()
    assert "AIzaSyTOPSECRETVALUE123" not in cikti
    assert "key=***" in cikti


def test_an_existing_configuration_is_not_overwritten(kok_logger):
    """Cagiran taraf kendi yapilandirmasini kurmussa ustune yazmak, bu
    fonksiyonun cozdugu sorunun aynisini ona yasatirdi."""
    kok_logger.handlers.clear()
    kendi_handler = logging.StreamHandler(io.StringIO())
    kok_logger.addHandler(kendi_handler)

    assert configure_root_logging() is False
    assert kok_logger.handlers == [kendi_handler]


def test_an_invalid_level_falls_back_to_info(kok_logger, monkeypatch):
    """Yazim hatasi olan bir `LOG_LEVEL` gunlugu TAMAMEN susturmamali."""
    kok_logger.handlers.clear()
    monkeypatch.setenv("LOG_LEVEL", "VERBOSE")

    assert configure_root_logging() is True
    assert kok_logger.level == logging.INFO
