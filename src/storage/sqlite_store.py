from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.models import PlaylistResult, TranscriptResult, VideoCandidate


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_in(seconds: int) -> str:
    return (_utc_now() + timedelta(seconds=seconds)).isoformat()


class SQLiteStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS search_cache (
                    cache_key TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    query TEXT NOT NULL,
                    filters_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS video_metadata_cache (
                    video_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS transcript_cache (
                    video_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (video_id, provider)
                );
                CREATE TABLE IF NOT EXISTS provider_health (
                    provider TEXT PRIMARY KEY,
                    cooldown_until TEXT,
                    last_error TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS run (
                    run_id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    filters_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    result_json TEXT
                );
                CREATE TABLE IF NOT EXISTS run_subtopic (
                    run_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    subtopic_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, position)
                );
                CREATE TABLE IF NOT EXISTS run_video (
                    run_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    video_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, stage, video_id)
                );
                """
            )

    @staticmethod
    def build_search_cache_key(provider: str, query: str, filters: dict[str, Any]) -> str:
        digest = hashlib.sha256(
            json.dumps({"provider": provider, "query": query, "filters": filters}, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return digest

    def get_search_cache(self, provider: str, query: str, filters: dict[str, Any]) -> list[VideoCandidate] | None:
        cache_key = self.build_search_cache_key(provider, query, filters)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json, expires_at FROM search_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if not row or row["expires_at"] <= _utc_now().isoformat():
            return None
        return [VideoCandidate.model_validate(item) for item in json.loads(row["payload_json"])]

    def put_search_cache(
        self,
        provider: str,
        query: str,
        filters: dict[str, Any],
        candidates: list[VideoCandidate],
        ttl_sec: int,
    ) -> None:
        cache_key = self.build_search_cache_key(provider, query, filters)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO search_cache (
                    cache_key, provider, query, filters_json, payload_json, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cache_key,
                    provider,
                    query,
                    json.dumps(filters, ensure_ascii=False, sort_keys=True),
                    json.dumps([item.model_dump() for item in candidates], ensure_ascii=False),
                    _iso_in(ttl_sec),
                    _utc_now().isoformat(),
                ),
            )

    def upsert_video_metadata(self, candidate: VideoCandidate, ttl_sec: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO video_metadata_cache (video_id, payload_json, expires_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    candidate.video_id,
                    json.dumps(candidate.model_dump(), ensure_ascii=False),
                    _iso_in(ttl_sec),
                    _utc_now().isoformat(),
                ),
            )

    def get_video_metadata(self, video_id: str) -> VideoCandidate | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json, expires_at FROM video_metadata_cache WHERE video_id = ?",
                (video_id,),
            ).fetchone()
        if not row or row["expires_at"] <= _utc_now().isoformat():
            return None
        return VideoCandidate.model_validate(json.loads(row["payload_json"]))

    def get_transcript_cache(self, video_id: str, provider: str) -> TranscriptResult | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json, expires_at
                FROM transcript_cache
                WHERE video_id = ? AND provider = ?
                """,
                (video_id, provider),
            ).fetchone()
        if not row or row["expires_at"] <= _utc_now().isoformat():
            return None
        return TranscriptResult.model_validate(json.loads(row["payload_json"]))

    def put_transcript_cache(self, transcript: TranscriptResult, ttl_sec: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO transcript_cache (video_id, provider, status, payload_json, expires_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    transcript.video_id,
                    transcript.source,
                    transcript.status,
                    json.dumps(transcript.model_dump(), ensure_ascii=False),
                    _iso_in(ttl_sec),
                    _utc_now().isoformat(),
                ),
            )

    def get_provider_cooldown(self, provider: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT cooldown_until FROM provider_health WHERE provider = ?",
                (provider,),
            ).fetchone()
        if not row:
            return None
        cooldown_until = row["cooldown_until"]
        if cooldown_until and cooldown_until > _utc_now().isoformat():
            return cooldown_until
        return None

    def mark_provider_cooldown(self, provider: str, error: str, cooldown_sec: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO provider_health (provider, cooldown_until, last_error, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (provider, _iso_in(cooldown_sec), error, _utc_now().isoformat()),
            )

    def clear_provider_cooldown(self, provider: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO provider_health (provider, cooldown_until, last_error, updated_at)
                VALUES (?, NULL, NULL, ?)
                """,
                (provider, _utc_now().isoformat()),
            )

    def create_run(self, run_id: str, topic: str, filters: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO run (run_id, topic, filters_json, created_at, result_json)
                VALUES (?, ?, ?, ?, NULL)
                """,
                (run_id, topic, json.dumps(filters, ensure_ascii=False), _utc_now().isoformat()),
            )

    def add_run_subtopic(self, run_id: str, position: int, payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO run_subtopic (run_id, position, subtopic_json)
                VALUES (?, ?, ?)
                """,
                (run_id, position, json.dumps(payload, ensure_ascii=False)),
            )

    def add_run_video(self, run_id: str, stage: str, video_id: str, payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO run_video (run_id, stage, video_id, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, stage, video_id, json.dumps(payload, ensure_ascii=False)),
            )

    def finalize_run(self, run_id: str, result: PlaylistResult) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE run SET result_json = ? WHERE run_id = ?",
                (json.dumps(result.model_dump(), ensure_ascii=False), run_id),
            )
