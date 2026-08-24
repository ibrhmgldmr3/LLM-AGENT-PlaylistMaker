"""Ogrenme alani deposu: sahiplik, arama indeksi ve silme kaskadi.

Uc sey kilitleniyor:

1. **Sahiplik.** Baska bir kullanicinin alani hicbir okuma/yazma yolundan
   gorunmemeli -- alanlar kullanicinin YUKLEDIGI dosyalari tasiyor.
2. **Arama indeksi senkron.** `chunk_fts` kendi kopyasini tutuyor; parcalar
   degistiginde onunla birlikte degismezse arama sessizce YANLIS sonuc verir
   (silinmis metni bulur, yeni metni bulmaz).
3. **Silme gercekten siliyor.** Alan silindiginde geride parca, vektor ya da
   FTS satiri kalmamali.
"""

from __future__ import annotations

import struct

from src.services.chunking import chunk_document
from src.storage import SQLiteStore
from src.utils.text_utils import build_fts_query


def _store(tmp_path) -> SQLiteStore:
    return SQLiteStore(str(tmp_path / "app.db"))


def _seed(store: SQLiteStore, space_id="sp", user_id="local", text="Bir metin."):
    store.create_space(space_id, user_id, "Alan")
    store.add_source(
        space_id,
        "doc:1",
        kind="document",
        ref_id="1",
        title="Ders notu",
        url=None,
        status="pending",
    )
    drafts = chunk_document([(1, text)], max_chars=400, overlap_chars=0)
    written = store.replace_chunks(space_id, "doc:1", drafts)
    store.update_source(space_id, "doc:1", status="indexed", chunk_count=written)
    return written


def _rowcount(store: SQLiteStore, table: str) -> int:
    with store.connect() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ------------------------------------------------------------------ sahiplik


def test_space_is_invisible_to_another_user(tmp_path):
    store = _store(tmp_path)
    store.create_space("sp", "ali", "Ali'nin alani")

    assert store.get_space("sp", "ali") is not None
    assert store.get_space("sp", "veli") is None
    assert store.list_spaces("veli") == []
    assert store.count_spaces("veli") == 0


def test_another_user_cannot_delete_the_space(tmp_path):
    store = _store(tmp_path)
    _seed(store, user_id="ali")

    assert store.delete_space("sp", "veli") is False
    # Icerik de duruyor: yetkisiz silme yariya kadar bile ilerlememeli.
    assert store.get_space("sp", "ali") is not None
    assert store.count_chunks("sp") == 1


def test_list_spaces_reports_source_and_chunk_counts(tmp_path):
    store = _store(tmp_path)
    _seed(store, text="Bir cumle. Iki cumle.")

    rows = store.list_spaces("local")

    assert len(rows) == 1
    assert rows[0]["source_count"] == 1
    assert rows[0]["chunk_count"] == store.count_chunks("sp")


# ------------------------------------------------------------- arama indeksi


def test_turkish_letters_match_across_normalisation(tmp_path):
    """`search_key`in tum varlik sebebi.

    FTS5'in `unicode61` tokenizer'i Turkce i/g/s harflerini indirgemedigi icin
    "olcum" yazan bir sorgu, metinde "olcum" gectigi halde eslesmiyordu. Iki
    taraf da AYNI donusumden gectigi surece eslesiyorlar.
    """
    store = _store(tmp_path)
    _seed(store, text="Ölçüm güncellemesi sırasında kovaryans küçülür.")

    for question in ("ölçüm", "olcum", "ÖLÇÜM", "kovaryans"):
        assert store.search_chunks_fts("sp", build_fts_query(question)), question


def test_turkish_suffix_is_matched_by_prefix_query(tmp_path):
    """Ekli bicimler leksik yolu sessizce devre disi birakiyordu.

    Metinde "guncellemesi" geciyor; kullanici "guncelleme" yaziyor. FTS5 tam
    esler, dolayisiyla onek (`*`) olmadan sonuc BOS donuyordu.
    """
    store = _store(tmp_path)
    _seed(store, text="Ölçüm güncellemesi kovaryans matrisini küçültür.")

    assert store.search_chunks_fts("sp", build_fts_query("güncelleme"))
    assert store.search_chunks_fts("sp", build_fts_query("matris"))


def test_unrelated_question_finds_nothing(tmp_path):
    """Kacinma (abstention) kapisinin dayandigi davranis."""
    store = _store(tmp_path)
    _seed(store, text="Ölçüm güncellemesi kovaryans matrisini küçültür.")

    assert store.search_chunks_fts("sp", build_fts_query("bugün hava nasıl")) == []


def test_search_is_scoped_to_one_space(tmp_path):
    store = _store(tmp_path)
    _seed(store, text="Kalman filtresi durum kestirimi yapar.")
    store.create_space("other", "local", "Digeri")
    store.add_source("other", "doc:1", kind="document", ref_id="1", title="X")
    store.replace_chunks(
        "other",
        "doc:1",
        chunk_document([(1, "Kalman filtresi burada da geciyor.")], max_chars=400, overlap_chars=0),
    )

    hits = store.search_chunks_fts("sp", build_fts_query("kalman"))

    assert len(hits) == 1
    found = store.get_chunks([hits[0][0]])[0]
    assert found["space_id"] == "sp"


