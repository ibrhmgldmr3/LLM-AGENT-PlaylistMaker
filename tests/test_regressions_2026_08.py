"""Tek tek bulunmus hatalarin geri gelmemesi icin regresyon testleri.

Her test BIR hatayi hedefliyor ve adinda hatanin OZU geciyor; bir gun
basarisiz olursa neyin bozuldugu test adindan okunabilsin.
"""

from __future__ import annotations

import logging
import threading

import pytest

from src.config import AppConfig
from src.storage import SQLiteStore
from src.utils.logging_utils import _RedactSecretsFilter, redact_secrets

KEY = "k" * 44


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / "app.db"), encryption_key=KEY)


# --------------------------------------------- gunluk sinirda TOCTOU yarisi

def test_daily_limit_holds_under_concurrent_requests(tmp_path):
    """Es zamanli istekler siniri ASMAMALI.

    Sayma ve INSERT ayri islemlerken iki istek de kontrolu ayni (eski) sayiyla
    geciyordu. Kabul edilen calistirma sayisi sinira ESIT olmali, fazla degil.
    """
    db = str(tmp_path / "app.db")
    store = SQLiteStore(db, encryption_key=KEY)
    limit = 3
    workers = 12

    accepted: list[bool] = []
    lock = threading.Lock()
    start = threading.Barrier(workers)

    def attempt(index: int) -> None:
        # Her thread KENDI baglantisini kullansin: `SQLiteStore.connect` zaten
        # cagri basina baglanti aciyor, paylasilan bir imlec yok.
        worker_store = SQLiteStore(db, encryption_key=KEY)
        start.wait()
        ok = worker_store.create_run_within_daily_limit(
            f"run-{index}", "konu", {}, user_id="ali", max_per_day=limit
        )
        with lock:
            accepted.append(ok)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(accepted) == limit
    assert len(store.list_runs(user_id="ali", limit=100, offset=0)) == limit


def test_daily_limit_zero_means_unlimited(store):
    for index in range(5):
        assert store.create_run_within_daily_limit(
            f"run-{index}", "konu", {}, user_id="ali", max_per_day=0
        )


def test_daily_limit_is_per_user(store):
    assert store.create_run_within_daily_limit("a1", "k", {}, user_id="ali", max_per_day=1)
    assert not store.create_run_within_daily_limit("a2", "k", {}, user_id="ali", max_per_day=1)
    assert store.create_run_within_daily_limit("v1", "k", {}, user_id="veli", max_per_day=1)


# ------------------------------------- cozulemeyen sir hesabi kilitlememeli

def test_unreadable_oauth_token_reads_as_missing_not_error(tmp_path):
    """Anahtar degisirse hesap KILITLENMEMELI.

    Eskiden `decrypt()` `ValueError` firlatiyor, okuma uclari 500 donuyordu;
    "bagli mi" sorusu bile patladigi icin arayuz baglantiyi kesme yolunu bile
    gosteremiyor ve kullanicinin kurtulma sansi kalmiyordu.
    """
    db = str(tmp_path / "app.db")
    SQLiteStore(db, encryption_key=KEY).save_oauth_token("ali", "youtube", '{"token": "abc"}')

    # Anahtar rotasyonu / bozulma.
    rotated = SQLiteStore(db, encryption_key="x" * 44)

    assert rotated.get_oauth_token("ali", "youtube") is None

    # Ve kullanici yeniden yetkilendirebiliyor: yeni jeton eskisinin uzerine biner.
    rotated.save_oauth_token("ali", "youtube", '{"token": "yeni"}')
    assert rotated.get_oauth_token("ali", "youtube") == '{"token": "yeni"}'


# ------------------------------------------------ asr_available dogrulugu

