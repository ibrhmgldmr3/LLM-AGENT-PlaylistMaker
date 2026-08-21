"""`ALLOW_UNSAFE_OPENMP_WORKAROUND` gercekten dinleniyor mu.

Bayrak Windows'ta cift Intel OpenMP calisma zamani cokmesini asiyor ama
`KMP_DUPLICATE_LIB_OK` SUREC GENELI bir degisken: bir kez kurulunca ayni
surecteki her sey etkileniyor. Bu yuzden "kim, ne zaman kuruyor" onemli.

Kilitlenen davranis: bayragi kuran tek yer, config'e BAKAN kullanim
noktalari. Paket importu bunu yapmamali -- import config okunmadan once
gerceklesiyor, dolayisiyla oradaki bir `setdefault` kullanicinin `false`
secimini sessizce eziyor (ve `setdefault` oldugu icin sonraki dogru
kontrolleri de etkisiz birakiyordu).
"""

import importlib
import os

import pytest

from src.config import AppConfig
from src.providers.faster_whisper_provider import FasterWhisperProvider


FLAG = "KMP_DUPLICATE_LIB_OK"


@pytest.fixture
def clean_flag(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    return FLAG


class _FakeSegment:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModel:
    def transcribe(self, *args, **kwargs):
        segments = [_FakeSegment("x" * 200)]
        return segments, type("Info", (), {"language": "tr"})()


def _provider(*, allowed: bool) -> FasterWhisperProvider:
    config = AppConfig(gemini_api_key="k", allow_unsafe_openmp_workaround=allowed)
    provider = FasterWhisperProvider(config=config)
    provider._load_model = lambda: _FakeModel()  # type: ignore[method-assign]
    return provider


def test_importing_src_does_not_set_flag(clean_flag, monkeypatch):
    """Paket importu bayragi kurmamali -- config henuz okunmadi.

    Regresyon: `src/__init__.py` bunu KOSULSUZ yapiyordu, yani
    `ALLOW_UNSAFE_OPENMP_WORKAROUND=false` Windows'ta olu bir ayardi.

    `os.name` sahte "nt": aksi halde bu test Windows disinda (CI) regresyonu
    hic goremezdi.
    """
    monkeypatch.setattr(os, "name", "nt")
    import src

    importlib.reload(src)

    assert clean_flag not in os.environ


def test_transcribe_does_not_set_flag_when_disabled(clean_flag, monkeypatch):
    monkeypatch.setattr(os, "name", "nt")

    result = _provider(allowed=False).transcribe("a.wav", "vid")

    assert result.status == "available"
    assert clean_flag not in os.environ


def test_transcribe_sets_flag_when_enabled(clean_flag, monkeypatch):
    monkeypatch.setattr(os, "name", "nt")

    _provider(allowed=True).transcribe("a.wav", "vid")

    assert os.environ[clean_flag] == "TRUE"
    monkeypatch.delenv(clean_flag, raising=False)


def test_flag_untouched_off_windows(clean_flag, monkeypatch):
    """Sorun Windows'a ozgu; baska yerde ortami kirletmeye gerek yok."""
    monkeypatch.setattr(os, "name", "posix")

    _provider(allowed=True).transcribe("a.wav", "vid")

    assert clean_flag not in os.environ
