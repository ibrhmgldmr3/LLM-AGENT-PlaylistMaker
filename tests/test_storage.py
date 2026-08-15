from src.config import AppConfig
from src.models import FilterOptions, TranscriptResult, VideoCandidate
from src.storage import SQLiteStore


def _config(tmp_path):
    config = AppConfig(
        gemini_api_key="test",
        gemini_model="gemini-test",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "cache" / "app.db"),
    )
    config.ensure_directories()
    return config


def test_sqlite_search_cache_hit_and_miss(tmp_path):
    config = _config(tmp_path)
    store = SQLiteStore(config.sqlite_path)
    filters = FilterOptions(language="en")
    candidate = VideoCandidate(video_id="vid12345678", url="https://example.com", title="Test")

    store.put_search_cache("yt_dlp", "python", filters.model_dump(), [candidate], ttl_sec=60)
    cached = store.get_search_cache("yt_dlp", "python", filters.model_dump())
    assert cached is not None
    assert cached[0].video_id == "vid12345678"

    store.put_search_cache("yt_dlp", "expired", filters.model_dump(), [candidate], ttl_sec=0)
    assert store.get_search_cache("yt_dlp", "expired", filters.model_dump()) is None


def test_failure_counter_only_cools_down_after_threshold(tmp_path):
    store = SQLiteStore(_config(tmp_path).sqlite_path)

    count, cooled = store.record_provider_failure("prov", "err", cooldown_sec=900, threshold=3)
    assert (count, cooled) == (1, False)
    assert store.get_provider_cooldown("prov") is None

    count, cooled = store.record_provider_failure("prov", "err", cooldown_sec=900, threshold=3)
    assert (count, cooled) == (2, False)

    count, cooled = store.record_provider_failure("prov", "err", cooldown_sec=900, threshold=3)
    assert (count, cooled) == (3, True)
    assert store.get_provider_cooldown("prov") is not None


def test_success_resets_the_failure_counter(tmp_path):
    store = SQLiteStore(_config(tmp_path).sqlite_path)

    store.record_provider_failure("prov", "err", cooldown_sec=900, threshold=3)
    store.record_provider_failure("prov", "err", cooldown_sec=900, threshold=3)
    store.clear_provider_cooldown("prov")

    count, cooled = store.record_provider_failure("prov", "err", cooldown_sec=900, threshold=3)
    assert (count, cooled) == (1, False)


def test_purge_expired_removes_only_stale_rows(tmp_path):
    store = SQLiteStore(_config(tmp_path).sqlite_path)
    filters = FilterOptions(language="en")
    candidate = VideoCandidate(video_id="keepme12345", url="https://example.com", title="Keep")

    store.put_search_cache("p", "fresh", filters.model_dump(), [candidate], ttl_sec=600)
    store.put_search_cache("p", "stale", filters.model_dump(), [candidate], ttl_sec=0)
    store.put_transcript_cache(
        TranscriptResult(video_id="keepme12345", status="available", source="p", text="x" * 60), ttl_sec=600
    )

    removed = store.purge_expired()

    assert removed >= 1
    assert store.get_search_cache("p", "fresh", filters.model_dump()) is not None
    assert store.get_transcript_cache("keepme12345", "p") is not None


def test_expiry_comparison_is_not_lexicographic(tmp_path):
    """Regresyon: mikrosaniyeli/mikrosaniyesiz ISO karisimi sozluksel karsilastirmayi bozuyordu."""
    store = SQLiteStore(_config(tmp_path).sqlite_path)
    filters = FilterOptions(language="en")
    candidate = VideoCandidate(video_id="abcdefghijk", url="https://example.com", title="T")

    store.put_search_cache("p", "q", filters.model_dump(), [candidate], ttl_sec=3600)
    assert store.get_search_cache("p", "q", filters.model_dump()) is not None


def test_cache_key_changes_with_schema_version(monkeypatch, tmp_path):
    """Model alanlari degistiginde eski onbellek kayitlari servis edilmemeli."""
    from src.storage import sqlite_store

    filters = FilterOptions(language="en").model_dump()
    old_key = SQLiteStore.build_search_cache_key("p", "q", filters)
    monkeypatch.setattr(sqlite_store, "CACHE_SCHEMA_VERSION", 999)
    assert SQLiteStore.build_search_cache_key("p", "q", filters) != old_key


def test_stale_schema_entries_are_treated_as_a_miss(monkeypatch, tmp_path):
    from src.storage import sqlite_store

    store = SQLiteStore(_config(tmp_path).sqlite_path)
    filters = FilterOptions(language="en").model_dump()
    store.put_search_cache("p", "q", filters, [VideoCandidate(video_id="v1234567890", url="u", title="T")], 600)
    assert store.get_search_cache("p", "q", filters) is not None

    monkeypatch.setattr(sqlite_store, "CACHE_SCHEMA_VERSION", 999)
    assert store.get_search_cache("p", "q", filters) is None


