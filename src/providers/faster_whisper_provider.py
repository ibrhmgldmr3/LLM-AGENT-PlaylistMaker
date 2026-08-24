from __future__ import annotations

import importlib.util
import os
import threading
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
            # Work around duplicate Intel OpenMP DLL loads seen in mixed TensorFlow/ASR Conda environments.
            os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

        model = self._load_model()
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
        segments = _to_segments(raw_segments)
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


def _to_segments(raw_segments) -> list[TranscriptSegment]:
    """faster-whisper segmentlerini alan modeline cevirir."""
    segments: list[TranscriptSegment] = []
    for segment in raw_segments:
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
