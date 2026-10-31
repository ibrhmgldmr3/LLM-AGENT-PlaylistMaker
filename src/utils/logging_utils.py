import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional


# API anahtari sorgu parametresi olarak gonderildigi icin requests istisnalarinin
# metni tam URL'i (ve anahtari) icerebiliyor. Log'a ve kullanici uyarilarina
# dusmeden once maskele.
_SECRET_PATTERNS = (
    re.compile(r"((?:[?&])(?:key|api_key|apikey|access_token|token|client_secret)=)[^&\s\"'<>]+", re.IGNORECASE),
    re.compile(r"(AIza)[0-9A-Za-z_\-]{10,}"),
)

_LOGGERS: dict[tuple[str, Optional[str]], logging.Logger] = {}


def redact_secrets(text: str) -> str:
    if not text:
        return text
    redacted = _SECRET_PATTERNS[0].sub(r"\1***", text)
    redacted = _SECRET_PATTERNS[1].sub(r"\1***", redacted)
    return redacted


class _RedactSecretsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            # Bicimlendirme hatasi log'u dusurmemeli AMA eski `return True`,
            # ham `msg`/`args` ciftini maskelenmeden handler'a birakiyordu:
            # handler `getMessage()`'i yeniden cagirip yine patliyor ve
            # `logging.Handler.handleError` ham `args` repr'ini stderr'e
            # dokuyordu -- sizinti tam da sirrin `args` icinde oldugu
            # durumda gerceklesiyordu.
            #
            # Cozum: `%` bicimlendirmesine HIC girmeyen tek parcali, maskelenmis
            # bir mesaj birakmak. `args` bosaltildigi icin handler tarafinda
            # ikinci bir bicimlendirme denemesi de olmuyor.
            record.msg = "[log biçimlendirme hatası] " + redact_secrets(
                f"msg={record.msg!r} args={record.args!r}"
            )
            record.args = ()
            return True
        record.msg = redact_secrets(message)
        record.args = ()
        return True


LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def configure_root_logging(level: Optional[str] = None) -> bool:
    """KOK logger'a handler takar; uygulamanin kendi kayitlari gorunur olsun.

    uvicorn yalnizca KENDI logger'larini (`uvicorn`, `uvicorn.error`,
    `uvicorn.access`) yapilandiriyor, koke dokunmuyor. `api` ve `src.*` altindaki
    kayitlar oraya propagate oluyor, handler bulamiyor ve Python'in `lastResort`
    handler'ina dusuyor -- o da SADECE WARNING ve ustunu, zaman damgasiz ve
    logger adi olmadan stderr'e yaziyor. Sonuc: acilistaki tek-surec kisiti,
    "yarida kalan N calistirma isaretlendi", "frontend mount edildi" gibi INFO
    kayitlarinin tamami uretimde SESSIZCE kayboluyordu -- tam da bir sunucunun
    dogru ayaga kalktigini dogrulamak icin bakilacak satirlar.

    Handler'a maskeleme filtresi de takiliyor: sir sizdirabilecek her kayit
    (bkz. `redact_secrets`) TEK yerde temizleniyor, her logger'in bunu kendi
    basina hatirlamasi gerekmiyor.

    Seviye `LOG_LEVEL` ile ayarlanir; varsayilan INFO.

    Kok logger'da ZATEN handler varsa hicbir sey yapmaz ve `False` doner:
    pytest, gunicorn ya da gomuldugu baska bir uygulama kendi yapilandirmasini
    kurmussa onun ustune yazmak, bu fonksiyonun cozdugu sorunun aynisini karsi
    tarafa yasatirdi.
    """
    root = logging.getLogger()
    if root.handlers:
        return False

    requested = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    resolved = getattr(logging, requested, None)
    # Gecersiz bir `LOG_LEVEL` yuzunden gunluk TAMAMEN susmamali.
    if not isinstance(resolved, int):
        resolved = logging.INFO

    # Windows konsolu stdout'u cp1254/cp857 ile aciyor ve log mesajlarindaki
    # Turkce harfler `?` olarak cikiyordu ("Is durumu" -> "?? durumu"). Linux
    # kabinda sorun yok, ama gelistirme burada yapiliyor ve okunamayan bir
    # gunluk, olmayan bir gunlukten cok da iyi degil. `errors="replace"`:
    # kodlanamayan tek bir karakter YUZUNDEN kayit dusmesin.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        # Yeniden yapilandirilamayan bir akim (yakalanmis/sarmalanmis stdout).
        pass

    # stdout: kap gunluklerinde uygulama ciktisi ile hata akisi karismasin.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(_RedactSecretsFilter())

    root.addHandler(handler)
    root.setLevel(resolved)
    return True


def setup_logger(name: str, log_file: Optional[str] = None) -> logging.Logger:
    """Ada VE log dosyasina gore onbelleklenmis logger dondurur.

    Sadece ada gore onbelleklemek, ikinci ve sonraki calistirmalarin loglarini
    ilk calistirmanin dosyasina yazdiriyordu.
    """
    cache_key = (name, log_file)
    if cache_key in _LOGGERS:
        return _LOGGERS[cache_key]

    # Her (ad, dosya) cifti kendi logger'ini alsin ki handler'lar karismasin.
    logger_name = name if log_file is None else f"{name}.{abs(hash(log_file)):x}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    redactor = _RedactSecretsFilter()

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(redactor)
    logger.addHandler(stream_handler)

    if log_file:
        directory = os.path.dirname(log_file)
        if directory:
            os.makedirs(directory, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redactor)
        logger.addHandler(file_handler)

    _LOGGERS[cache_key] = logger
    return logger


def close_logger(name: str, log_file: Optional[str] = None) -> None:
    """Handler'lari kapatip onbellekten dusurur; dosya tanitici sizintisini onler."""
    logger = _LOGGERS.pop((name, log_file), None)
    if logger is None:
        return
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass


def run_log_path(run_dir: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return os.path.join(run_dir, f"run_{ts}.log")
