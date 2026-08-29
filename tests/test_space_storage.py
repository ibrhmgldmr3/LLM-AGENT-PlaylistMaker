"""Ogrenme alani deposu: sahiplik, arama indeksi ve silme kaskadi.

Bu suit IKI lehceye karsi da kosuyor (`store` fixture'i, bkz.
`tests/conftest.py`): store'un veritabanina en cok bagimli yuzeyi burasi --
uretilen anahtar okumasi, tam metin indeksi ve silme kaskadi. Postgres
parametresi `MAP_TEST_POSTGRES_DSN` tanimli degilse ATLANIYOR.

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
    # Sutun ADIYLA okunuyor: Postgres tarafinda satirlar sozluk (`RealDictRow`)
    # ve konum indeksi (`[0]`) orada calismiyor.
    with store.connect() as conn:
        return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


# ------------------------------------------------------------------ sahiplik


def test_space_is_invisible_to_another_user(store):
    store.create_space("sp", "ali", "Ali'nin alani")

    assert store.get_space("sp", "ali") is not None
    assert store.get_space("sp", "veli") is None
    assert store.list_spaces("veli") == []
    assert store.count_spaces("veli") == 0


def test_another_user_cannot_delete_the_space(store):
    _seed(store, user_id="ali")

    assert store.delete_space("sp", "veli") is False
    # Icerik de duruyor: yetkisiz silme yariya kadar bile ilerlememeli.
    assert store.get_space("sp", "ali") is not None
    assert store.count_chunks("sp") == 1


def test_list_spaces_reports_source_and_chunk_counts(store):
    _seed(store, text="Bir cumle. Iki cumle.")

    rows = store.list_spaces("local")

    assert len(rows) == 1
    assert rows[0]["source_count"] == 1
    assert rows[0]["chunk_count"] == store.count_chunks("sp")


# ------------------------------------------------------------- arama indeksi


def test_turkish_letters_match_across_normalisation(store):
    """`search_key`in tum varlik sebebi.

    FTS5'in `unicode61` tokenizer'i Turkce i/g/s harflerini indirgemedigi icin
    "olcum" yazan bir sorgu, metinde "olcum" gectigi halde eslesmiyordu. Iki
    taraf da AYNI donusumden gectigi surece eslesiyorlar.
    """
    _seed(store, text="Ölçüm güncellemesi sırasında kovaryans küçülür.")

    for question in ("ölçüm", "olcum", "ÖLÇÜM", "kovaryans"):
        assert store.search_chunks_fts("sp", build_fts_query(question)), question


def test_turkish_suffix_is_matched_by_prefix_query(store):
    """Ekli bicimler leksik yolu sessizce devre disi birakiyordu.

    Metinde "guncellemesi" geciyor; kullanici "guncelleme" yaziyor. FTS5 tam
    esler, dolayisiyla onek (`*`) olmadan sonuc BOS donuyordu.
    """
    _seed(store, text="Ölçüm güncellemesi kovaryans matrisini küçültür.")

    assert store.search_chunks_fts("sp", build_fts_query("güncelleme"))
    assert store.search_chunks_fts("sp", build_fts_query("matris"))


def test_unrelated_question_finds_nothing(store):
    """Kacinma (abstention) kapisinin dayandigi davranis."""
    _seed(store, text="Ölçüm güncellemesi kovaryans matrisini küçültür.")

    assert store.search_chunks_fts("sp", build_fts_query("bugün hava nasıl")) == []


def test_search_is_scoped_to_one_space(store):
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


def test_best_match_comes_first_and_scores_descend(store):
    """SKOR SOZLESMESI: buyuk olan daha iyi, liste en iyiden kotuye.

    Iki lehcenin ham skoru bu sozu KENDILIGINDEN vermiyor -- FTS5 `bm25()`
    negatif ve daha negatif olani daha iyi, Postgres `ts_rank` pozitif ve daha
    buyugu daha iyi. Isaret karisikligi sessizce TERS siralama uretirdi.

    Siralama, skorun DEGERINDEN daha onemli: birlestirme (RRF) skorlari degil
    SIRALARI kullaniyor (bkz. `rag_service._reciprocal_rank_fusion`), yani
    leksik yolun davranisi tamamen bu siraya bagli.
    """
    store.create_space("sp", "local", "Alan")
    store.add_source("sp", "doc:1", kind="document", ref_id="1", title="Not")
    store.replace_chunks(
        "sp",
        "doc:1",
        chunk_document(
            [
                (1, "Kalman filtresi durum kestirimi yapar."),
                (2, "Filtre tasarimi ayri bir konudur."),
                (3, "Bugun hava yagmurlu gorunuyor."),
            ],
            max_chars=60,
            overlap_chars=0,
        ),
    )

    hits = store.search_chunks_fts("sp", build_fts_query("kalman filtre"))

    assert len(hits) == 2, hits  # ucuncu parca hicbir tokenla eslesmiyor
    scores = [score for _chunk_id, score in hits]
    assert scores == sorted(scores, reverse=True), scores
    # Iki tokenu da iceren parca once gelmeli.
    assert store.get_chunks([hits[0][0]])[0]["ordinal"] == 0


def test_empty_query_returns_nothing(store):
    _seed(store)

    assert store.search_chunks_fts("sp", "") == []
    assert store.search_chunks_fts("sp", "   ") == []


def test_malformed_fts_query_is_not_an_error(store):
    """Sorgu metnini kullanici yaziyor; bir soru yuzunden 500 donmek yanlis."""
    _seed(store)

    assert store.search_chunks_fts("sp", 'dengesiz " tirnak') == []


def test_reindexing_replaces_chunks_and_keeps_fts_in_sync(store):
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


def test_get_chunks_joins_source_metadata(store):
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


def test_embeddings_are_scoped_by_model(store):
    """Farkli modellerin vektorleri arasinda kosinus anlamsizdir.

    Model degisince eski satirlar "gomulmemis" sayilmali ki tembel yeniden
    uretim devreye girsin.
    """
    _seed(store)
    (chunk_id, _text) = store.chunks_missing_embeddings("sp", "model-a")[0]
    store.put_embeddings([(chunk_id, "model-a", 3, _vector([1.0, 0.0, 0.0]))])

    assert store.chunks_missing_embeddings("sp", "model-a") == []
    assert len(store.chunks_missing_embeddings("sp", "model-b")) == 1
    assert store.load_embeddings("sp", "model-a")
    assert store.load_embeddings("sp", "model-b") == []


# --------------------------------------------------------------------- silme


def test_deleting_space_removes_chunks_fts_and_vectors(store):
    _seed(store)
    chunk_id = store.chunks_missing_embeddings("sp", "m")[0][0]
    store.put_embeddings([(chunk_id, "m", 3, _vector([1.0, 0.0, 0.0]))])

    assert store.delete_space("sp", "local") is True

    assert _rowcount(store, "chunk") == 0
    assert _rowcount(store, "chunk_fts") == 0
    assert _rowcount(store, "chunk_embedding") == 0
    assert _rowcount(store, "space_source") == 0
    assert store.get_space("sp", "local") is None


def test_deleting_one_source_leaves_the_others(store):
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


def test_deleting_unknown_source_reports_false(store):
    _seed(store)

    assert store.delete_source("sp", "doc:yok") is False


def test_adding_the_same_source_twice_refreshes_instead_of_duplicating(store):
    """Ayni videoyu iceren ikinci bir calistirma alana eklenebilmeli."""
    _seed(store)

    store.add_source(
        "sp", "doc:1", kind="document", ref_id="1", title="Guncellenmis baslik"
    )

    sources = store.list_sources("sp")
    assert len(sources) == 1
    assert sources[0]["title"] == "Guncellenmis baslik"
    assert sources[0]["status"] == "pending"
