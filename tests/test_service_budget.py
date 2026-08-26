"""Servis geneli gunluk kota tavani.

Kullanici basina sinir (`MAX_RUNS_PER_USER_PER_DAY`) tek bir kullanicinin
asiri tuketmesini engelliyor ama TOPLAMI sinirlamiyor: arama kotasi tum
kullanicilar icin ortak ve gunde ~8 calistirmalik. Yeterince kullanici,
her biri kendi hakki icinde kalarak kotayi bitirebilir.

Tavan asildiginda YouTube 403 doner ve calistirma YARIDA olur -- LLM cagrisi
da bosa gider. Onceden reddetmek her durumda daha iyi.
"""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from src.config import AppConfig, ServerConfig
from src.providers.youtube_data_api_provider import estimate_run_units
from src.storage import SQLiteStore
from src.storage.sqlite_store import seconds_until_next_quota_day

KEY = "k" * 44


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / "app.db"), encryption_key=KEY)


def _spend(store: SQLiteStore, units: int, user_id: str = "ali") -> None:
    store.record_api_usage(user_id, "youtube_data_api", "search.list", units)


# --------------------------------------------------------------- kabul karari

def test_a_run_is_refused_when_it_would_exceed_the_budget(store):
    _spend(store, 9_000)

    karar = store.create_run_within_daily_limit(
        "r1", "k", {}, user_id="ali", max_per_day=0,
        max_units_per_day=10_000, estimated_units=1_224,
    )

    assert not karar
    assert karar.reason == "service_budget"


def test_a_run_that_fits_is_accepted(store):
    _spend(store, 8_000)

    karar = store.create_run_within_daily_limit(
        "r1", "k", {}, user_id="ali", max_per_day=0,
        max_units_per_day=10_000, estimated_units=1_224,
    )

    assert karar
    assert karar.reason is None


def test_the_two_ceilings_are_reported_separately(store):
    """Kullaniciya "senin hakkin doldu" ile "servis doldu" ayni sey degil."""
    karar = store.create_run_within_daily_limit(
        "r1", "k", {}, user_id="ali", max_per_day=1, max_units_per_day=10_000,
        estimated_units=10,
    )
    assert karar

    kullanici = store.create_run_within_daily_limit(
        "r2", "k", {}, user_id="ali", max_per_day=1, max_units_per_day=10_000,
        estimated_units=10,
    )
    assert kullanici.reason == "user_limit"

    _spend(store, 9_999)
    servis = store.create_run_within_daily_limit(
        "r3", "k", {}, user_id="veli", max_per_day=1, max_units_per_day=10_000,
        estimated_units=1_224,
    )
    assert servis.reason == "service_budget"


def test_budget_zero_means_the_ceiling_is_off(store):
    _spend(store, 999_999)

    assert store.create_run_within_daily_limit(
        "r1", "k", {}, user_id="ali", max_per_day=0, max_units_per_day=0, estimated_units=1_224
    )


def test_yesterdays_spending_does_not_count_against_today(store):
    _spend(store, 9_999)
    store.record_api_usage("ali", "youtube_data_api", "search.list", 500_000, day="2020-01-01")

    karar = store.create_run_within_daily_limit(
        "r1", "k", {}, user_id="ali", max_per_day=0,
        max_units_per_day=10_000, estimated_units=1,
    )
    assert karar, "eski gunun harcamasi bugunu kilitlememeli"


