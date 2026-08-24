from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass

from src.config import AppConfig
from src.models import TranscriptResult, TranscriptSegment
from src.utils.text_utils import MIN_TRANSCRIPT_CHARS, normalize_text


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

    def transcribe(self, audio_path: str, video_id: str, language: str | None = None) -> TranscriptResult:
        if not self.is_available():
            return TranscriptResult(
                video_id=video_id,
                status="failed_permanent",
                source="asr",
                backend=self.name,
                error="whisper.cpp is not configured",
            )
        # whisper.cpp 16 kHz WAV bekler; ses indirici artik bu formatta uretiyor.
        if not audio_path.lower().endswith(".wav"):
            return TranscriptResult(
                video_id=video_id,
                status="failed_permanent",
                source="asr",
                backend=self.name,
                error=f"whisper.cpp 16 kHz WAV bekler, alınan: {os.path.basename(audio_path)}",
            )

        output_base = os.path.splitext(audio_path)[0]
        cmd = [
            self.config.whisper_cpp_cli_path,
            "-m",
            self.config.whisper_cpp_model_path,
            "-f",
            audio_path,
            # JSON: RAG alintilari metnin videodaki YERINI gosterebilmeli.
            # Segment basina `offsets.from`/`.to` tasiyor (milisaniye: kaynakta
            # `t0 * 10`, t0 santisaniye). Duz metin bu bilgiyi hic tasimiyordu.
            "-oj",
            # Duz metin de YEDEK olarak isteniyor: JSON'un bicimi surumler
            # arasinda degisirse transkriptin TAMAMINI kaybetmek, yalnizca zaman
            # damgasini kaybetmekten cok daha agir olurdu. Ek maliyeti tek bir
            # dosya yazimi -- ayri bir calistirma DEGIL.
            #
            # `-nt` GEREKMIYOR: `output_txt` zaten zaman damgasi yazmiyor
            # (whisper.cpp/examples/cli/cli.cpp). Ustelik `-nt` kod cozucunun
            # `no_timestamps` parametresini de kuruyor ve JSON offsetlerini
            # etkileme riski tasiyor.
            "-otxt",
            "-of",
            output_base,
        ]
        if language:
            cmd.extend(["-l", language])

        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                # Timeout olmadan asili kalan bir surec Streamlit thread'ini sonsuza kadar blokluyordu.
                timeout=self.config.whisper_cpp_timeout_sec,
            )
        except subprocess.TimeoutExpired:
            return TranscriptResult(
                video_id=video_id,
                status="failed_temporary",
                source="asr",
                backend=self.name,
                error=f"whisper.cpp {self.config.whisper_cpp_timeout_sec} sn içinde tamamlanmadı",
            )
        except OSError as exc:
            return TranscriptResult(
                video_id=video_id,
                status="failed_permanent",
                source="asr",
                backend=self.name,
                error=f"whisper.cpp çalıştırılamadı: {exc}",
            )

        if completed.returncode != 0:
            return TranscriptResult(
                video_id=video_id,
                status="failed_temporary",
                source="asr",
                backend=self.name,
                error=(completed.stderr or "").strip()[:500] or "whisper.cpp failed",
            )

        json_path = f"{output_base}.json"
        text_path = f"{output_base}.txt"
        try:
            segments = _read_segments(json_path)
            # JSON'dan segment cikmadiysa (dosya yok, bozuk, ya da bicimi
            # degismis) duz metne DUS. Zaman damgasi kaybolur, transkript
            # kaybolmaz -- ikisi ayni agirlikta degil.
            if segments:
                text = normalize_text(" ".join(segment.text for segment in segments))
            else:
                text = _read_text(text_path)
        finally:
            for path in (json_path, text_path):
                try:
                    os.remove(path)
                except OSError:
                    pass

        if not text:
            return TranscriptResult(
                video_id=video_id,
                status="failed_temporary",
                source="asr",
                backend=self.name,
                error="whisper.cpp did not produce readable output",
            )

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
            text=text,
            segments=segments,
        )


def _read_segments(json_path: str) -> list[TranscriptSegment]:
    """`-oj` ciktisini okur; okunamazsa BOS doner (cagiran duz metne duser)."""
    try:
        with open(json_path, "r", encoding="utf-8") as handle:
            return _to_segments(json.load(handle))
    except (OSError, ValueError):
        return []


def _read_text(text_path: str) -> str:
    """`-otxt` yedegini okur. `output_txt` zaman damgasi yazmaz, kirpma gerekmez."""
    try:
        with open(text_path, "r", encoding="utf-8") as handle:
            return normalize_text(handle.read())
    except OSError:
        return ""


def _to_segments(payload: object) -> list[TranscriptSegment]:
    """whisper.cpp `-oj` ciktisini segmentlere cevirir.

    Bicim: `{"transcription": [{"timestamps": {...}, "offsets": {"from": ms,
    "to": ms}, "text": "..."}]}`. `offsets` MILISANIYE cinsinden; `timestamps`
    ise insan okunur dize ("00:00:01,000") ve ayristirmasi gereksiz -- ikisi
    ayni bilgi.

    Bicim beklenmedikse BOS doner ve cagiran `-otxt` yedegine duser: zaman
    damgasini kaybetmek kabul edilebilir, transkripti kaybetmek degil.
    """
    rows = payload.get("transcription") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []

    segments: list[TranscriptSegment] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        offsets = row.get("offsets") if isinstance(row.get("offsets"), dict) else {}
        start_sec = _ms_to_sec(offsets.get("from")) or 0.0
        end_sec = _ms_to_sec(offsets.get("to"))
        if end_sec is not None and end_sec < start_sec:
            end_sec = None
        segments.append(TranscriptSegment(start_sec=start_sec, end_sec=end_sec, text=text))
    return segments


def _ms_to_sec(value: object) -> float | None:
    try:
        return max(0.0, float(value) / 1000.0)
    except (TypeError, ValueError):
        return None