def test_empty_query_returns_nothing(tmp_path):
    store = _store(tmp_path)
    _seed(store)

    assert store.search_chunks_fts("sp", "") == []
    assert store.search_chunks_fts("sp", "   ") == []


def test_malformed_fts_query_is_not_an_error(tmp_path):
    """Sorgu metnini kullanici yaziyor; bir soru yuzunden 500 donmek yanlis."""
    store = _store(tmp_path)
    _seed(store)

    assert store.search_chunks_fts("sp", 'dengesiz " tirnak') == []


def test_reindexing_replaces_chunks_and_keeps_fts_in_sync(tmp_path):
    store = _store(tmp_path)
    _seed(store, text="Eski metin kovaryans hakkinda.")

    store.replace_chunks(
        "sp",
        "doc:1",
        chunk_document([(1, "Yeni metin entropi hakkinda.")], max_chars=400, overlap_chars=0),
    )

    assert store.count_chunks("sp") == 1
    assert _rowcount(store, "chunk_fts") == 1
    # Eski metin ARTIK bulunmuyor, yenisi buluniyor.
    assert store.search_chunks_fts("sp", build_fts_query("kovaryans")) == []
    assert store.search_chunks_fts("sp", build_fts_query("entropi"))


def test_get_chunks_joins_source_metadata(tmp_path):
    store = _store(tmp_path)
    store.create_space("sp", "local", "Alan")
    store.add_source(
        "sp",
        "video:abc",
        kind="video",
        ref_id="abc",
        title="Kalman filtresi",
        url="https://www.youtube.com/watch?v=abc",
    )
    store.replace_chunks(
        "sp",
        "video:abc",
        chunk_document([(1, "Durum kestirimi.")], max_chars=400, overlap_chars=0),
    )

    hits = store.search_chunks_fts("sp", build_fts_query("kestirim"))
    chunk = store.get_chunks([hits[0][0]])[0]

    assert chunk["title"] == "Kalman filtresi"
    assert chunk["url"].endswith("v=abc")
    assert chunk["kind"] == "video"


# ------------------------------------------------------------------ vektorler


def _vector(values):
    return struct.pack(f"<{len(values)}f", *values)


def test_embeddings_are_scoped_by_model(tmp_path):
    """Farkli modellerin vektorleri arasinda kosinus anlamsizdir.

    Model degisince eski satirlar "gomulmemis" sayilmali ki tembel yeniden
    uretim devreye girsin.
    """
    store = _store(tmp_path)
    _seed(store)
    (chunk_id, _text) = store.chunks_missing_embeddings("sp", "model-a")[0]
    store.put_embeddings([(chunk_id, "model-a", 3, _vector([1.0, 0.0, 0.0]))])

    assert store.chunks_missing_embeddings("sp", "model-a") == []
    assert len(store.chunks_missing_embeddings("sp", "model-b")) == 1
    assert store.load_embeddings("sp", "model-a")
    assert store.load_embeddings("sp", "model-b") == []


# --------------------------------------------------------------------- silme


def test_deleting_space_removes_chunks_fts_and_vectors(tmp_path):
    store = _store(tmp_path)
    _seed(store)
    chunk_id = store.chunks_missing_embeddings("sp", "m")[0][0]
    store.put_embeddings([(chunk_id, "m", 3, _vector([1.0, 0.0, 0.0]))])

    assert store.delete_space("sp", "local") is True

    assert _rowcount(store, "chunk") == 0
    assert _rowcount(store, "chunk_fts") == 0
    assert _rowcount(store, "chunk_embedding") == 0
    assert _rowcount(store, "space_source") == 0
    assert store.get_space("sp", "local") is None


def test_deleting_one_source_leaves_the_others(tmp_path):
    store = _store(tmp_path)
    _seed(store, text="Birinci kaynak kovaryans.")
    store.add_source("sp", "doc:2", kind="document", ref_id="2", title="Ikinci")
    store.replace_chunks(
        "sp",
        "doc:2",
        chunk_document([(1, "Ikinci kaynak entropi.")], max_chars=400, overlap_chars=0),
    )

    assert store.delete_source("sp", "doc:1") is True

    assert store.count_chunks("sp") == 1
    assert _rowcount(store, "chunk_fts") == 1
    assert store.search_chunks_fts("sp", build_fts_query("kovaryans")) == []
    assert store.search_chunks_fts("sp", build_fts_query("entropi"))
    assert [s["source_id"] for s in store.list_sources("sp")] == ["doc:2"]


def test_deleting_unknown_source_reports_false(tmp_path):
    store = _store(tmp_path)
    _seed(store)

    assert store.delete_source("sp", "doc:yok") is False


def test_adding_the_same_source_twice_refreshes_instead_of_duplicating(tmp_path):
    """Ayni videoyu iceren ikinci bir calistirma alana eklenebilmeli."""
    store = _store(tmp_path)
    _seed(store)

    store.add_source(
        "sp", "doc:1", kind="document", ref_id="1", title="Guncellenmis baslik"
    )

    sources = store.list_sources("sp")
    assert len(sources) == 1
    assert sources[0]["title"] == "Guncellenmis baslik"
    assert sources[0]["status"] == "pending"
