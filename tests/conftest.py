"""Testlerin ortak yardimcilari."""

from __future__ import annotations

import os
import uuid

import pytest

from src.jobs import register_task, unregister_task
from src.storage import SQLiteStore
from src.storage.dialect import PostgresDialect

# Postgres kosumu OPT-IN: bu degisken tanimli degilse o parametre atlaniyor.
# Varsayilan olarak acmak, veritabani olmayan her gelistiricide ve CI'da
# suiti kirardi -- ve "atlandi" satiri, sessizce SQLite'a dusmekten cok daha
# durust bir sinyal.
POSTGRES_DSN_ENV = "MAP_TEST_POSTGRES_DSN"

# Ulasilamayan bir sunucunun atlama sebebi BIR KEZ olculuyor. Her testte
# yeniden denemek olculdu: kapali bir portta 17 test 69 saniye suruyordu,
# cunku baglanti denemesinin zaman asimi test basina odeniyor.
_postgres_skip_reason: str | None = None


@pytest.fixture
def task():
    """Ad-hoc bir isi kaydeder ve `submit` icin ADINI doner.

    `JobRunner.submit` artik closure degil is ADI aliyor: paylasimli bir
    kuyruga yazilacak sey kod degil veri olmali (bkz. `src/jobs/tasks.py`).
    Runner MEKANIGINI (iptal, olay gunlugu, es zamanlilik) sinayan testlerin
    yine de keyfi fonksiyonlara ihtiyaci var; bu fixture o ihtiyaci sozlesmeyi
    delmeden karsiliyor.

    Kayitlar test bitince TEK TEK siliniyor: `clear_tasks` gercek kayitlari da
    (`build_playlist`) silip testler arasi sira bagimliligi yaratirdi.

    Test fonksiyonlari `(emit)` imzasini kullanmaya devam ediyor; baglami
    ilgilendirmeyen testlere `TaskContext` dayatmanin bir faydasi yok.
    """
    registered: list[str] = []

    def register(fn):
        name = f"_test_task_{uuid.uuid4().hex[:12]}"
        register_task(name, lambda context, emit: fn(emit))
        registered.append(name)
        return name

    yield register

    for name in registered:
        unregister_task(name)


# ------------------------------------------------------------------- depolama


def _database_name(dsn: str) -> str:
    import psycopg2.extensions

    return psycopg2.extensions.parse_dsn(dsn).get("dbname", "")


def _fresh_postgres_dsn() -> str:
    """Bos bir Postgres semasi hazirlar ve DSN'i doner.

    Sema her testte SIFIRLANIYOR. Alternatif her testte yeni bir veritabani
    yaratmakti; `DROP SCHEMA public CASCADE` ayni yalitimi veriyor ve
    olcusmeyecek kadar hizli.
    """
    global _postgres_skip_reason
    if _postgres_skip_reason is not None:
        pytest.skip(_postgres_skip_reason)

    dsn = os.environ.get(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        _postgres_skip_reason = f"{POSTGRES_DSN_ENV} tanimli degil"
        pytest.skip(_postgres_skip_reason)
    psycopg2 = pytest.importorskip("psycopg2")

    # GUVENLIK KAPISI. Asagisi veritabanindaki HER SEYI siliyor. Yanlislikla
    # bir gelistirme veritabaninin DSN'i verilirse bunu geri alacak bir sey
    # yok, o yuzden adinda "test" gecmeyen bir hedefe hic dokunmuyoruz.
    name = _database_name(dsn)
    if "test" not in name.lower():
        pytest.fail(
            f"{POSTGRES_DSN_ENV} veritabani adinda 'test' gecmiyor ({name!r}); "
            "testler semayi bastan yaratiyor ve bu veri kaybi olurdu"
        )

    try:
        conn = psycopg2.connect(dsn, connect_timeout=5)
    except psycopg2.OperationalError as exc:
        _postgres_skip_reason = f"Postgres'e baglanilamadi ({exc.__class__.__name__})"
        pytest.skip(_postgres_skip_reason)
    try:
        conn.set_session(autocommit=True)
        with conn.cursor() as cursor:
            cursor.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public")
    finally:
        conn.close()
    return dsn


@pytest.fixture
def postgres_dsn() -> str:
    """Bos bir Postgres semasi; yoksa test ATLANIR."""
    return _fresh_postgres_dsn()


@pytest.fixture(params=["sqlite", "postgres"])
def store(request, tmp_path) -> SQLiteStore:
    """Depoyu IKI lehceye karsi da kurar.

    Neden parametreli: lehce farklari (yer tutucu, uretilen anahtar, upsert,
    tam metin) yalnizca gercek veritabanina karsi gorunuyor. SQLite'a karsi
    test edip Postgres'te kosmak, bu hatalari tam da goremeyecegimiz yerde
    saklardi -- Redis'te ayni ders ODENDI: elle yazilmis bir taklit, gercek
    bagimliligin ortaya cikardigi hatayi bulamamisti.
    """
    dialect = (
        PostgresDialect(_fresh_postgres_dsn()) if request.param == "postgres" else None
    )
    return SQLiteStore(str(tmp_path / "app.db"), dialect=dialect)