def test_the_ceiling_holds_under_concurrent_requests(tmp_path):
    """Es zamanli istekler butceyi ASMAMALI.

    Iki asamali bir yaris var ve ikincisi ilk denememde gozden kacti:

    1. Iki istek ayni anda kabul kontrolunden gecebilir -- `BEGIN IMMEDIATE`
       bunu cozuyor.
    2. Kabul edilen calistirmanin GERCEK harcamasi dakikalar sonra olusuyor.
       Yalnizca gerceklesmis harcamaya bakan bir kontrol, ucustaki
       calistirmalari hic gormeden hepsini kabul ediyordu: 5 calistirmalik
       butceye 6 kabul edildi. Cozum, kabul aninda butceden AYIRMAK.

    Burada kesin bir kabul sayisi degil ASIL DEGISMEZ dogrulaniyor: gercek
    harcama hicbir kosulda butceyi asmamali. Kesin aritmetik icin yukaridaki
    tek is parcacikli testler var -- es zamanlilikta kabul sayisi, rezervasyon
    ile gercek harcamanin ortustugu pencereye bagli olarak mesru sekilde
    degisebiliyor (tutucu tarafta).
    """
    db = str(tmp_path / "app.db")
    SQLiteStore(db, encryption_key=KEY).record_api_usage(
        "ali", "youtube_data_api", "search.list", 5_000
    )

    butce, tahmin, workers = 10_000, 1_000, 16  # kalan 5.000 -> en fazla 5 calistirma
    kabuller: list[bool] = []
    lock = threading.Lock()
    start_barrier = threading.Barrier(workers)

    def dene(index: int) -> None:
        from src.models import FilterOptions, PlaylistResult

        worker = SQLiteStore(db, encryption_key=KEY)
        start_barrier.wait()
        karar = worker.create_run_within_daily_limit(
            f"r{index}", "k", {}, user_id=f"u{index}", max_per_day=0,
            max_units_per_day=butce, estimated_units=tahmin,
        )
        if karar:
            # Gercek yasam dongusu: harca, sonra bitir (rezervasyon birakilir).
            worker.record_api_usage(f"u{index}", "youtube_data_api", "search.list", tahmin)
            worker.finalize_run(
                f"r{index}",
                PlaylistResult(
                    run_id=f"r{index}", topic="k", filters=FilterOptions(),
                    subtopics=[], recommendations=[],
                ),
            )
        with lock:
            kabuller.append(bool(karar))

    threads = [threading.Thread(target=dene, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    kabul_sayisi = sum(1 for k in kabuller if k)
    harcanan = SQLiteStore(db, encryption_key=KEY).sum_api_units()

    assert harcanan <= butce, f"butce asildi: {harcanan} > {butce}"
    assert kabul_sayisi <= 5
    assert kabul_sayisi >= 1, "hepsini reddetmek de dogru davranis degil"


def test_an_in_flight_run_holds_its_reservation(tmp_path):
    """Ucustaki calistirma butceden yer TUTMALI -- harcamasi henuz olusmamis olsa bile.

    Bu, es zamanlilik testinin yakaladigi ikinci yarisin tek is parcacikli,
    deterministik hali.
    """
    store = SQLiteStore(str(tmp_path / "app.db"), encryption_key=KEY)

    # Butcenin yarisini alan bir calistirma kabul edildi ama HENUZ harcamadi.
    assert store.create_run_within_daily_limit(
        "r1", "k", {}, user_id="ali", max_per_day=0,
        max_units_per_day=10_000, estimated_units=6_000,
    )
    assert store.sum_api_units() == 0, "henuz hicbir sey harcanmadi"

    # Ikincisi SIGMAMALI: birincisi yer tutuyor.
    ikinci = store.create_run_within_daily_limit(
        "r2", "k", {}, user_id="veli", max_per_day=0,
        max_units_per_day=10_000, estimated_units=6_000,
    )
    assert ikinci.reason == "service_budget"


def test_finishing_a_run_releases_its_reservation(tmp_path):
    from src.models import FilterOptions, PlaylistResult

    store = SQLiteStore(str(tmp_path / "app.db"), encryption_key=KEY)
    store.create_run_within_daily_limit(
        "r1", "k", {}, user_id="ali", max_per_day=0,
        max_units_per_day=10_000, estimated_units=6_000,
    )
    # Gercekte cok daha ucuza mal oldu (onbellek isabeti).
    store.record_api_usage("ali", "youtube_data_api", "search.list", 100)
    store.finalize_run(
        "r1",
        PlaylistResult(run_id="r1", topic="k", filters=FilterOptions(), subtopics=[], recommendations=[]),
    )

    assert store.create_run_within_daily_limit(
        "r2", "k", {}, user_id="veli", max_per_day=0,
        max_units_per_day=10_000, estimated_units=6_000,
    ), "biten calistirmanin rezervasyonu birakilmali"


def test_an_interrupted_run_releases_its_reservation(tmp_path):
    """Yarida kalan calistirma butceyi SONSUZA KADAR tutmamali."""
    store = SQLiteStore(str(tmp_path / "app.db"), encryption_key=KEY)
    store.create_run_within_daily_limit(
        "r1", "k", {}, user_id="ali", max_per_day=0,
        max_units_per_day=10_000, estimated_units=9_000,
    )

    store.mark_interrupted_runs()

    assert store.create_run_within_daily_limit(
        "r2", "k", {}, user_id="veli", max_per_day=0,
        max_units_per_day=10_000, estimated_units=9_000,
    )


# ------------------------------------------------------------- tahmin/butce

def test_the_estimate_matches_what_a_run_actually_spends():
    """Olculdu: 6 alt konu, iki dilli -> 1224 birim."""
    assert estimate_run_units(6, 2) == 1_224
    assert estimate_run_units(6, 1) == 612


def test_the_budget_can_never_exceed_the_project_quota():
    """`MAX_UNITS_PER_DAY` kotadan buyuk verilirse kota kazanir."""
    assert ServerConfig(max_units_per_day=999_999).daily_unit_budget() == 10_000
    assert ServerConfig(max_units_per_day=3_000).daily_unit_budget() == 3_000
    assert ServerConfig(youtube_daily_quota_units=100_000).daily_unit_budget() == 100_000


def test_retry_after_points_at_the_real_reset_not_an_hour():
    """Kota Pasifik saatiyle donuyor; sabit 3600 kullaniciyi bosuna dondururdu."""
    saniye = seconds_until_next_quota_day()

    assert 0 < saniye <= 24 * 3600


# ------------------------------------------------------------------- uc uca

def _client(monkeypatch, tmp_path, **overrides):
    from api import deps
    from api.routers import runs as runs_router

    config = AppConfig(
        gemini_api_key="k",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "app.db"),
        secret_encryption_key=KEY,
        **overrides,
    )
    config.ensure_directories()
    monkeypatch.setattr(deps, "_base_config", lambda: config)
    monkeypatch.setattr(runs_router, "build_playlist", lambda *a, **k: None)

    from api.main import app

    return TestClient(app), SQLiteStore(config.sqlite_path, encryption_key=KEY)


def test_the_api_refuses_with_a_distinct_message_when_the_service_is_full(monkeypatch, tmp_path):
    client, store = _client(monkeypatch, tmp_path, max_units_per_day=2_000)
    with client:
        store.record_api_usage("local", "youtube_data_api", "search.list", 1_900)

        response = client.post("/api/runs", json={"topic": "kuantum"})

    assert response.status_code == 429
    assert "Servisin bugünkü kapasitesi doldu" in response.json()["detail"]
    assert int(response.headers["Retry-After"]) > 3600, "Pasifik sifirlanmasina gore olmali"


def test_capabilities_warns_before_the_user_even_submits(monkeypatch, tmp_path):
    """Kullanici formu doldurup gonderdikten SONRA ogrenmemeli."""
    client, store = _client(monkeypatch, tmp_path, max_units_per_day=2_000)
    with client:
        assert client.get("/api/config").json()["service_capacity_reached"] is False

        store.record_api_usage("local", "youtube_data_api", "search.list", 1_900)

        assert client.get("/api/config").json()["service_capacity_reached"] is True


def test_failed_run_releases_its_quota_reservation(tmp_path):
    """Basarisiz calistirma butceyi TUTMAMALI.

    `finalize_run` rezervasyonu yalnizca BASARI yolunda birakiyordu. Basarisiz
    bir calistirmada `result_json` NULL kaliyor ve satir "ucustaki rezervasyon"
    toplamina SURESIZ giriyordu -- gercek harcama sifir olsa bile. Olculdu:
    3.000 birimlik butcede ust uste basarisiz uc calistirma butcenin tamamini
    kilitliyor ve o gunku her istegi "servisin kapasitesi doldu" ile
    reddettiriyordu. Ancak surec yeniden baslayinca cozuluyordu.
    """
    store = SQLiteStore(str(tmp_path / "budget.db"))
    budget, estimate = 3000, 1000

    def admit(run_id: str):
        return store.create_run_within_daily_limit(
            run_id, "konu", {}, "user1",
            max_per_day=0, max_units_per_day=budget, estimated_units=estimate,
        )

    for run_id in ("run1", "run2", "run3"):
        assert admit(run_id).accepted
        store.release_run_reservation(run_id)  # is patladi

    with store.connect() as conn:
        reserved = conn.execute(
            "SELECT COALESCE(SUM(reserved_units), 0) AS t FROM run"
            " WHERE result_json IS NULL AND interrupted_at IS NULL"
        ).fetchone()["t"]

    assert reserved == 0
    # Hicbir birim GERCEKTEN harcanmadigi icin butce hala acik olmali.
    assert admit("run4").accepted


def test_releasing_a_reservation_does_not_invent_a_result(tmp_path):
    """Rezervasyonu birakmak, calistirmayi "tamamlanmis" gostermemeli."""
    store = SQLiteStore(str(tmp_path / "budget.db"))
    store.create_run_within_daily_limit(
        "run1", "konu", {}, "user1",
        max_per_day=0, max_units_per_day=3000, estimated_units=1000,
    )

    store.release_run_reservation("run1")

    with store.connect() as conn:
        row = conn.execute(
            "SELECT result_json, reserved_units FROM run WHERE run_id = ?", ("run1",)
        ).fetchone()
    assert row["result_json"] is None
    assert row["reserved_units"] == 0
