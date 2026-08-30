"""`DATABASE_URL` deponun lehcesini secmeli -- HER cagri yerinde.

Depoyu kuran alti cagri yeri vardi. Birinin fabrikaya baglanmasi unutulursa
uygulamanin yarisi Postgres'e, yarisi SQLite'a yazar: sessizce BOLUNMUS bir
veritabani, en gec fark edilen ariza turlerinden biri. Bu suit o unutmayi
yakalamak icin var.

`create_store` gercek bir baglanti ACMIYOR -- `SQLiteStore.__init__` semayi
kurmak icin baglaniyor. Bu yuzden testler lehce SECIMINI dogruluyor,
Postgres'e karsi calismayi degil; o `tests/test_space_storage.py`de gercek
sunucuya karsi olculuyor.
"""

from __future__ import annotations

import pytest

from src.config import AppConfig
from src.storage import backend_name, create_dialect
from src.storage.dialect import PostgresDialect


def _config(tmp_path, **overrides) -> AppConfig:
    config = AppConfig(
        gemini_api_key="test",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "cache" / "app.db"),
        **overrides,
    )
    return config


def test_without_a_dsn_the_store_stays_on_sqlite(tmp_path):
    """Varsayilan DEGISMEDI: `DATABASE_URL` tanimsizken bugunku davranis."""
    config = _config(tmp_path)

    assert create_dialect(config) is None
    assert backend_name(config) == "sqlite"


def test_a_dsn_selects_postgres(tmp_path):
    config = _config(tmp_path, database_url="postgresql://u:p@h:5432/db")

    dialect = create_dialect(config)

    assert isinstance(dialect, PostgresDialect)
    assert dialect.dsn == "postgresql://u:p@h:5432/db"
    assert backend_name(config) == "postgres"


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_dsn_is_not_a_dsn(tmp_path, blank):
    """`.env`'de `DATABASE_URL=` yazmak "Postgres istiyorum" DEMEK DEGIL.

    Bos degeri bir DSN saymak, ornek `.env`'i kopyalayan herkesi anlamsiz bir
    baglanti hatasina dusururdu.
    """
    config = _config(tmp_path, database_url=blank)

    assert create_dialect(config) is None
    assert backend_name(config) == "sqlite"


def test_the_dsn_comes_from_the_environment(monkeypatch):
    """`.env`'e yazmak yetmiyor: adin `ENV_TO_FIELD`de de olmasi gerekiyor.

    Bu esleme unutulursa ayar SESSIZCE yoksayilir -- "yazdim ama hicbir sey
    degismedi" durumunun tam kaynagi.
    """
    from src.config.settings import ENV_TO_FIELD, load_config, reset_base_config

    assert ENV_TO_FIELD["DATABASE_URL"] == "database_url"

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/db")
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    reset_base_config()
    try:
        assert load_config().database_url == "postgresql://u:p@h:5432/db"
    finally:
        reset_base_config()


# ------------------------------------------------- her cagri yeri fabrikada mi


def test_no_call_site_builds_the_store_directly():
    """Kablolamanin ASIL guvencesi.

    Yukaridaki testler fabrikanin dogru sectigini gosteriyor; bu test
    fabrikanin ATLANMADIGINI gosteriyor. Ikincisi olmadan yeni bir
    `SQLiteStore(...)` cagrisi sessizce eklenebilir ve o cagri yeri
    `DATABASE_URL`i hic gormezdi.
    """
    import pathlib

    # Fabrikayi ATLAMASI mesru olan iki yer. Liste kisa ve gerekcesi yazili
    # olmali: buyumeye baslamasi, kablolamanin cozuldugunun isareti olur.
    allowed = {
        # Fabrikanin KENDISI kurmak zorunda.
        "src/storage/factory.py",
        # Aktarim araci IKI tarafi da acikca kuruyor: kaynak SQLite dosyasi,
        # hedef Postgres. Fabrika tek bir yapilandirmadan TEK depo uretiyor,
        # yani bu ihtiyaci tanimi geregi karsilayamaz.
        "src/storage/transfer.py",
    }

    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for path in list((root / "src").rglob("*.py")) + list((root / "api").rglob("*.py")):
        if path.relative_to(root).as_posix() in allowed:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("*"):
                continue
            if "SQLiteStore(" in stripped:
                offenders.append(f"{path.relative_to(root)}:{number}: {stripped}")

    assert offenders == [], (
        "Depo dogrudan kurulmus; `src.storage.create_store` kullanin:\n"
        + "\n".join(offenders)
    )


