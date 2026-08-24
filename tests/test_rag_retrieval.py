"""Erisim: birlestirme, kaynak cesitliligi ve alinti bicimi."""

from __future__ import annotations

import pytest

from src.config import AppConfig
from src.models import TranscriptSegment
from src.services import rag_service
from src.services.chunking import chunk_document, chunk_transcript
from src.storage import SQLiteStore


def _config(tmp_path, **overrides):
    values = dict(
        gemini_api_key="test",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "app.db"),
        enable_rag=True,
        rag_top_k=4,
        rag_max_chunks_per_source=2,
    )
    values.update(overrides)
    return AppConfig(**values)


class _Capturing:
    """Kendisine VERILEN baglami saklayan sahte saglayici."""

    def __init__(self):
        self.seen: list[dict] = []

    def embed(self, texts):
        return [[0.0, 0.0, 1.0] for _ in texts]

    def answer_from_context(self, question, chunks, language):
        self.seen = chunks
        return {
            "answered": True,
            "answer": "Cevap.",
            "used_chunk_ids": [chunk["chunk_id"] for chunk in chunks],
            "missing": "",
        }


# ------------------------------------------------------------- birlestirme


def test_rrf_rewards_a_chunk_ranked_by_both_retrievers():
    """Iki listede de gecen aday, tek listede birinci olandan one gecebilir."""
    lexical = [(1, 5.0), (2, 4.0), (3, 3.0)]
    semantic = [(3, 0.9), (4, 0.8), (1, 0.7)]

    fused = rag_service._reciprocal_rank_fusion(lexical, semantic)
    order = [chunk_id for chunk_id, _score in fused]

    # 1: leksik 1. + anlamsal 3.  |  3: leksik 3. + anlamsal 1.  -> ikisi de
    # yalnizca tek listede gecen 2 ve 4'un onunde.
    assert order.index(1) < order.index(2)
    assert order.index(3) < order.index(4)


def test_rrf_is_deterministic_for_equal_scores():
    fused = rag_service._reciprocal_rank_fusion([(7, 1.0)], [(3, 1.0)])

    assert [chunk_id for chunk_id, _ in fused] == [3, 7]


def test_rrf_with_no_input_is_empty():
    assert rag_service._reciprocal_rank_fusion([], []) == []


# ------------------------------------------------------- kaynak cesitliligi


def test_one_source_cannot_fill_the_whole_context(tmp_path):
    """Tek bir uzun videonun baglami doldurmasi, daha iyi cevabi baska bir
    kaynakta olan sorularda o kaynagi hic gostermemek demek."""
    config = _config(tmp_path, rag_top_k=4, rag_max_chunks_per_source=2)
    store = SQLiteStore(config.sqlite_path)
    store.create_space("sp", "local", "Alan")

    for index in (1, 2):
        store.add_source(
            "sp", f"doc:{index}", kind="document", ref_id=str(index), title=f"Kaynak {index}"
        )
        pages = [(1, " ".join(f"kovaryans matrisi cümle {n}." for n in range(20)))]
        drafts = chunk_document(pages, max_chars=60, overlap_chars=0)
        store.replace_chunks("sp", f"doc:{index}", drafts)
        store.update_source("sp", f"doc:{index}", status="indexed", chunk_count=len(drafts))

    llm = _Capturing()
    rag_service.answer_question(config, store, llm, "sp", "kovaryans")

    per_source: dict[str, int] = {}
    for chunk in llm.seen:
        per_source[chunk["source_id"]] = per_source.get(chunk["source_id"], 0) + 1

    assert len(llm.seen) <= 4
    assert all(count <= 2 for count in per_source.values())
    assert len(per_source) == 2  # iki kaynak da temsil edildi


