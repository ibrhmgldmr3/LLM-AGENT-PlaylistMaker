import json
import os
from typing import Optional
from uuid import uuid4

from src.models import PlaylistResult, Transcript


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def generate_run_id() -> str:
    return uuid4().hex


def get_runs_dir(data_dir: str) -> str:
    return os.path.join(data_dir, "runs")


def get_cache_dir(data_dir: str) -> str:
    return os.path.join(data_dir, "cache")


def get_transcript_cache_dir(data_dir: str) -> str:
    return os.path.join(get_cache_dir(data_dir), "transcripts")


def get_run_dir(data_dir: str, run_id: str) -> str:
    run_dir = os.path.join(get_runs_dir(data_dir), run_id)
    ensure_dir(run_dir)
    return run_dir


def get_transcript_cache_path(data_dir: str, video_id: str) -> str:
    cache_dir = get_transcript_cache_dir(data_dir)
    ensure_dir(cache_dir)
    return os.path.join(cache_dir, f"{video_id}.json")


def load_transcript_cache(path: str) -> Optional[Transcript]:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return Transcript(**data)


def save_transcript_cache(path: str, transcript: Transcript) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(transcript.model_dump(), f, ensure_ascii=False, indent=2)


def save_playlist_result(run_dir: str, result: PlaylistResult) -> str:
    path = os.path.join(run_dir, "result.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result.model_dump(), f, ensure_ascii=False, indent=2)
    return path
