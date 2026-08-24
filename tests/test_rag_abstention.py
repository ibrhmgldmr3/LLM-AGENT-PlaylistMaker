"""Kacinma kapilari: "bulamadim" diyebilmek.

Ozelligin asil vaadi cevap uretmek DEGIL, cevabi olmayan soruya cevap
URETMEMEK. Bir dil modeli istendiginde her zaman inandirici bir sey yazabilir;
bu dosya uc bagimsiz kapinin da gercekten kapandigini kilitliyor.

En onemli iddia: alakasiz bir soruda **LLM hic cagrilmiyor**. Sahte
saglayicinin cagri sayaci bunu olcuyor -- "yanit bos dondu" ile "hic sorulmadi"
disaridan ayni gorunur ama ikincisi hem ucretsiz hem de modelin ikna
kabiliyetinden bagimsizdir.
"""

from __future__ import annotations

import pytest

from src.config import AppConfig
from src.models import TranscriptSegment
from src.services import rag_service
from src.services.chunking import chunk_transcript
from src.storage import SQLiteStore


SEGMENTS = [
    TranscriptSegment(
        start_sec=0.0, end_sec=30.0, text="Kalman filtresi bir durum kestirimi yöntemidir."
    ),
    TranscriptSegment(
        start_sec=30.0, end_sec=60.0, text="Ölçüm güncellemesi kovaryans matrisini küçültür."
    ),
    TranscriptSegment(
        start_sec=754.0,
        end_sec=800.0,
        text="Kokusuz Kalman filtresi doğrusal olmayan sistemler içindir.",
    ),
]


class FakeLLM:
    """Sayac tutan sahte saglayici.

    `embed` her zaman AYNI diK vektoru donuyor: anlamsal benzerlik kasitli
    olarak dusuk kaliyor ki testler leksik yolu ve 1. kapiyi ayri ayri
    zorlayabilsin.
    """

    def __init__(self, answer=None):
        self.embed_calls = 0
        self.answer_calls = 0
        self._answer = answer

    def embed(self, texts):
        self.embed_calls += 1
        return [[0.0, 0.0, 1.0] for _ in texts]

    def answer_from_context(self, question, chunks, language):
        self.answer_calls += 1
        if self._answer is not None:
            return self._answer
        return {
            "answered": True,
            "answer": "Kovaryans küçülür.",
            "used_chunk_ids": [chunks[0]["chunk_id"]],
            "missing": "",
        }


@pytest.fixture
def space(tmp_path):
    config = AppConfig(
        gemini_api_key="test",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "app.db"),
        enable_rag=True,
        rag_top_k=3,
        rag_min_similarity=0.55,
    )
    store = SQLiteStore(config.sqlite_path)
    store.create_space("sp", "local", "Kalman")
    store.add_source(
        "sp",
        "video:abc",
        kind="video",
        ref_id="abc",
        title="Kalman filtresi",
        url="https://www.youtube.com/watch?v=abc",
    )
    drafts = chunk_transcript(SEGMENTS, None, max_chars=120, overlap_chars=0)
    store.replace_chunks("sp", "video:abc", drafts)
    store.update_source("sp", "video:abc", status="indexed", chunk_count=len(drafts))
    return config, store


# --------------------------------------------------------------- 1. KAPI


def test_unrelated_question_never_reaches_the_model(space):
    """En kritik iddia: kacinma LLM'e SORMADAN gerceklesiyor."""
    config, store = space
    llm = FakeLLM()

    answer = rag_service.answer_question(config, store, llm, "sp", "bugün hava nasıl olacak")

    assert answer.answered is False
    assert answer.answer is None
    assert answer.citations == []
    assert llm.answer_calls == 0


def test_abstention_reason_points_at_the_pool_not_the_question(space):
    """Kullanici eksik olanin kendi sorusu degil HAVUZU oldugunu gormeli."""
    config, store = space

    answer = rag_service.answer_question(config, store, FakeLLM(), "sp", "zebra göçü")

    assert answer.searched_sources == 1
    assert "kaynakta arandı" in answer.reason


