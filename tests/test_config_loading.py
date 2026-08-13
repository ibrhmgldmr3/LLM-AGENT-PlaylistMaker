"""`.env` yukleme dayanikliligi."""

import pytest

from src.config import settings
from src.config.settings import load_config


def _write(path, content: bytes):
    path.write_bytes(content)
    return str(path)


def test_null_bytes_produce_an_actionable_error(tmp_path, monkeypatch):
    """Regresyon: PowerShell `>>` ile eklenen UTF-16 satir, ham
    `ValueError: embedded null character` yigin izine yol aciyordu."""
    env_file = tmp_path / ".env"
    # Ilk satir UTF-8, ikinci satir UTF-16 (PowerShell `>>` davranisi).
    _write(env_file, b"GEMINI_API_KEY=abc\n" + "YTDLP_COOKIES_FROM_BROWSER=chrome\n".encode("utf-16-le"))

    monkeypatch.setattr(settings, "find_dotenv", lambda usecwd=True: str(env_file))

    with pytest.raises(RuntimeError) as excinfo:
        load_config()

    message = str(excinfo.value)
    assert "null bayt" in message
    assert "PowerShell" in message
    assert "utf8" in message  # nasil duzeltilecegi soyleniyor


def test_reports_the_damaged_line_numbers(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    _write(env_file, b"A=1\nB=2\n" + "C=3\n".encode("utf-16-le"))
    monkeypatch.setattr(settings, "find_dotenv", lambda usecwd=True: str(env_file))

    with pytest.raises(RuntimeError) as excinfo:
        load_config()

    assert "satır 3" in str(excinfo.value)


def test_clean_utf8_env_loads(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    _write(env_file, "GEMINI_API_KEY=abc\nGEMINI_MODEL=gemini-test\n".encode("utf-8"))
    monkeypatch.setattr(settings, "find_dotenv", lambda usecwd=True: str(env_file))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    config = load_config()

    assert config.gemini_api_key == "abc"
    assert config.gemini_model == "gemini-test"


def test_utf8_with_bom_still_loads(tmp_path, monkeypatch):
    """BOM'lu UTF-8 (Windows editorlerinde yaygin) sorun cikarmamali."""
    env_file = tmp_path / ".env"
    _write(env_file, b"\xef\xbb\xbf" + "GEMINI_API_KEY=abc\n".encode("utf-8"))
    monkeypatch.setattr(settings, "find_dotenv", lambda usecwd=True: str(env_file))
    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert config.gemini_api_key == "abc"


def test_missing_env_file_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "find_dotenv", lambda usecwd=True: "")
    monkeypatch.setenv("GEMINI_API_KEY", "abc")
    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert config.gemini_api_key == "abc"
