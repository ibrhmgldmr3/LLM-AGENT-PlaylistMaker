import logging
import os
from datetime import datetime
from typing import Optional


_LOGGERS = {}


def setup_logger(name: str, log_file: Optional[str] = None) -> logging.Logger:
    if name in _LOGGERS:
        return _LOGGERS[name]

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    _LOGGERS[name] = logger
    return logger


def run_log_path(run_dir: str) -> str:
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return os.path.join(run_dir, f"run_{ts}.log")