def test_empty_space_answers_without_calling_the_model(space):
    config, store = space
    store.create_space("bos", "local", "Boş alan")
    llm = FakeLLM()

    answer = rag_service.answer_question(config, store, llm, "bos", "herhangi bir soru")

    assert answer.answered is False
    assert llm.answer_calls == 0
    assert llm.embed_calls == 0
    assert "henüz aranabilir içerik yok" in answer.reason


def test_blank_question_is_rejected_early(space):
    config, store = space
    llm = FakeLLM()

    answer = rag_service.answer_question(config, store, llm, "sp", "   ")

    assert answer.answered is False
    assert llm.answer_calls == 0


def test_relevant_question_does_reach_the_model(space):
    """Kapi 1 fazla siki olmamali: cevabi OLAN soru gecebilmeli."""
    config, store = space
    llm = FakeLLM()

    answer = rag_service.answer_question(config, store, llm, "sp", "ölçüm güncellemesi ne yapar")

    assert llm.answer_calls == 1
    assert answer.answered is True
    assert answer.answer == "Kovaryans küçülür."


# --------------------------------------------------------------- 3. KAPI


def test_citation_to_a_chunk_that_was_never_offered_is_rejected(space):
    """Model "cevapladim" deyip var olmayan kaynaga atif yaparsa yanit dusurulur.

    Sema bir alanin VARLIGINI zorlar, ICERIGININ dogrulugunu degil. Bu kapi
    olmadan uydurma bir yanit sessizce gecerdi.
    """
    config, store = space
    liar = FakeLLM(
        answer={
            "answered": True,
            "answer": "Videoda anlatildigina gore...",
            "used_chunk_ids": [999_999],
            "missing": "",
        }
    )

    answer = rag_service.answer_question(config, store, liar, "sp", "ölçüm güncellemesi")

    assert answer.answered is False
    assert answer.citations == []


def test_partially_invented_citations_are_filtered_but_answer_survives(space):
    """Gecerli EN AZ BIR atif varsa yanit korunuyor, uydurma olan atiliyor.

    Yaniti tumden dusurmek fazla katı olurdu: model gercekten kullandigi
    kaynaklarin yaninda fazladan bir numara uydurmus olabilir ve cevabin
    kendisi hala dayanakli.
    """
    config, store = space
    real_ids = [row["chunk_id"] for row in store.get_chunks(
        [chunk_id for chunk_id, _ in store.search_chunks_fts("sp", "olcum*", limit=5)]
    )]
    llm = FakeLLM(
        answer={
            "answered": True,
            "answer": "Kovaryans küçülür.",
            "used_chunk_ids": real_ids[:1] + [999_999],
            "missing": "",
        }
    )

    answer = rag_service.answer_question(config, store, llm, "sp", "ölçüm güncellemesi")

    assert answer.answered is True
    assert len(answer.citations) == 1


def test_model_saying_not_answered_is_respected(space):
    """Kapi 2: model kendisi "bulamadim" derse yanit uretilmiyor."""
    config, store = space
    llm = FakeLLM(
        answer={
            "answered": False,
            "answer": "",
            "used_chunk_ids": [],
            "missing": "Alıntılar bu konuyu kapsamıyor.",
        }
    )

    answer = rag_service.answer_question(config, store, llm, "sp", "ölçüm güncellemesi")

    assert answer.answered is False
    assert answer.reason == "Alıntılar bu konuyu kapsamıyor."


def test_answered_true_with_empty_text_is_downgraded(space):
    config, store = space
    llm = FakeLLM(
        answer={"answered": True, "answer": "", "used_chunk_ids": [1], "missing": ""}
    )

    answer = rag_service.answer_question(config, store, llm, "sp", "ölçüm güncellemesi")

    assert answer.answered is False


# ------------------------------------------------------------- dayaniklilik


def test_semantic_failure_falls_back_to_lexical_search(space):
    """Embedding saglayicisi duserse arama ZAYIFLAR, kaybolmaz."""
    config, store = space

    class BrokenEmbedding(FakeLLM):
        def embed(self, texts):
            raise RuntimeError("saglayici gecici olarak kapali")

    llm = BrokenEmbedding()
    answer = rag_service.answer_question(config, store, llm, "sp", "ölçüm güncellemesi")

    assert answer.answered is True
    assert llm.answer_calls == 1
