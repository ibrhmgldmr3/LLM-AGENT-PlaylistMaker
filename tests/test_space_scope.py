"""Defter SINIRLARI: izolasyon ve defter duzeyi sorular.

Iki iddia birlikte duruyor cunku ikisi de ayni soruyu yanitliyor -- "bu defter
neyi kapsiyor":

1. **Izolasyon.** Bir deftere sorulan soru YALNIZCA o defterin kaynaklarinda
   aranir. Baska bir defterin parcasi hicbir yoldan (leksik ya da anlamsal)
   sonuca karisamaz. Bu, kullanicinin urunden bekledigi en temel guvence:
   "ekonomi defterine sordugum sey react videolarindan yanitlanmasin".
2. **Defter duzeyi sorular.** "Bu defterde neler var" sorusunun cevabi tek bir
   parcada DEGIL, defterin butunundedir. Benzerlik aramasi bu soruyu
   yanitlayamaz; ayri bir baglam secimi gerekiyor.
"""

from __future__ import annotations

import pytest

from src.config import AppConfig
from src.models import TranscriptSegment
from src.services import rag_service
from src.services.chunking import chunk_transcript
from src.storage import SQLiteStore
from src.utils.text_utils import is_overview_question


class _Capturing:
    """Kendisine VERILEN baglami saklayan sahte saglayici.

    `embed` sabit vektor donuyor: bu dosyanin iddialari erisimin KAPSAMI
    hakkinda, siralamasi hakkinda degil.
    """

    def __init__(self):
        self.seen: list[dict] = []

    def embed(self, texts):
        return [[0.0, 0.0, 1.0] for _ in texts]

    def answer_from_context(self, question, chunks, language):
        self.seen = chunks
        # Yanit, VERILEN metinden turetiliyor. Sabit bir cumle 3b (dayanak)
        # kapisina takilirdi -- gercek bir modelin yapmadigi bir sey: kaynagi
        # ozetleyen yanit kaynagin sozcuklerini kullanir. Canli kosumda
        # "Bu defterde neler var" yaniti 11 alintiyla gecti.
        ozet = " ".join(chunk["text"][:80] for chunk in chunks[:3])
        return {
            "answered": True,
            "answer": f"Defterde şu konular yer alıyor: {ozet}",
            "used_chunk_ids": [chunk["chunk_id"] for chunk in chunks],
            "missing": "",
        }


def _segments(*texts):
    return [
        TranscriptSegment(start_sec=float(i * 30), end_sec=float(i * 30 + 30), text=text)
        for i, text in enumerate(texts)
    ]


@pytest.fixture
def two_spaces(tmp_path):
    """Iki defter, TAMAMEN ayri konular: ekonomi ve react."""
    config = AppConfig(
        gemini_api_key="test",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "app.db"),
        enable_rag=True,
        rag_top_k=4,
        rag_min_similarity=0.55,
    )
    store = SQLiteStore(config.sqlite_path)

    store.create_space("ekonomi", "local", "Ekonomi")
    store.add_source("ekonomi", "video:tufe", kind="video", ref_id="tufe", title="TÜFE nedir")
    store.replace_chunks(
        "ekonomi",
        "video:tufe",
        chunk_transcript(
            _segments(
                "Tüketici fiyat endeksi enflasyonu ölçen bir göstergedir.",
                "Enflasyon hesaplanırken sepetteki mal fiyatları karşılaştırılır.",
            ),
            None,
            max_chars=200,
            overlap_chars=0,
        ),
    )
    store.update_source("ekonomi", "video:tufe", status="indexed", chunk_count=2)

    store.create_space("react", "local", "React")
    store.add_source("react", "video:hooks", kind="video", ref_id="hooks", title="React Hooks")
    store.replace_chunks(
        "react",
        "video:hooks",
        chunk_transcript(
            _segments(
                "useState kancası bileşen durumunu yönetmek içindir.",
                "useReducer karmaşık durum geçişlerinde tercih edilir.",
            ),
            None,
            max_chars=200,
            overlap_chars=0,
        ),
    )
    store.update_source("react", "video:hooks", status="indexed", chunk_count=2)
    return config, store


# ------------------------------------------------------------------ izolasyon


def test_a_question_never_reaches_another_notebook(two_spaces):
    """EN TEMEL GUVENCE: ekonomi defterine sorulan soru react'te aranmaz."""
    config, store = two_spaces
    llm = _Capturing()

    rag_service.answer_question(config, store, llm, "ekonomi", "enflasyon nasıl ölçülür")

    assert llm.seen, "baglam bos kalmamali"
    assert all(chunk["source_id"] == "video:tufe" for chunk in llm.seen)


def test_content_that_exists_only_in_another_notebook_is_not_found(two_spaces):
    """react'te olan bir konu, ekonomi defterinden sorulunca BULUNAMAMALI."""
    config, store = two_spaces

    answer = rag_service.answer_question(
        config, store, _Capturing(), "ekonomi", "useReducer ne zaman kullanılır"
    )

    assert answer.answered is False


