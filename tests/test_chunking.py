"""Parcalama kurallari.

Bu dosya `src/services/chunking.py` ust docstring'indeki IKI KURALI kilitliyor:

1. Zaman/sayfa UYDURULMAZ -- kaynak vermiyorsa `None` kalir.
2. Ardisik parcalar ortusur, boylece sinira denk gelen bir cevap ikiye
   bolunup iki parcada da yarim kalmaz.

Ayrica paketleyicinin ILERLEME garantisi: hicbir girdi sonsuz donguye ya da
sinirsiz parca sayisina yol acmamali.
"""

from __future__ import annotations

from src.models import TranscriptSegment
from src.services.chunking import chunk_document, chunk_transcript


def _segments(count: int, *, chars: int = 39, step: float = 5.0):
    return [
        TranscriptSegment(
            start_sec=index * step,
            end_sec=index * step + step,
            text=f"s{index:02d} " + "x" * (chars - 4),
        )
        for index in range(count)
    ]


# ------------------------------------------------------------- zaman damgasi


def test_transcript_chunks_carry_real_segment_times():
    chunks = chunk_transcript(_segments(9), None, max_chars=120, overlap_chars=30)

    assert len(chunks) > 1
    # Her parcanin baslangici GERCEK bir segment baslangici (5'in kati).
    for chunk in chunks:
        assert chunk.start_sec is not None
        assert chunk.start_sec % 5.0 == 0.0


def test_transcript_chunk_times_are_monotonic():
    chunks = chunk_transcript(_segments(20), None, max_chars=200, overlap_chars=40)

    starts = [chunk.start_sec for chunk in chunks]
    assert starts == sorted(starts)
    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))


def test_transcript_without_segments_has_no_invented_times():
    """Segment yoksa saniye UYDURULMUYOR.

    Eski onbellek kayitlari (`segments=[]`) tam olarak bu yoldan geciyor.
    Orantiyla tahmin edilen bir saniye alintiyi tiklayani alakasiz bir yere
    goturur; konumsuz alinti bundan iyidir.
    """
    text = " ".join(f"Bu {index}. cumledir ve biraz uzundur." for index in range(30))

    chunks = chunk_transcript([], text, max_chars=150, overlap_chars=30)

    assert len(chunks) > 1
    assert all(chunk.start_sec is None for chunk in chunks)
    assert all(chunk.end_sec is None for chunk in chunks)
    assert all(chunk.page is None for chunk in chunks)


def test_chunk_end_comes_from_last_segment():
    chunks = chunk_transcript(_segments(3), None, max_chars=1000, overlap_chars=0)

    assert len(chunks) == 1
    assert chunks[0].start_sec == 0.0
    assert chunks[0].end_sec == 15.0


# ------------------------------------------------------------------ ortusme


def test_consecutive_chunks_overlap():
    """Sinira denk gelen icerik iki parcada da yarim kalmamali."""
    segments = _segments(9)
    chunks = chunk_transcript(segments, None, max_chars=120, overlap_chars=30)

    # Bir parcanin son segmenti, bir sonrakinin ILK segmenti olarak tekrarlanir.
    for previous, following in zip(chunks, chunks[1:]):
        assert following.start_sec is not None and previous.end_sec is not None
        assert following.start_sec < previous.end_sec


def test_zero_overlap_produces_disjoint_chunks():
    chunks = chunk_transcript(_segments(9), None, max_chars=120, overlap_chars=0)

    for previous, following in zip(chunks, chunks[1:]):
        assert following.start_sec >= previous.end_sec


def test_no_chunk_exceeds_the_limit():
    chunks = chunk_transcript(_segments(40), None, max_chars=200, overlap_chars=50)

    assert all(len(chunk.text) <= 200 for chunk in chunks)


# ----------------------------------------------------------------- ilerleme


def test_single_unpunctuated_blob_is_hard_split():
    """Noktalamasiz otomatik altyazi tek "cumle" olarak geliyor.

    Zorla bolunmezse paketleyici onu hicbir zaman yerlestiremez ve donguye
    girer.
    """
    blob = " ".join("kelime" for _ in range(500))

    chunks = chunk_transcript([], blob, max_chars=100, overlap_chars=20)

    assert len(chunks) > 1
    assert all(len(chunk.text) <= 100 for chunk in chunks)


def test_word_longer_than_limit_is_split_by_characters():
    chunks = chunk_transcript([], "a" * 500, max_chars=100, overlap_chars=20)

    assert all(len(chunk.text) <= 100 for chunk in chunks)
    assert len(chunks) >= 5