def test_schema_has_no_orphan_metadata_table(tmp_path):
    """`video_metadata_cache` uretimde yalnizca YAZILIYOR, hic okunmuyordu.

    Ayni veriyi arama onbellegi zaten tasiyor; katman kaldirildi.
    """
    store = SQLiteStore(_config(tmp_path).sqlite_path)
    with store.connect() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "video_metadata_cache" not in tables
    assert {"search_cache", "transcript_cache", "provider_health", "run"} <= tables


def test_schema_setup_is_safe_from_several_threads_at_once(tmp_path):
    """Regresyon: `duplicate column name: failure_count`.

    `_migrate` "once bak, sonra ekle" yapiyordu. Semayi ayni anda kuran iki
    baglanti da sutunu eksik gorup ikisi de `ALTER` calistiriyor, ikincisi
    patliyordu. CI'da tam olarak boyle kirildi: bir istek parcacigi ile arka
    plandaki is parcacigi ayni anda store aciyordu. Windows'ta zamanlama denk
    gelmedigi icin yerelde hic gorulmedi.

    Ayni anlik acilis burada bir bariyerle ZORLANIYOR, uykuyla degil.
    """
    import sqlite3
    import threading

    db_path = tmp_path / "cache" / "app.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # ESKI semayla basla: `failure_count` ve `user_id` yok. Taze bir veritabaninda
    # `ALTER` yolu cogu parcacik icin hic calismaz ve yaris ortaya cikmaz --
    # goc tam da MEVCUT bir veritabani acilirken kosuyor, kirilma da oradaydi.
    legacy = sqlite3.connect(db_path)
    legacy.executescript(
        """
        CREATE TABLE provider_health (
            provider TEXT PRIMARY KEY,
            cooldown_until TEXT,
            last_error TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE run (
            run_id TEXT PRIMARY KEY,
            topic TEXT NOT NULL,
            filters_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            result_json TEXT
        );
        """
    )
    legacy.commit()
    legacy.close()

    db_path = str(db_path)
    workers = 8
    start = threading.Barrier(workers)
    errors: list[BaseException] = []

    def open_store():
        try:
            start.wait(timeout=10)
            SQLiteStore(db_path)
        except BaseException as exc:  # noqa: BLE001 - hepsi rapor edilmeli
            errors.append(exc)

    threads = [threading.Thread(target=open_store) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert not errors, f"es zamanli sema kurulumu patladi: {errors[:3]}"

    # Sema gercekten eksiksiz olmali; hatayi yutmak yeterli degil.
    store = SQLiteStore(db_path)
    with store.connect() as conn:
        health = {row["name"] for row in conn.execute("PRAGMA table_info(provider_health)")}
        run = {row["name"] for row in conn.execute("PRAGMA table_info(run)")}
    assert "failure_count" in health
    assert "user_id" in run


def test_column_migration_tolerates_a_concurrent_writer(tmp_path):
    """Regresyon: `duplicate column name: failure_count` (CI'da kirilan tam bu).

    `_add_column_if_missing` "once bak, sonra ekle" yapiyor. Iki baglanti ayni
    anda goc calistirdiginda ikisi de sutunu eksik gorup ikisi de `ALTER`
    deniyor; ikincisi patliyordu.

    Yaris parcaciklarla ZORLANAMIYOR -- pencere cok dar ve makineye gore
    kesismiyor (Windows'ta hic kesismedi, Linux'ta CI'da kesisti). Bu yuzden
    kosul dogrudan kuruluyor: PRAGMA kontrolu ile `ALTER` ARASINDA baska bir
    baglanti sutunu ekliyor. Testin gecmesi, hatanin "zaten uygulanmis" sinyali
    olarak dogru yorumlandigi anlamina geliyor.
    """
    import sqlite3

    db_path = tmp_path / "app.db"
    setup = sqlite3.connect(db_path)
    setup.execute("CREATE TABLE provider_health (provider TEXT PRIMARY KEY)")
    setup.commit()
    setup.close()

    ddl = "ALTER TABLE provider_health ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0"

    class RacingConnection:
        """PRAGMA'dan hemen sonra sutunu baska bir baglantiya ekleten sarmalayici."""

        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args):
            if sql.startswith("PRAGMA table_info"):
                rows = self._conn.execute(sql, *args).fetchall()
                other = sqlite3.connect(db_path)
                other.execute(ddl)
                other.commit()
                other.close()
                return rows
            return self._conn.execute(sql, *args)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        SQLiteStore._add_column_if_missing(
            RacingConnection(conn),
            "provider_health",
            "failure_count",
            "failure_count INTEGER NOT NULL DEFAULT 0",
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(provider_health)")}
    finally:
        conn.close()

    assert "failure_count" in columns