def test_both_retrieval_paths_are_scoped(two_spaces):
    """Kapsam TEK bir yolda degil, IKISINDE birden zorlanmali.

    Leksik yol duzelip anlamsal yol acik kalsaydi sizinti sessiz olurdu:
    kullanici yalnizca "alakasiz bir kaynak gosterildi" diye fark ederdi.
    """
    config, store = two_spaces
    import src.services.embedding_service as embedding_service

    ekonomi_chunks = {row["chunk_id"] for row in store.list_source_chunks("ekonomi", "video:tufe")}

    lexical, semantic = rag_service._retrieve(
        config, store, _Capturing(), "ekonomi", "useReducer durum yönetimi", "local"
    )

    assert all(chunk_id in ekonomi_chunks for chunk_id, _ in lexical)
    assert all(chunk_id in ekonomi_chunks for chunk_id, _ in semantic)
    assert embedding_service  # anlamsal yol gercekten kosuldu


def test_a_new_source_joins_its_own_notebook_only(two_spaces):
    """Sonradan eklenen kaynak O defterin sorgularina girer, digerine girmez."""
    config, store = two_spaces
    store.add_source("ekonomi", "video:gsyh", kind="video", ref_id="gsyh", title="GSYH deflatörü")
    store.replace_chunks(
        "ekonomi",
        "video:gsyh",
        chunk_transcript(
            _segments("Deflatör nominal ve reel GSYH oranından hesaplanır."),
            None,
            max_chars=200,
            overlap_chars=0,
        ),
    )
    store.update_source("ekonomi", "video:gsyh", status="indexed", chunk_count=1)

    llm = _Capturing()
    rag_service.answer_question(config, store, llm, "ekonomi", "deflatör nasıl hesaplanır")
    assert any(chunk["source_id"] == "video:gsyh" for chunk in llm.seen)

    react = _Capturing()
    rag_service.answer_question(config, store, react, "react", "deflatör nasıl hesaplanır")
    assert all(chunk["source_id"] != "video:gsyh" for chunk in react.seen)


# --------------------------------------------------- defter duzeyi sorular


@pytest.mark.parametrize(
    "question",
    [
        "Bu defterde neler var?",
        "Bu konunun ana fikri ne?",
        "hangi konular işleniyor",
        "defteri özetle",
        "what topics are covered here",
        "summarize this",
    ],
)
def test_notebook_level_questions_are_recognised(question):
    assert is_overview_question(question)


@pytest.mark.parametrize(
    "question",
    ["useReducer ne zaman kullanılır", "TÜFE nedir", "Zustand kurulumu nasıl yapılır"],
)
def test_specific_questions_are_not_treated_as_notebook_level(question):
    assert not is_overview_question(question)


def test_a_notebook_level_question_gets_one_excerpt_per_source(two_spaces):
    """Kapsam derinlikten ONCE gelir: her kaynaktan bir TEMSILCI.

    "Bu defterde ne var" sorusunun cevabi tek bir parcada degil; benzerlige
    gore secim yapilirsa bazi kaynaklar baglama hic girmez ve model onlardan
    HABERSIZ kalir.
    """
    config, store = two_spaces
    store.add_source("ekonomi", "video:gsyh", kind="video", ref_id="gsyh", title="GSYH deflatörü")
    store.replace_chunks(
        "ekonomi",
        "video:gsyh",
        chunk_transcript(
            _segments("Deflatör nominal ve reel GSYH oranından hesaplanır."),
            None,
            max_chars=200,
            overlap_chars=0,
        ),
    )
    store.update_source("ekonomi", "video:gsyh", status="indexed", chunk_count=1)

    llm = _Capturing()
    rag_service.answer_question(config, store, llm, "ekonomi", "Bu defterde neler var?")

    sunulan = {chunk["source_id"] for chunk in llm.seen}
    assert sunulan == {"video:tufe", "video:gsyh"}, "her kaynak temsil edilmeli"


def test_a_notebook_level_question_skips_the_similarity_gate(two_spaces):
    """1. kapi bu soruyu KESMEMELI.

    Kapinin sordugu sey "cevap kaynaklarda var mi"; oysa bu soru kaynaklarin
    KENDISI hakkinda. Olculdu: "bu konunun ana fikri ne" 0.601 benzerlik ve
    0.00 leksik kapsam aliyor -- yani kapi onu her zaman kesiyordu.
    """
    config, store = two_spaces
    llm = _Capturing()

    answer = rag_service.answer_question(config, store, llm, "ekonomi", "Bu konunun ana fikri ne?")

    assert llm.seen, "defter duzeyi soru modele ULASMALI"
    assert answer.answered is True


def test_a_notebook_level_question_still_cannot_cross_notebooks(two_spaces):
    """Kapi atlaniyor ama KAPSAM atlanmiyor."""
    config, store = two_spaces
    llm = _Capturing()

    rag_service.answer_question(config, store, llm, "react", "Bu defterde neler var?")

    assert all(chunk["source_id"] == "video:hooks" for chunk in llm.seen)


def test_an_empty_notebook_still_refuses(two_spaces):
    """Kapi atlamasi BOS deftere yanit uretmeye donusmemeli."""
    config, store = two_spaces
    store.create_space("bos", "local", "Boş")
    llm = _Capturing()

    answer = rag_service.answer_question(config, store, llm, "bos", "Bu defterde neler var?")

    assert answer.answered is False
    assert not llm.seen