def test_asr_available_follows_real_backend_selection(monkeypatch, tmp_path):
    """`asr_available`, `select_transcription_backend` ile ayni cevabi vermeli."""
    import importlib.util

    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name, *args, **kwargs):
        if name == "faster_whisper":
            return object()
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    # `auto` + faster-whisper kurulu: secim mantigi onu ONCELIKLI seciyor,
    # dolayisiyla yetenek `True` olmali. Eskiden `whisper_cpp_cli_path` bos
    # oldugu icin `False` donuyordu.
    config = AppConfig(gemini_api_key="k", asr_backend="auto", data_dir=str(tmp_path))
    assert config.public_capabilities()["asr_available"] is True


def test_asr_unavailable_when_selected_backend_missing(monkeypatch, tmp_path):
    """`auto` disi modda arka uc KURULU DEGILSE `True` donmemeli."""
    import importlib.util

    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name, *args, **kwargs):
        if name == "faster_whisper":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    config = AppConfig(
        gemini_api_key="k", asr_backend="faster-whisper", data_dir=str(tmp_path)
    )
    assert config.public_capabilities()["asr_available"] is False

    # whisper.cpp secili ama dosyalar yok.
    config = AppConfig(
        gemini_api_key="k",
        asr_backend="whisper.cpp",
        whisper_cpp_cli_path=str(tmp_path / "yok.exe"),
        whisper_cpp_model_path=str(tmp_path / "yok.bin"),
        data_dir=str(tmp_path),
    )
    assert config.public_capabilities()["asr_available"] is False


# ------------------------------- log redaksiyonu bicimlendirme hatasinda da

def test_redaction_survives_bad_format_string():
    """Bicimlendirme patlarsa bile sir DUZ METIN kalmamali.

    Eski kod bu durumda `record.msg`/`record.args`'i oldugu gibi birakip
    `True` donuyordu; handler `getMessage()`'i tekrar cagirip yine patliyor ve
    ham `args` stderr'e dokuluyordu.
    """
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="eksik arguman %s %s",  # args sayisi tutmuyor -> bicimlendirme hatasi
        args=("https://api.example.com/v3/search?key=AIzaSyTOPSECRETVALUE123",),
        exc_info=None,
    )

    assert _RedactSecretsFilter().filter(record) is True

    # Handler tarafinda IKINCI bir bicimlendirme denemesi olmamali.
    assert record.args == ()
    rendered = record.getMessage()
    assert "AIzaSyTOPSECRETVALUE123" not in rendered
    assert "***" in rendered


def test_redaction_normal_path_still_masks():
    assert "TOPSECRET" not in redact_secrets("?key=TOPSECRET&part=snippet")


# ------------------------------------------- YouTube hata nedeni yazim hatasi

def test_service_unavailable_is_classified_temporary():
    """`servicelunavailable` yazim hatasi geri gelmemeli."""
    from src.providers import youtube_data_api_provider as provider

    assert "serviceunavailable" in provider._TEMPORARY_REASONS
    assert "servicelunavailable" not in provider._TEMPORARY_REASONS


# ---------------------------- Gemini `thinking_config` yarisinda sahte hata