def test_overlap_larger_than_chunk_is_clamped():
    """Ortusme parcadan buyuk olamaz; olsaydi ilerleme dururdu."""
    chunks = chunk_transcript(_segments(12), None, max_chars=100, overlap_chars=10_000)

    assert len(chunks) < 40  # sinirsiz uremiyor
    assert all(len(chunk.text) <= 100 for chunk in chunks)


def test_empty_input_produces_no_chunks():
    assert chunk_transcript([], "", max_chars=100, overlap_chars=20) == []
    assert chunk_transcript([], None, max_chars=100, overlap_chars=20) == []
    assert chunk_document([], max_chars=100, overlap_chars=20) == []


def test_last_chunk_is_not_a_pure_repeat():
    """Ortusme kuyrugu yeni icerik almadan kapaniyorsa o parca atilir."""
    chunks = chunk_transcript(_segments(4), None, max_chars=120, overlap_chars=30)

    texts = [chunk.text for chunk in chunks]
    for index, text in enumerate(texts):
        others = texts[:index] + texts[index + 1 :]
        assert not any(text in other for other in others)


# ------------------------------------------------------------------ dokuman


def test_document_chunk_reports_the_page_it_starts_on():
    pages = [
        (1, "Birinci sayfa cumlesi. Ikinci cumle burada."),
        (2, "Ucuncu cumle ikinci sayfada. Dorduncu cumle."),
        (3, "Besinci cumle ucuncu sayfada."),
    ]

    chunks = chunk_document(pages, max_chars=60, overlap_chars=0)

    assert [chunk.page for chunk in chunks] == sorted(chunk.page for chunk in chunks)
    assert chunks[0].page == 1
    assert chunks[-1].page == 3
    assert all(chunk.start_sec is None for chunk in chunks)


def test_document_chunk_may_span_pages():
    """Kisa sayfalar tek parcada birlesebilir; sayfa BASLANGICI gosterilir."""
    pages = [(1, "Kisa."), (2, "Yine kisa."), (3, "Bir tane daha.")]

    chunks = chunk_document(pages, max_chars=500, overlap_chars=0)

    assert len(chunks) == 1
    assert chunks[0].page == 1
    assert "Bir tane daha." in chunks[0].text


def test_chunk_never_exceeds_max_chars_after_an_overlap_carry():
    """Tavan, ortusme kuyrugu devredildikten SONRA da gecerli.

    `_tail_for_overlap` kuyrugun ILK parcasini boyuna bakmadan aliyor (aksi
    halde hic kuyruk kalmazdi). Kuyruk tek basina buyuk olunca, flush sonrasi
    eklenen parcayla birlikte tavan bir daha hic kontrol edilmiyordu.
    Olculdu: max_chars=200 iken 379 karakterlik parca (1.9x).
    """
    max_chars, overlap = 200, 80
    sentences = ["a" * 8 + ".", "b" * 98 + ".", "c" * 188 + ".", "d" * 188 + "."]

    chunks = chunk_document(
        [(1, " ".join(sentences))], max_chars=max_chars, overlap_chars=overlap
    )

    assert chunks
    assert [len(c.text) for c in chunks if len(c.text) > max_chars] == []


def test_max_chars_holds_across_many_shapes():
    """Tavan tek bir kurguda degil, GENEL olarak gecerli olmali."""
    import random

    random.seed(7)
    for _ in range(200):
        max_chars = random.randint(50, 400)
        overlap = random.randint(0, max_chars)
        sentences = [
            chr(97 + i % 26) * random.randint(1, max_chars + 120) + "."
            for i in range(random.randint(1, 25))
        ]
        chunks = chunk_document(
            [(1, " ".join(sentences))], max_chars=max_chars, overlap_chars=overlap
        )
        assert all(len(c.text) <= max_chars for c in chunks), (max_chars, overlap)


def test_oversized_boundary_does_not_lose_content():
    """Tavani korumak icin kuyrugu birakmak, METNI dusurmemeli."""
    max_chars, overlap = 200, 80
    sentences = ["a" * 8 + ".", "b" * 98 + ".", "c" * 188 + ".", "d" * 188 + "."]

    chunks = chunk_document(
        [(1, " ".join(sentences))], max_chars=max_chars, overlap_chars=overlap
    )

    joined = " ".join(c.text for c in chunks)
    for sentence in sentences:
        assert sentence.strip(".")[:20] in joined