def test_context_respects_the_character_budget(tmp_path):
    config = _config(tmp_path, rag_top_k=10, rag_max_chunks_per_source=10, rag_context_char_limit=1000)
    store = SQLiteStore(config.sqlite_path)
    store.create_space("sp", "local", "Alan")
    store.add_source("sp", "doc:1", kind="document", ref_id="1", title="Uzun")
    pages = [(1, " ".join(f"kovaryans cümlesi numara {n}." for n in range(200)))]
    drafts = chunk_document(pages, max_chars=300, overlap_chars=0)
    store.replace_chunks("sp", "doc:1", drafts)
    store.update_source("sp", "doc:1", status="indexed", chunk_count=len(drafts))

    llm = _Capturing()
    rag_service.answer_question(config, store, llm, "sp", "kovaryans")

    assert sum(len(chunk["text"]) for chunk in llm.seen) <= 1000 + 300


# --------------------------------------------------------------- alintilar


@pytest.fixture
def video_space(tmp_path):
    config = _config(tmp_path)
    store = SQLiteStore(config.sqlite_path)
    store.create_space("sp", "local", "Alan")
    store.add_source(
        "sp",
        "video:abc",
        kind="video",
        ref_id="abc",
        title="Kalman filtresi",
        url="https://www.youtube.com/watch?v=abc",
    )
    segments = [
        TranscriptSegment(start_sec=754.0, end_sec=800.0, text="Kovaryans matrisi küçülür."),
    ]
    drafts = chunk_transcript(segments, None, max_chars=200, overlap_chars=0)
    store.replace_chunks("sp", "video:abc", drafts)
    store.update_source("sp", "video:abc", status="indexed", chunk_count=len(drafts))
    return config, store


def test_video_citation_links_to_the_exact_second(video_space):
    config, store = video_space

    answer = rag_service.answer_question(config, store, _Capturing(), "sp", "kovaryans")

    citation = answer.citations[0]
    assert citation.url == "https://www.youtube.com/watch?v=abc&t=754s"
    assert citation.start_sec == 754.0
    assert citation.quote


def test_citation_without_timestamp_keeps_the_plain_url(tmp_path):
    """Segment tasimayan eski bir transkriptte saniye UYDURULMUYOR."""
    config = _config(tmp_path)
    store = SQLiteStore(config.sqlite_path)
    store.create_space("sp", "local", "Alan")
    store.add_source(
        "sp",
        "video:abc",
        kind="video",
        ref_id="abc",
        title="Eski video",
        url="https://www.youtube.com/watch?v=abc",
    )
    drafts = chunk_transcript([], "Kovaryans matrisi küçülür.", max_chars=200, overlap_chars=0)
    store.replace_chunks("sp", "video:abc", drafts)
    store.update_source("sp", "video:abc", status="indexed", chunk_count=len(drafts))

    answer = rag_service.answer_question(config, store, _Capturing(), "sp", "kovaryans")

    citation = answer.citations[0]
    assert citation.url == "https://www.youtube.com/watch?v=abc"
    assert citation.start_sec is None


def test_document_citation_reports_the_page(tmp_path):
    config = _config(tmp_path)
    store = SQLiteStore(config.sqlite_path)
    store.create_space("sp", "local", "Alan")
    store.add_source("sp", "doc:1", kind="document", ref_id="1", title="Ders notu.pdf")
    drafts = chunk_document([(4, "Kovaryans matrisi küçülür.")], max_chars=200, overlap_chars=0)
    store.replace_chunks("sp", "doc:1", drafts)
    store.update_source("sp", "doc:1", status="indexed", chunk_count=len(drafts))

    answer = rag_service.answer_question(config, store, _Capturing(), "sp", "kovaryans")

    assert answer.citations[0].page == 4
    assert answer.citations[0].url is None


# ------------------------------------------------------------ konum etiketi


@pytest.mark.parametrize(
    "seconds, expected",
    [(0, "0:00"), (5, "0:05"), (65, "1:05"), (754, "12:34"), (3661, "1:01:01")],
)
def test_timestamp_formatting(seconds, expected):
    assert rag_service._format_timestamp(seconds) == expected


def test_url_without_query_uses_question_mark():
    assert rag_service._url_with_timestamp("https://youtu.be/abc", 12) == (
        "https://youtu.be/abc?t=12s"
    )
