"""SQLite -> Postgres veri aktarimi (`python -m src.storage.transfer`).

Aktarim bir kez calisiyor ve geri alinamiyor: yanlis giden bir sey varsa
kullanicinin gecmisi, ogrenme alanlari ve OAuth jetonlari yeni veritabaninda
EKSIK oluyor. Bu yuzden testler "birkac satir geldi mi"ye degil, aktarimin
sessizce eksik birakabilecegi seylere bakiyor:

  * hicbir tablonun atlanmamasi
  * ikili verinin (vektorler) bozulmamasi
  * hedefteki otomatik id sayacinin ilerlemis olmasi
  * tam metin indeksinin ARAMA YAPABILIR halde gelmesi

Postgres yoksa tamami atlanir.
"""

from __future__ import annotations

import struct

import pytest

from src.services.chunking import chunk_document
from src.storage import SQLiteStore
from src.storage.dialect import PostgresDialect
from src.storage.transfer import logical_tables, transfer
from src.utils.text_utils import build_fts_query

KEY = "0" * 43 + "="


@pytest.fixture
def source(tmp_path) -> SQLiteStore:
    """Her tablosunda veri olan bir SQLite deposu."""
    store = SQLiteStore(str(tmp_path / "kaynak.db"), encryption_key=KEY)

    store.create_space("sp", "ali", "Kalman")
    store.add_source("sp", "doc:1", kind="document", ref_id="1", title="Ders notu")
    store.replace_chunks(
        "sp",
        "doc:1",
        chunk_document(
            [(1, "Kalman filtresi durum kestirimi yapar."), (2, "Kovaryans kuculur.")],
            max_chars=60,
            overlap_chars=0,
        ),
    )
    chunk_id = store.chunks_missing_embeddings("sp", "m")[0][0]
    store.put_embeddings([(chunk_id, "m", 3, struct.pack("<3f", 1.0, 0.5, 0.25))])

    store.create_run("r1", "Kalman filtresi", {}, user_id="ali")
    store.record_api_usage("ali", "youtube_data_api", "search", units=100)
    store.save_oauth_token("ali", "youtube", '{"access_token": "gizli"}')
    return store


@pytest.fixture
def target(source, postgres_dsn, tmp_path) -> SQLiteStore:
    return SQLiteStore(
        str(tmp_path / "kaynak.db"),
        encryption_key=KEY,
        dialect=PostgresDialect(postgres_dsn),
    )


# ----------------------------------------------------------- tablo secimi


def test_shadow_tables_are_not_transferred(source):
    """FTS5 kendi ic tablolarini da yaratiyor; hedefte karsiliklari yok.

    Kopyalamaya calismak "no such table" ile patlardi -- ve bu, aktarimin
    ORTASINDA patlamak demek.
    """
    with source.connect() as conn:
        tables = logical_tables(conn)

    assert "chunk_fts" in tables
    assert not [name for name in tables if name.startswith("chunk_fts_")]


def test_every_table_in_the_schema_is_transferred(source, target):
    """Elle yazilmis bir liste yeni tabloda sessizce eksik kalirdi.

    Bu test aktarilan tablo kumesini SEMANIN kendisiyle karsilastiriyor: yeni
    bir tablo eklendiginde ya aktarilir ya da bu test duser.
    """
    with source.connect() as conn:
        expected = set(logical_tables(conn))

    moved = transfer(source, target)

    assert set(moved) == expected


# --------------------------------------------------------------- veri


def test_rows_arrive_with_their_values_intact(source, target):
    transfer(source, target)

    assert target.get_space("sp", "ali") is not None
    assert target.get_space("sp", "veli") is None, "sahiplik korunmali"
    assert target.count_chunks("sp") == source.count_chunks("sp")
    assert [s["source_id"] for s in target.list_sources("sp")] == ["doc:1"]
    assert target.get_run_summary("r1")["topic"] == "Kalman filtresi"


def test_binary_vectors_survive_the_transfer(source, target):
    """BLOB -> BYTEA. Bozulursa kosinus benzerligi SESSIZCE anlamsizlasir."""
    before = source.load_embeddings("sp", "m")

    transfer(source, target)

    after = target.load_embeddings("sp", "m")
    assert after == before
    assert struct.unpack("<3f", bytes(after[0][1])) == (1.0, 0.5, 0.25)


def test_encrypted_secrets_are_copied_without_being_decrypted(source, target):
    """AYNI anahtarla okunabilmeli; aktarim sirri cozup yeniden sifrelememeli."""
    transfer(source, target)

    assert target.get_oauth_token("ali", "youtube") == '{"access_token": "gizli"}'


def test_the_search_index_is_usable_after_the_transfer(source, target):
    """`chunk_fts` iki veritabaninda BASKA seyler: FTS5 sanal tablosu ve
    `tsvector`li gercek tablo. Satirlari kopyalamak yetmiyorsa arama, veri
    yerinde dururken bos doner -- ve bu, RAG'in cekimserlik kapisi.
    """
    transfer(source, target)

    hits = target.search_chunks_fts("sp", build_fts_query("kestirim"))

    assert hits, "aktarimdan sonra leksik arama calismali"
    assert target.get_chunks([hits[0][0]])[0]["space_id"] == "sp"


def test_the_id_counter_continues_after_the_transfer(source, target):
    """Aktarimin en kolay gozden kacan adimi.

    `chunk_id` hedefte `BIGSERIAL` ve satirlar ACIK id ile yaziliyor: sayac
    ilerletilmezse veri dogru GORUNUYOR, uygulama ilk yazmada "duplicate key"
    ile kiriliyor.
    """
    transfer(source, target)
    before = target.count_chunks("sp")

    target.add_source("sp", "doc:2", kind="document", ref_id="2", title="Ikinci")
    written = target.replace_chunks(
        "sp",
        "doc:2",
        chunk_document([(1, "Yeni bir parca.")], max_chars=400, overlap_chars=0),
    )

    assert written == 1
    assert target.count_chunks("sp") == before + 1


# ------------------------------------------------------------- guvenlik


def test_a_non_empty_target_is_refused(source, target):
    """Yarim dolu bir hedefe eklemek iki veritabanin KARISIMINI birakirdi."""
    transfer(source, target)

    with pytest.raises(ValueError, match="bos degil"):
        transfer(source, target)


def test_truncate_makes_the_transfer_repeatable(source, target):
    transfer(source, target)

    moved = transfer(source, target, truncate=True)

    assert moved["chunk"] == source.count_chunks("sp")
    assert target.count_chunks("sp") == source.count_chunks("sp")


def test_dry_run_writes_nothing(source, target):
    plan = transfer(source, target, dry_run=True)

    assert plan["chunk"] == source.count_chunks("sp")
    assert target.count_chunks("sp") == 0
