"""`docker-compose.yml` gercekten COKLU SURECLI kurulumu tarif ediyor mu.

Compose dosyasi calisan bir belge: kimse onu okumadan `up` diyor. Icindeki bir
hata da calisma aninda "calisiyor gibi gorunen" bicimde ortaya cikiyor -- web
ayaga kalkar, arayuz acilir, ve sorun ancak bir dokuman yuklendiginde ya da
ikinci replika acildiginda anlasilir.

Buradaki testler yigini AYAGA KALDIRMIYOR (bunun icin Docker gerekirdi);
tarifin kendisiyle uygulamanin gerektirdikleri arasindaki baglari kontrol
ediyorlar.
"""

from __future__ import annotations

import pathlib

import pytest

yaml = pytest.importorskip("yaml")

COMPOSE = pathlib.Path(__file__).resolve().parent.parent / "docker-compose.yml"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _mounted_volumes(service: dict) -> set[str]:
    """Servisin bagladigi ADLI birimler (`ad:/yol` bicimindekiler)."""
    return {entry.split(":", 1)[0] for entry in service.get("volumes", [])}


def test_the_stack_has_all_four_roles(compose):
    services = compose["services"]

    assert {"web", "worker", "postgres", "redis"} <= set(services)


def test_the_worker_runs_the_worker_module(compose):
    """Ayni imaj, BASKA komut. Worker'i unutmak, islerin sessizce kuyrukta
    birikmesi demek: web 202 doner, hicbir sey ilerlemez."""
    assert compose["services"]["worker"]["command"] == [
        "python",
        "-m",
        "src.jobs.worker",
    ]


@pytest.mark.parametrize("service", ["web", "worker"])
def test_both_tiers_get_the_shared_backends(compose, service):
    """Paylasimli is kuyrugu ve depo ikisinde de tanimli olmali.

    Yalnizca birinde tanimli olmasi en sinsi hata: web Redis'e yazar, worker
    kendi BELLEGINE bakar ve kuyrukta bekleyen isi hic gormez.
    """
    env = compose["services"][service]["environment"]

    assert env["JOB_BACKEND"] == "redis"
    assert env["REDIS_URL"].startswith("redis://redis:")
    assert "@postgres:" in env["DATABASE_URL"]


def test_web_and_worker_share_one_data_volume(compose):
    """ASIL KIRILGAN NOKTA.

    Dokuman yukleme SENKRON: web dosyayi diske yaziyor ve indekslemeyi kuyruga
    atiyor, worker o dosyayi DISKTEN okuyor (bkz.
    `api/routers/spaces.add_document_source`). Ayri birimlerde worker "dosya
    yok" ile patlar -- ve bu, arka plan isinin icinde, kullaniciya "yukleme
    basarili" dendikten SONRA olur.
    """
    services = compose["services"]
    web = _mounted_volumes(services["web"])
    worker = _mounted_volumes(services["worker"])

    assert web & worker, "web ve worker en az bir birimi PAYLASMALI"
    assert "appdata" in web & worker


@pytest.mark.parametrize("service", ["web", "worker"])
def test_both_tiers_wait_for_their_backends_to_be_healthy(compose, service):
    """`depends_on` tek basina yetmiyor: kabin BASLAMASI hazir olmasi demek
    degil. Postgres ilk aciliste semayi kuruyor ve o sirada baglanti
    reddediyor."""
    depends = compose["services"][service]["depends_on"]

    assert depends["postgres"]["condition"] == "service_healthy"
    assert depends["redis"]["condition"] == "service_healthy"


def test_worker_disables_http_healthcheck(compose):
    """Dockerfile'daki `/api/health` kontrolu worker icin gecersiz (port dinlemiyor).
    Devre disi birakilmazsa worker konteyneri sonsuza kadar 'unhealthy' gorunur."""
    assert compose["services"]["worker"]["healthcheck"].get("disable") is True


def test_the_transfer_tool_does_not_run_on_every_boot(compose):
    """Aktarim BIR KERELIK. Her aciliste kosarsa ikinci kez dolu hedefi gorup
    hata verir ve yigin saglikli haldeyken kirmizi gorunur."""
    transfer = compose["services"]["transfer"]

    assert transfer.get("profiles"), "profil yoksa `up` ile birlikte baslar"


def test_postgres_and_redis_keep_their_data(compose):
    """Kuyruk ve veritabani yeniden baslatmaya dayanmali.

    Redis'te yalnizca gecici durum YOK: is kuyrugu ve SSE olay gunlugu de
    orada. Kaliciligi kapali birakmak, bir yeniden baslatmada kuyrukta
    bekleyen isleri sessizce yok ederdi.
    """
    services = compose["services"]

    assert _mounted_volumes(services["postgres"])
    assert _mounted_volumes(services["redis"])
    assert "--appendonly" in services["redis"]["command"]


def test_the_driver_for_postgres_is_declared():
    """Tarif Postgres'i sart kosuyor; imaj surucuyu TASIMALI.

    `psycopg2` yalnizca Postgres dalinda import ediliyor, yani eksikligi
    varsayilan kurulumda hic gorunmuyor -- ilk `DATABASE_URL` denemesinde
    `ModuleNotFoundError` olarak cikardi.
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")

    assert "psycopg2" in requirements
    assert "redis" in requirements