# ----------------------------------------------------- gercek sunucuya karsi


def test_the_api_serves_a_run_stored_in_postgres(postgres_dsn, tmp_path, monkeypatch):
    """UCTAN UCA: `DATABASE_URL` verilince uygulama gercekten Postgres'e yaziyor.

    Yukaridaki testler lehce SECIMINI dogruluyor; bu test secimin ISE
    YARADIGINI dogruluyor. Onemi su: HTTP istegi deponun fabrikadan gecen ayri
    bir kurulumuna (`api.deps.get_store`) dusuyor, uygulamanin acilisi da
    baskalarina (`_mark_interrupted_runs`, `_purge_orphan_run_dirs`). Hepsinin
    AYNI veritabanina baktigini ancak yazan ile okuyanin farkli cagri yerleri
    oldugu boyle bir akis gosterebilir.

    Postgres yoksa atlanir.
    """
    from fastapi.testclient import TestClient

    from src.config import settings
    from src.models import (
        ExportArtifacts,
        FilterOptions,
        MetadataScore,
        PlaylistResult,
        Recommendation,
        VideoCandidate,
    )
    from src.storage import create_store

    config = AppConfig(
        gemini_api_key="test",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "cache" / "app.db"),
        database_url=postgres_dsn,
    )
    config.ensure_directories()
    monkeypatch.setattr(settings, "base_config", lambda: config)

    # YAZAN taraf: arka plan isinin kullandigi kurulum yolu.
    store = create_store(config)
    assert store._dialect.name == "postgres"
    video = VideoCandidate(video_id="v1", url="https://youtu.be/v1", title="Kalman")
    score = MetadataScore(
        total=8.0, title_relevance=3.0, description_relevance=1.0, channel_quality=1.0,
        duration_fit=1.0, difficulty_fit=0.5, language_match=1.0, freshness=0.3,
        engagement=0.2, rationale=[],
    )
    store.create_run("r1", "Kalman filtresi", {}, user_id="local")
    store.finalize_run(
        "r1",
        PlaylistResult(
            run_id="r1",
            topic="Kalman filtresi",
            filters=FilterOptions(),
            subtopics=[],
            recommendations=[
                Recommendation(
                    position=1, subtopic="Giriş", video=video, why_selected="temel",
                    confidence_score=8.0, transcript_status="available",
                    metadata_score=score,
                )
            ],
            exports=ExportArtifacts(json_path="/yok/r.json", markdown_path="/yok/s.md"),
        ),
    )

    # OKUYAN taraf: HTTP istegi, kendi depo kurulumuyla.
    from api.main import app

    with TestClient(app) as client:
        response = client.get("/api/runs/r1/export/json")

    assert response.status_code == 200, response.text
    assert response.json()["topic"] == "Kalman filtresi"


# ------------------------------------------------------- acilis uyarisi


def _multi_process_log(monkeypatch, caplog, **server_overrides) -> str:
    import logging

    from api import main as api_main

    server = AppConfig(gemini_api_key="test", **server_overrides).server_config()
    monkeypatch.setattr(api_main, "get_server_config", lambda: server)
    with caplog.at_level(logging.INFO, logger=api_main.logger.name):
        api_main._warn_on_multi_process()
    return "\n".join(record.message for record in caplog.records)


def test_redis_with_sqlite_does_not_claim_multi_machine_support(monkeypatch, caplog):
    """Paylasimli IS KUYRUGU tek basina makineler arasi olceklemeye yetmiyor.

    Bu satir ikinci makineyi acacak kisinin okudugu tek yer olabilir. "Coklu
    replika destekleniyor" demek, depo hala tek bir SQLite DOSYASI iken YANLIS
    olurdu -- ve yanlisligi ancak iki makine acildiktan sonra, veriler ikiye
    bolununce anlasilirdi.
    """
    message = _multi_process_log(monkeypatch, caplog, job_backend="redis")

    assert "DATABASE_URL" in message
    assert "ÇOKLU MAKİNE çalışmaz" in message


def test_redis_with_postgres_reports_multi_machine_support(monkeypatch, caplog):
    message = _multi_process_log(
        monkeypatch,
        caplog,
        job_backend="redis",
        database_url="postgresql://u:p@h:5432/db",
    )

    assert "postgres" in message
    assert "Çoklu makine/replika" in message


def test_memory_backend_still_warns_about_a_single_process(monkeypatch, caplog):
    """Varsayilan yolun uyarisi DEGISMEDI."""
    message = _multi_process_log(monkeypatch, caplog, job_backend="memory")

    assert "tek süreç" in message.lower()
