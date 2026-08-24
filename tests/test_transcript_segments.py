"""Zaman damgali transkript segmentleri.

RAG alintilarinin "su videonun 12:34 aninda" diyebilmesi icin metnin videodaki
YERI gerekiyor. Dort saglayici da bu bilgiyi zaten uretiyordu; eskiden
`" ".join(...)` sirasinda atiliyordu. Bu dosya dordunun de artik korudugunu ve
`text` alaninin davranisinin DEGISMEDIGINI kilitliyor.
"""

from __future__ import annotations

import json

from src.config import AppConfig
from src.models import TranscriptResult
from src.providers import faster_whisper_provider, whisper_cpp_provider, ytdlp_provider
from src.providers.youtube_transcript_api_provider import _to_segments as yt_to_segments
from src.storage import SQLiteStore


# ------------------------------------------------------ youtube_transcript_api


def test_youtube_snippets_keep_start_and_duration():
    segments = yt_to_segments(
        [("merhaba", 0.0, 2.5), ("dunya", 2.5, 3.0), ("  ", 6.0, 1.0)]
    )

    assert [s.start_sec for s in segments] == [0.0, 2.5]
    assert segments[0].end_sec == 2.5
    assert segments[1].end_sec == 5.5
    # Bos metin segment uretmiyor.
    assert len(segments) == 2


def test_youtube_snippet_with_unreadable_time_keeps_text():
    """Zamani okunamayan satirin METNI atilmiyor.

    Metin transkriptin gercek bir parcasi; onu dusurmek arama havuzundan
    icerik eksiltmek olurdu. Yanlis olan yalnizca konum.
    """
    segments = yt_to_segments([("onemli cumle", None, None)])

    assert len(segments) == 1
    assert segments[0].text == "onemli cumle"
    assert segments[0].start_sec == 0.0
    assert segments[0].end_sec is None


# ------------------------------------------------------------------------ VTT


VTT = """WEBVTT
Kind: captions
Language: tr

00:00:01.000 --> 00:00:04.000
kalman filtresi

00:00:04.000 --> 00:00:07.500
kalman filtresi
sensor fuzyonu

00:01:02.250 --> 00:01:05.000
kovaryans matrisi
"""


def test_vtt_segments_carry_timestamps():
    segments = ytdlp_provider._vtt_to_segments(VTT)

    assert [s.start_sec for s in segments] == [1.0, 4.0, 62.25]
    assert segments[0].end_sec == 4.0


def test_vtt_rolling_window_duplicate_is_dropped_at_first_occurrence():
    """Otomatik altyazilarin kayan pencere tekrari elenmeye devam ediyor.

    Tekrar eden "kalman filtresi" ikinci cue'da DUSUYOR; ayni cue'nun yeni
    satiri ("sensor fuzyonu") kaliyor ve o cue'nun zamanini aliyor.
    """
    segments = ytdlp_provider._vtt_to_segments(VTT)

    assert segments[0].text == "kalman filtresi"
    assert segments[1].text == "sensor fuzyonu"
    assert ytdlp_provider._vtt_to_text(VTT) == (
        "kalman filtresi sensor fuzyonu kovaryans matrisi"
    )


def test_vtt_text_is_derived_from_segments():
    """`text` ayri bir gecisten DEGIL, segmentlerden turetiliyor."""
    text = ytdlp_provider._vtt_to_text(VTT)
    joined = " ".join(s.text for s in ytdlp_provider._vtt_to_segments(VTT))

    assert text == joined


def test_vtt_accepts_short_and_comma_timestamps():
    vtt = "WEBVTT\n\n01:02,500 --> 01:04,000\nkisa bicim\n"
    segments = ytdlp_provider._vtt_to_segments(vtt)

    assert len(segments) == 1
    assert segments[0].start_sec == 62.5


# --------------------------------------------------------------- faster-whisper


class _FakeSegment:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


def test_faster_whisper_segments_are_converted():
    raw = [_FakeSegment(0.0, 3.2, " merhaba "), _FakeSegment(3.2, 6.0, "dunya")]

    segments = faster_whisper_provider._to_segments(raw)

    assert [(s.start_sec, s.end_sec, s.text) for s in segments] == [
        (0.0, 3.2, "merhaba"),
        (3.2, 6.0, "dunya"),
    ]


def test_faster_whisper_drops_impossible_end_time():
    segments = faster_whisper_provider._to_segments([_FakeSegment(10.0, 2.0, "ters")])

    assert segments[0].start_sec == 10.0
    assert segments[0].end_sec is None


# ------------------------------------------------------------------ whisper.cpp