def test_thinking_retry_is_decided_per_call_not_by_shared_state():
    """Es zamanli iki cagri birbirinin retry hakkini CALMAMALI.

    `_thinking_unsupported` is parcaciklari arasinda paylasiliyor. Eskiden
    retry karari `except` icinde "model sette mi" diye bakiyordu; A'nin
    basarili retry'i modeli sete ekleyince, ayni anda ilk kez ayni modeli
    cagiran B kendi retry'ini yapamadan `raise` ediyordu. Sonuc: model aslinda
    calisiyorken alt konu / calisma notu "failed" isaretleniyordu.

    Burada B'nin bakis acisi canlandiriliyor: B config'ini set BOSKEN hesapliyor
    (yani `thinking_config` GONDERIYOR), sonra -- istek ucustayken A kazanmis
    gibi -- set doluyor ve ardindan B'nin istegi 400 ile donuyor.
    """
    from src.config import AppConfig
    from src.providers.llm_provider import GeminiLLMProvider

    provider = GeminiLLMProvider.__new__(GeminiLLMProvider)
    provider.config = AppConfig(gemini_api_key="test")
    provider._thinking_unsupported = set()

    calls: list[str] = []

    class _Models:
        def generate_content(self, model, contents, config):
            calls.append(config)
            if config == "with-thinking":
                # A thread'i tam bu sirada kazandi ve modeli sete ekledi.
                provider._thinking_unsupported.add(model)
                raise ValueError("400 INVALID_ARGUMENT: thinking_config not supported")
            return type("R", (), {"text": "tamam"})()

    provider.client = type("C", (), {"models": _Models()})()

    def config_factory(model_name):
        return "no-thinking" if model_name in provider._thinking_unsupported else "with-thinking"

    # ESKI kod burada ValueError firlatiyordu (model artik sette oldugu icin).
    assert provider._generate("gemini-test", "istem", config_factory) == "tamam"
    assert calls == ["with-thinking", "no-thinking"]


def test_thinking_unsupported_model_does_not_retry_forever():
    """Model zaten sette iken 400 gelirse SONSUZ retry olmamali; hata yukselmeli."""
    from src.config import AppConfig
    from src.providers.llm_provider import GeminiLLMProvider

    provider = GeminiLLMProvider.__new__(GeminiLLMProvider)
    provider.config = AppConfig(gemini_api_key="test")
    provider._thinking_unsupported = {"gemini-test"}

    attempts: list[int] = []

    class _Models:
        def generate_content(self, model, contents, config):
            attempts.append(1)
            raise ValueError("400 INVALID_ARGUMENT: baska bir sebep")

    provider.client = type("C", (), {"models": _Models()})()

    with pytest.raises(ValueError):
        provider._generate("gemini-test", "istem", lambda name: "no-thinking")

    assert len(attempts) == 1


# ------------------- yayinlamada HttpError olmayan hata sahipsiz playlist birakiyordu

def _publish_fixture(monkeypatch, failure: Exception):
    """Playlist olusturmayi taklit eder; ikinci videonun eklenmesi `failure` ile patlar."""
    import googleapiclient.discovery

    from src.models import FilterOptions, MetadataScore, PlaylistResult, Recommendation, VideoCandidate
    from src.services import playlist_publish_service as publish

    def _video(index: int) -> VideoCandidate:
        return VideoCandidate(
            video_id=f"v{index}",
            url=f"https://www.youtube.com/watch?v=v{index}",
            title=f"Video {index}",
            description="",
            channel="Kanal",
            duration_sec=600,
            view_count=1000,
            language="tr",
        )

    def _score() -> MetadataScore:
        return MetadataScore(
            total=5.0,
            title_relevance=1.0,
            description_relevance=1.0,
            channel_quality=1.0,
            duration_fit=1.0,
            difficulty_fit=0.5,
            language_match=0.5,
            freshness=0.0,
            engagement=0.0,
            rationale=["x"],
        )

    result = PlaylistResult(
        run_id="r1",
        topic="konu",
        filters=FilterOptions(),
        subtopics=[],
        recommendations=[
            Recommendation(
                position=index,
                subtopic="alt",
                video=_video(index),
                why_selected="cunku",
                confidence_score=0.9,
                transcript_status="available",
                metadata_score=_score(),
            )
            for index in (1, 2, 3)
        ],
    )

    class _Execute:
        def __init__(self, payload=None, raises=None):
            self._payload = payload
            self._raises = raises

        def execute(self):
            if self._raises:
                raise self._raises
            return self._payload

    class _PlaylistItems:
        def __init__(self):
            self.seen: list[str] = []

        def insert(self, part, body):
            video_id = body["snippet"]["resourceId"]["videoId"]
            self.seen.append(video_id)
            return _Execute(payload={}, raises=failure if video_id == "v2" else None)

    class _Service:
        def __init__(self):
            self._items = _PlaylistItems()

        def playlists(self):
            return type("P", (), {"insert": lambda _self, part, body: _Execute({"id": "PL123"})})()

        def playlistItems(self):
            return self._items

    service = _Service()
    monkeypatch.setattr(googleapiclient.discovery, "build", lambda *a, **k: service)
    monkeypatch.setattr(publish, "_credentials_from_token", lambda *a, **k: object())
    return publish, result, service


