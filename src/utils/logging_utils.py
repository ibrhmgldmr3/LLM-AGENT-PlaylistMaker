import logging
import os
import re
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
        except Exception:  # bicimlendirme hatasi log'u dusurmemeli
            return True
        record.msg = redact_secrets(message)
        record.args = ()
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