def test_whisper_cpp_offsets_are_milliseconds():
    """`offsets` MILISANIYE: kaynakta `t0 * 10` ve t0 santisaniye."""
    payload = {
        "transcription": [
            {"offsets": {"from": 0, "to": 3200}, "text": " merhaba "},
            {"offsets": {"from": 3200, "to": 6000}, "text": "dunya"},
        ]
    }

    segments = whisper_cpp_provider._to_segments(payload)

    assert [(s.start_sec, s.end_sec) for s in segments] == [(0.0, 3.2), (3.2, 6.0)]


def test_whisper_cpp_unexpected_shape_yields_no_segments():
    """Bicim beklenmedikse bos doner; cagiran `-otxt` yedegine duser."""
    assert whisper_cpp_provider._to_segments({"segments": []}) == []
    assert whisper_cpp_provider._to_segments("bozuk") == []


def test_whisper_cpp_falls_back_to_text_when_json_unusable(tmp_path, monkeypatch):
    """JSON bozuksa transkript KAYBOLMUYOR, yalnizca zaman damgasi kayboluyor."""
    cli = tmp_path / "whisper-cli"
    model = tmp_path / "model.bin"
    cli.write_text("", encoding="utf-8")
    model.write_text("", encoding="utf-8")
    audio = tmp_path / "video.wav"
    audio.write_text("", encoding="utf-8")

    (tmp_path / "video.json").write_text("{bu gecerli JSON degil", encoding="utf-8")
    (tmp_path / "video.txt").write_text(
        "kalman filtresi sensor fuzyonu icin kullanilan bir durum kestirimi yontemidir",
        encoding="utf-8",
    )

    class _Completed:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(
        whisper_cpp_provider.subprocess, "run", lambda *a, **k: _Completed()
    )

    config = AppConfig(
        gemini_api_key="test",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "cache" / "app.db"),
        whisper_cpp_cli_path=str(cli),
        whisper_cpp_model_path=str(model),
    )
    result = whisper_cpp_provider.WhisperCppProvider(config).transcribe(
        str(audio), "abc123def45"
    )

    assert result.status == "available"
    assert "kalman filtresi" in result.text
    assert result.segments == []
    # Iki cikti dosyasi da temizlendi.
    assert not (tmp_path / "video.json").exists()
    assert not (tmp_path / "video.txt").exists()


def test_whisper_cpp_prefers_json_segments_over_text(tmp_path, monkeypatch):
    cli = tmp_path / "whisper-cli"
    model = tmp_path / "model.bin"
    cli.write_text("", encoding="utf-8")
    model.write_text("", encoding="utf-8")
    audio = tmp_path / "video.wav"
    audio.write_text("", encoding="utf-8")

    (tmp_path / "video.json").write_text(
        json.dumps(
            {
                "transcription": [
                    {
                        "offsets": {"from": 1000, "to": 5000},
                        "text": "kalman filtresi sensor fuzyonu icin kullanilan bir durum kestirimi yontemidir",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "video.txt").write_text("kullanilmamali", encoding="utf-8")

    class _Completed:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(
        whisper_cpp_provider.subprocess, "run", lambda *a, **k: _Completed()
    )

    config = AppConfig(
        gemini_api_key="test",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "cache" / "app.db"),
        whisper_cpp_cli_path=str(cli),
        whisper_cpp_model_path=str(model),
    )
    result = whisper_cpp_provider.WhisperCppProvider(config).transcribe(
        str(audio), "abc123def45"
    )

    assert result.status == "available"
    assert "kullanilmamali" not in result.text
    assert [s.start_sec for s in result.segments] == [1.0]


# ------------------------------------------------------- onbellek uyumlulugu


def test_legacy_cached_transcript_without_segments_still_loads(tmp_path):
    """`segments` alani EKLENMEDEN once yazilmis onbellek kaydi okunabilmeli.

    30 gunluk TTL'i yalnizca zaman damgasi ugruna gecersizlestirmek yuzlerce
    transkript cagrisi harcamak olurdu; eski kayitlar `[]` ile geliyor.
    """
    store = SQLiteStore(str(tmp_path / "app.db"))
    legacy = {
        "video_id": "abc123def45",
        "status": "available",
        "source": "youtube_transcript_api",
        "language": "tr",
        "text": "eski kayit",
        "backend": None,
        "attempted_providers": [],
        "error": None,
        "fetched_at": "2026-01-01T00:00:00+00:00",
    }
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO transcript_cache (video_id, provider, status, payload_json,"
            " expires_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "abc123def45",
                "youtube_transcript_api",
                "available",
                json.dumps(legacy),
                "2099-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )

    cached = store.get_transcript_cache("abc123def45", "youtube_transcript_api")

    assert isinstance(cached, TranscriptResult)
    assert cached.text == "eski kayit"
    assert cached.segments == []