def test_non_http_error_while_adding_items_still_returns_the_playlist_url(monkeypatch, tmp_path):
    """Playlist ZATEN olustu; ag hatasi URL'i yutmamali.

    Eskiden yalnizca `HttpError` yakalaniyordu. Soket hatasi / timeout gibi
    `HttpError` OLMAYAN istisnalar yukari firliyor, kullanicinin hesabinda
    yarim kalmis bir playlist ve elinde ne URL ne uyari kaliyordu.
    """
    publish, result, service = _publish_fixture(monkeypatch, TimeoutError("baglanti zaman asimi"))

    config = AppConfig(
        gemini_api_key="k",
        youtube_oauth_client_id="id",
        youtube_oauth_client_secret="secret",
        data_dir=str(tmp_path),
    )

    published = publish.create_youtube_playlist(config, result, token_json='{"token": "t"}')

    assert published.url == "https://www.youtube.com/playlist?list=PL123"
    assert published.added == 2
    assert len(published.warnings) == 1
    assert "Video 2" in published.warnings[0]
    # Hata, KALAN videolarin eklenmesini de durdurmamali.
    assert service.playlistItems().seen == ["v1", "v2", "v3"]


# ------------------- paylasimli anahtar + sinirsiz calistirma acilista gorunsun

def _client_with(monkeypatch, tmp_path, **config_overrides):
    from fastapi.testclient import TestClient

    from api import deps

    config = AppConfig(
        gemini_api_key="k",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "app.db"),
        secret_encryption_key=KEY,
        **config_overrides,
    )
    config.ensure_directories()
    monkeypatch.setattr(deps, "_base_config", lambda: config)

    from api.main import app

    return TestClient(app)


def test_multi_user_without_a_daily_limit_warns_on_startup(monkeypatch, tmp_path, caplog):
    """Sessiz kalmamali: bu kombinasyon kotayi tek kullaniciya actiriyor.

    Anahtarlar paylasimli oldugu icin `multi_user` + sinirsiz, giris yapan tek
    bir kullanicinin gunluk YouTube kotasinin tamamini tuketmesine izin veriyor.
    Varsayilan 0 oldugundan bu durum yapilandirmaya hic dokunmayan kurulumlarda
    KENDILIGINDEN olusuyor -- bu yuzden acilista goze carpmasi gerekiyor.
    """
    with caplog.at_level(logging.WARNING, logger="api"):
        with _client_with(monkeypatch, tmp_path, auth_mode="multi_user", max_runs_per_user_per_day=0):
            pass

    uyarilar = [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]
    assert any("MAX_RUNS_PER_USER_PER_DAY=0" in message for message in uyarilar), uyarilar


def test_no_warning_when_the_limit_is_set(monkeypatch, tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="api"):
        with _client_with(monkeypatch, tmp_path, auth_mode="multi_user", max_runs_per_user_per_day=3):
            pass

    assert not [r for r in caplog.records if "MAX_RUNS_PER_USER_PER_DAY" in r.getMessage()]


def test_no_warning_in_single_user_mode(monkeypatch, tmp_path, caplog):
    """Tek kullanicili kurulumda sinirsiz olmasi normal: kota zaten sahibinin."""
    with caplog.at_level(logging.WARNING, logger="api"):
        with _client_with(monkeypatch, tmp_path, auth_mode="single_user", max_runs_per_user_per_day=0):
            pass

    assert not [r for r in caplog.records if "MAX_RUNS_PER_USER_PER_DAY" in r.getMessage()]
