from __future__ import annotations

import importlib.util
import os
import threading
from dataclasses import dataclass

from src.config import AppConfig
from src.models import TranscriptResult
from src.utils.text_utils import normalize_text


MIN_TRANSCRIPT_CHARS = 50

# Model yuklemesi saniyeler suruyor ve her video icin tekrarlanmamali.
_MODEL_CACHE: dict[tuple[str, str, str], object] = {}
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
        )
        with _MODEL_LOCK:
            model = _MODEL_CACHE.get(cache_key)
            if model is None:
                from faster_whisper import WhisperModel

                model = WhisperModel(
                    cache_key[0],
                    device=cache_key[1],
                    compute_type=cache_key[2],
                )
                _MODEL_CACHE[cache_key] = model
            return model

    def transcribe(self, audio_path: str, video_id: str, language: str | None = None) -> TranscriptResult:
        if os.name == "nt" and self.config.allow_unsafe_openmp_workaround:
            # Work around duplicate Intel OpenMP DLL loads seen in mixed TensorFlow/ASR Conda environments.
            os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

        model = self._load_model()
        segments, info = model.transcribe(
            audio_path,
            language=language,
            # Sessiz bolumleri atlamak calisma suresini belirgin sekilde kisaltir.
            vad_filter=True,
            # Yalnizca duz metne ihtiyacimiz var; zaman damgasi uretmeye gerek yok.
            without_timestamps=True,
            beam_size=self.config.faster_whisper_beam_size,
            condition_on_previous_text=False,
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
        )
