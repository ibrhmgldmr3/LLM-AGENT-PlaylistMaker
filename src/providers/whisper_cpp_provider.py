from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from src.config import AppConfig
from src.models import TranscriptResult
from src.utils.text_utils import normalize_text


@dataclass
class WhisperCppProvider:
    config: AppConfig
    name: str = "whisper.cpp"

    def is_available(self) -> bool:
        return bool(
            self.config.whisper_cpp_cli_path
            and self.config.whisper_cpp_model_path
            and os.path.exists(self.config.whisper_cpp_cli_path)
            and os.path.exists(self.config.whisper_cpp_model_path)
        )

    def transcribe(self, audio_path: str, video_id: str) -> TranscriptResult:
        if not self.is_available():
            return TranscriptResult(
                video_id=video_id,
                status="failed_permanent",
                source="asr",
                backend=self.name,
                error="whisper.cpp is not configured",
            )
        output_base = os.path.splitext(audio_path)[0]
        cmd = [
            self.config.whisper_cpp_cli_path,
            "-m",
            self.config.whisper_cpp_model_path,
            "-f",
            audio_path,
            "-otxt",
            "-of",
            output_base,
        ]
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            return TranscriptResult(
                video_id=video_id,
                status="failed_temporary",
                source="asr",
                backend=self.name,
                error=completed.stderr.strip() or "whisper.cpp failed",
            )
        text_path = f"{output_base}.txt"
        if not os.path.exists(text_path):
            return TranscriptResult(
                video_id=video_id,
                status="failed_temporary",
                source="asr",
                backend=self.name,
                error="whisper.cpp did not produce a text file",
            )
        with open(text_path, "r", encoding="utf-8") as handle:
            text = normalize_text(handle.read())
        if len(text) < 50:
            return TranscriptResult(
                video_id=video_id,
                status="unavailable",
                source="asr",
                backend=self.name,
                error="ASR transcript too short",
            )
        return TranscriptResult(video_id=video_id, status="available", source="asr", backend=self.name, text=text)
