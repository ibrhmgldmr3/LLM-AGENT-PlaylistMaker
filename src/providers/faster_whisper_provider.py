from __future__ import annotations

import os
from dataclasses import dataclass

from src.config import AppConfig
from src.models import TranscriptResult
from src.utils.text_utils import normalize_text


@dataclass
class FasterWhisperProvider:
    config: AppConfig
    name: str = "faster_whisper"

    def is_available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        return True

    def transcribe(self, audio_path: str, video_id: str) -> TranscriptResult:
        if os.name == "nt" and self.config.allow_unsafe_openmp_workaround:
            # Work around duplicate Intel OpenMP DLL loads seen in mixed TensorFlow/ASR Conda environments.
            os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

        from faster_whisper import WhisperModel

        model = WhisperModel(
            self.config.faster_whisper_model_size,
            device=self.config.faster_whisper_device,
            compute_type=self.config.faster_whisper_compute_type,
        )
        segments, info = model.transcribe(audio_path)
        text = normalize_text(" ".join(segment.text for segment in segments))
        if len(text) < 50:
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
