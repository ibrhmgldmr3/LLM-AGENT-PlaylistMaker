"""YouTube altyazi bicimlerinin ayristiricilari.

Bu ayristiricilar "altyazili bir videoyu bos saymayalim" diye eklendi; sessizce
bos donen ya da patlayan bir ayristirici tam olarak onlemek istedigi seyi
yapar. Uc gercek hata bu dosyada kilitleniyor:

  1. `srv3` dali aslinda `srv1` semasini ayristiriyordu -- gercek srv3
     belgesinde hicbir dugum eslesmiyor, sonuc sessizce bos donuyordu.
  2. `json3` dali kok sozluk olmayan bir govdede `AttributeError` ile
     PATLIYORDU; "bicimi taniyamadim" durumu saglayici arizasi gibi gorunup
     hata sayacini artiriyordu.
  3. faster-whisper butcesi `transcribe()` cagrisindan SONRA basliyordu, oysa
     o cagri ureteci dondurmeden once sesi cozup VAD calistiriyor.
"""

from __future__ import annotations

import pytest

from src.config import AppConfig
from src.providers import faster_whisper_provider, ytdlp_provider
from src.providers.faster_whisper_provider import FasterWhisperProvider


# --------------------------------------------------------------------- srv3/srv1

# Gercek srv3: zaman MILISANIYE, cue kelime kelime `<s>` cocuklarina bolunmus.
SRV3 = (
    '<timedtext format="3"><body>'
    '<p t="1000" d="3000"><s>merhaba</s><s> dunya</s></p>'
    '<p t="9000" d="2000"></p>'
    '<p t="4000" d="2000"><s>ikinci cue</s></p>'
    "</body></timedtext>"
)

# srv1/srv2: zaman SANIYE, metin dogrudan dugumde.
SRV1 = '<transcript><text start="1.0" dur="3.0">merhaba dunya</text></transcript>'


def test_real_srv3_document_is_parsed():
    """Asil hata: bu belge sessizce bos donuyordu."""
    segments = ytdlp_provider._timedtext_xml_to_segments(SRV3)

    assert [s.text for s in segments] == ["merhaba dunya", "ikinci cue"]


def test_srv3_milliseconds_become_seconds():
    first = ytdlp_provider._timedtext_xml_to_segments(SRV3)[0]

    assert first.start_sec == 1.0
    assert first.end_sec == 4.0


def test_srv3_word_children_stay_in_one_segment():
    """Her `<s>` ayri segment olsaydi ayni cue yapay parcalara bolunurdu."""
    segments = ytdlp_provider._timedtext_xml_to_segments(SRV3)

    assert segments[0].text == "merhaba dunya"


def test_srv3_empty_cue_is_skipped():
    # srv3 zamanlama amacli bos `<p>` dugumleri de tasiyor.
    assert len(ytdlp_provider._timedtext_xml_to_segments(SRV3)) == 2


def test_srv1_schema_still_works():
    segments = ytdlp_provider._timedtext_xml_to_segments(SRV1)

    assert len(segments) == 1
    assert segments[0].start_sec == 1.0
    assert segments[0].end_sec == 4.0


def test_unparsable_xml_yields_nothing_instead_of_raising():
    assert ytdlp_provider._timedtext_xml_to_segments("<<<") == []


def test_node_without_timing_keeps_its_text():
    """Zamani okunamayan dugumun METNI atilmamali; kaybolan yalnizca konum."""
    document = "<transcript><text>zamansiz ama gercek metin</text></transcript>"
    segments = ytdlp_provider._timedtext_xml_to_segments(document)

    assert [s.text for s in segments] == ["zamansiz ama gercek metin"]
    assert segments[0].start_sec == 0.0
    assert segments[0].end_sec is None


# ------------------------------------------------------------------------ json3


@pytest.mark.parametrize(
    "payload",
    [
        "[1, 2]",  # kok bir DIZI -- eskiden AttributeError ile patliyordu
        "42",  # kok bir sayi
        '"metin"',  # kok bir dize
        "null",
        "{bozuk",  # hic JSON degil
        "",
    ],
)
def test_json3_never_raises_on_unexpected_payloads(payload):
    assert ytdlp_provider._json3_to_segments(payload) == []


def test_json3_valid_document_is_parsed():
    document = (
        '{"events":[{"tStartMs":1000,"dDurationMs":3000,'
        '"segs":[{"utf8":"merhaba"},{"utf8":" dunya"}]}]}'
    )
    segments = ytdlp_provider._json3_to_segments(document)

    assert len(segments) == 1
    assert segments[0].text == "merhaba dunya"
    assert segments[0].start_sec == 1.0
    assert segments[0].end_sec == 4.0


def test_json3_skips_events_with_malformed_segments():
    document = '{"events":[{"tStartMs":5000,"segs":"bir dize, liste degil"}]}'

    assert ytdlp_provider._json3_to_segments(document) == []


# -------------------------------------------------------------------- dagitici


@pytest.mark.parametrize("extension", ["srv3", "srv2", "srv1"])
def test_xml_formats_route_to_the_xml_parser(extension):
    assert ytdlp_provider._subtitle_to_segments(SRV1, extension)


def test_unknown_format_yields_nothing():
    """Taninmayan bicim, saglayici arizasi degil."""
    assert ytdlp_provider._subtitle_to_segments("herhangi bir icerik", "ttml") == []


# ------------------------------------------------------- faster-whisper butcesi


class _FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_budget_covers_the_eager_work_inside_transcribe(tmp_path, monkeypatch):
    """`transcribe()` ureteci dondurmeden ONCE sesi cozup VAD calistiriyor.

    Sayac o cagrinin ardindan baslatilirsa bu is butcenin disinda kaliyor --
    uzun bir dosyada tavanin asilmasinin en olasi yeri tam da orasi.
    """
    clock = _FakeClock()
    monkeypatch.setattr(faster_whisper_provider.time, "monotonic", clock)

    class EagerModel:
        def transcribe(self, audio_path, **kwargs):
            # Ses cozme + VAD: butceyi tek basina tuketiyor.
            clock.advance(200)
            return iter([_FakeSegment("butce zaten dolmus olmali")]), _FakeInfo()

    class _FakeSegment:
        def __init__(self, text):
            self.text = text
            self.start = 0.0
            self.end = 3.0

    class _FakeInfo:
        language = "tr"

    config = AppConfig(
        gemini_api_key="test",
        gemini_model="gemini-test",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "cache" / "app.db"),
        faster_whisper_timeout_sec=100,
    )
    monkeypatch.setattr(FasterWhisperProvider, "_load_model", lambda self: EagerModel())

    result = FasterWhisperProvider(config).transcribe("ses.wav", "abc123def45", "tr")

    assert result.status == "failed_temporary"
    assert "100" in (result.error or "")
