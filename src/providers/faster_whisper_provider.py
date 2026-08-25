from __future__ import annotations

import importlib.util
import os
import threading
import time
from dataclasses import dataclass

from src.config import AppConfig
from src.models import TranscriptResult, TranscriptSegment
from src.utils.text_utils import MIN_TRANSCRIPT_CHARS, normalize_text

# Model yuklemesi saniyeler suruyor ve her video icin tekrarlanmamali.
_MODEL_CACHE: dict[tuple[str, str, str, int, int], object] = {}
_MODEL_LOCK = threading.Lock()


@dataclass
class FasterWhisperProvider:
    config: AppConfig
    name: str = "faster_whisper"

    def is_available(self) -> bool:
        # `find_spec` modulu CALISTIRMADAN varligini kontrol eder; import etmek
        # burada gereksiz (ve faster_whisper agir bir modul).
        return importlib.util.find_spec("faster_whisper") is not None

    def _load_model(self):
        cache_key = (
            self.config.faster_whisper_model_size,
            self.config.faster_whisper_device,
            self.config.faster_whisper_compute_type,
            self.config.faster_whisper_cpu_threads,
            self.config.faster_whisper_num_workers,
        )
        with _MODEL_LOCK:
            model = _MODEL_CACHE.get(cache_key)
            if model is None:
                from faster_whisper import WhisperModel

                model = WhisperModel(
                    cache_key[0],
                    device=cache_key[1],
                    compute_type=cache_key[2],
                    cpu_threads=cache_key[3],
                    num_workers=cache_key[4],
                )
                _MODEL_CACHE[cache_key] = model
            return model

    def transcribe(self, audio_path: str, video_id: str, language: str | None = None) -> TranscriptResult:
        if os.name == "nt" and self.config.allow_unsafe_openmp_workaround:
            # Karisik TensorFlow/ASR Conda ortamlarinda ayni Intel OpenMP DLL'i
            # iki kez yukleniyor ve surec cokuyor; bu bayrak onu tolere ettiriyor.
            os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

        model = self._load_model()

        # Butce `transcribe` CAGRISINDAN ONCE basliyor. Cagrinin adi yaniltici:
        # ureteci dondurmeden once sesi cozuyor ve VAD calistiriyor
        # (`decode_audio`, `get_speech_timestamps`), yani uzun bir dosyada
        # gercek bir sure buraya harcaniyor. Sayaci cagrinin ardindan
        # baslatmak bu isi butcenin DISINDA birakiyordu.
        #
        # Model yuklemesi bilerek disarida: onbellege alinmis ve videolar
        # arasinda paylasilan bir maliyeti tek bir videonun butcesine yazmak
        # yanlis olurdu; onu cagiran taraftaki pay karsiliyor
        # (`_ASR_TIMEOUT_GRACE_SEC`).
        timeout_sec = self.config.faster_whisper_timeout_sec
        deadline = time.monotonic() + timeout_sec if timeout_sec else None

        raw_segments, info = model.transcribe(
            audio_path,
            language=language,
            # Sessiz bolumleri atlamak calisma suresini belirgin sekilde kisaltir.
            vad_filter=True,
            # Zaman damgalari ARTIK GEREKLI: RAG alintilari "videonun su
            # saniyesinde" diyebilmeli. Eskiden `without_timestamps=True` idi
            # cunku yalnizca duz metin kullaniliyordu. Segment duzeyi zamanlar
            # bu bayraktan BAGIMSIZ olarak zaten uretiliyor; bayrak yalnizca
            # kod cozucunun zaman damgasi TOKENLARINI atlamasini sagliyordu.
            without_timestamps=False,
            beam_size=self.config.faster_whisper_beam_size,
            condition_on_previous_text=False,
        )
        # `transcribe` TEMBEL bir uretec doner; tek gecis icin listeye aliniyor.
        # Ayni ureteci hem metin hem segment icin iki kez dolasmak, ikincisinde
        # bos sonuc verirdi.
        #
        # Tembellik ayni zamanda zaman asiminin CALISMA SEBEBI: kod cozme isi
        # dongu donduginde yapiliyor, dolayisiyla her segmentte saate bakmak
        # gercekten isi kesiyor. whisper.cpp ayri bir surec oldugu icin
        # `subprocess timeout` ile sinirlanabiliyordu; faster-whisper surec
        # icinde calisiyor ve hicbir tavani yoktu -- yani `auto` modun
        # VARSAYILAN arka ucu sinirsizdi ve asili kalan tek bir is transkript
        # worker'ini suresiz blokluyordu.
        try:
            segments = _to_segments(raw_segments, deadline)
        except _TranscriptionTimeout:
            # Kismi cikti BILEREK atiliyor. Cagiran taraf "tamamlandi" ile
            # "yarida kesildi"yi `status` uzerinden ayirt edemez; yarim bir
            # transkriptin calisma notuna ya da RAG'a sessizce girmesi, hic
            # transkript olmamasindan daha yaniltici olurdu.
            return TranscriptResult(
                video_id=video_id,
                status="failed_temporary",
                source="asr",
                backend=self.name,
                error=f"faster-whisper {timeout_sec} sn içinde tamamlanmadı",
            )
        text = normalize_text(" ".join(segment.text for segment in segments))
        if len(text) < MIN_TRANSCRIPT_CHARS:
            return TranscriptResult(
                video_id=video_id,
                status="unavailable",
                source="asr",
                backend=self.name,
                error="ASR transcript too short",
            )
        return TranscriptResult(
            video_id=video_id,
            status="available",
            source="asr",
            backend=self.name,
            language=getattr(info, "language", None),
            text=text,
            segments=segments,
        )


class _TranscriptionTimeout(Exception):
    """Kod cozme butcesi doldu. Yalnizca bu modul icinde akis kontrolu."""


def _to_segments(raw_segments, deadline: float | None = None) -> list[TranscriptSegment]:
    """faster-whisper segmentlerini alan modeline cevirir.

    `deadline` (varsa) `time.monotonic()` olceginde bir an. Saate HER SEGMENTTE
    bakiliyor cunku `raw_segments` tembel bir uretec: kod cozme isi dongu
    donerken yapiliyor, dolayisiyla burada durmak gercekten isi kesiyor.
    Duvar saati (`time.time()`) DEGIL: sistem saatinin geri alinmasi butceyi
    sessizce uzatirdi.
    """
    segments: list[TranscriptSegment] = []
    for segment in raw_segments:
        if deadline is not None and time.monotonic() > deadline:
            raise _TranscriptionTimeout()
        text = (getattr(segment, "text", "") or "").strip()
        if not text:
            continue
        try:
            start_sec = max(0.0, float(getattr(segment, "start", 0.0) or 0.0))
        except (TypeError, ValueError):
            start_sec = 0.0
        try:
            end = getattr(segment, "end", None)
            end_sec = float(end) if end is not None else None
        except (TypeError, ValueError):
            end_sec = None
        if end_sec is not None and end_sec < start_sec:
            end_sec = None
        segments.append(TranscriptSegment(start_sec=start_sec, end_sec=end_sec, text=text))
    return segments
