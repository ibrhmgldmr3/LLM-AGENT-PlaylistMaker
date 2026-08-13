from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.models import PlaylistResult, TranscriptResult, VideoCandidate


# Bir saglayicinin gecici olarak devre disi birakilmasi icin gereken ardisik hata sayisi.
# Tek bir videonun altyazisi yoksa saglayicinin tamami cezalandirilmamalidir.
DEFAULT_FAILURE_THRESHOLD = 3

_BUSY_TIMEOUT_SEC = 30.0

# `VideoCandidate` alanlari degistiginde arttirin. Onbellek anahtarina karistigi
# icin eski kayitlar otomatik olarak gecersizlesir; aksi halde TTL dolana kadar
# (saatler) eksik alanli adaylar servis edilir ve siralama sessizce bozulur.
CACHE_SCHEMA_VERSION = 2


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(value: datetime) -> str:
    # Sabit hassasiyet: mikrosaniyeli/mikrosaniyesiz karisimi sozluksel
    # karsilastirmayi bozuyordu. Artik karsilastirma Python tarafinda yapiliyor
    # ama yazim formatini yine de tekillestiriyoruz.
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _iso_in(seconds: int) -> str:
    return _to_iso(_utc_now() + timedelta(seconds=seconds))


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_expired(expires_at: str | None) -> bool:
    parsed = _parse_iso(expires_at)
    if parsed is None:
        return True
    return parsed <= _utc_now()


class SQLiteStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=_BUSY_TIMEOUT_SEC)
        conn.row_factory = sqlite3.Row
        try:
            # WAL + busy_timeout: Streamlit'te es zamanli oturumlarda
            # "database is locked" hatasini onler.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=%d" % int(_BUSY_TIMEOUT_SEC * 1000))
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
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
                CREATE INDEX IF NOT EXISTS idx_search_cache_expires ON search_cache (expires_at);
                CREATE INDEX IF NOT EXISTS idx_transcript_cache_expires ON transcript_cache (expires_at);
                """
            )
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(provider_health)")}
        if "failure_count" not in columns:
            conn.execute("ALTER TABLE provider_health ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0")

    @staticmethod
    def build_search_cache_key(provider: str, query: str, filters: dict[str, Any]) -> str:
        digest = hashlib.sha256(
            json.dumps(
                {
                    "v": CACHE_SCHEMA_VERSION,
                    "provider": provider,
                    "query": query,
                    "filters": filters,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return digest

    def get_search_cache(self, provider: str, query: str, filters: dict[str, Any]) -> list[VideoCandidate] | None:
        cache_key = self.build_search_cache_key(provider, query, filters)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json, expires_at FROM search_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if not row or _is_expired(row["expires_at"]):
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
                    _to_iso(_utc_now()),
                ),
            )

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
        if not row or _is_expired(row["expires_at"]):
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
                    _to_iso(_utc_now()),
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
        parsed = _parse_iso(cooldown_until)
        if parsed and parsed > _utc_now():
            return cooldown_until
        return None

    def mark_provider_cooldown(self, provider: str, error: str, cooldown_sec: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO provider_health (provider, cooldown_until, last_error, failure_count, updated_at)
                VALUES (?, ?, ?, COALESCE((SELECT failure_count FROM provider_health WHERE provider = ?), 0), ?)
                ON CONFLICT(provider) DO UPDATE SET
                    cooldown_until = excluded.cooldown_until,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                """,
                (provider, _iso_in(cooldown_sec), error, provider, _to_iso(_utc_now())),
            )

    def record_provider_failure(
        self,
        provider: str,
        error: str,
        cooldown_sec: int,
        threshold: int = DEFAULT_FAILURE_THRESHOLD,
    ) -> tuple[int, bool]:
        """Ardisik hata sayacini arttirir; esik asilirsa cooldown uygular.

        Tek bir videonun hatasi artik tum saglayiciyi kapatmaz. (int, bool) olarak
        (guncel ardisik hata sayisi, cooldown uygulandi mi) doner.
        """
        now = _to_iso(_utc_now())
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO provider_health (provider, cooldown_until, last_error, failure_count, updated_at)
                VALUES (?, NULL, ?, 1, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    last_error = excluded.last_error,
                    failure_count = provider_health.failure_count + 1,
                    updated_at = excluded.updated_at
                """,
                (provider, error, now),
            )
            row = conn.execute(
                "SELECT failure_count FROM provider_health WHERE provider = ?",
                (provider,),
            ).fetchone()
            failure_count = int(row["failure_count"]) if row else 1
            cooled_down = failure_count >= threshold
            if cooled_down:
                conn.execute(
                    "UPDATE provider_health SET cooldown_until = ?, failure_count = 0, updated_at = ? WHERE provider = ?",
                    (_iso_in(cooldown_sec), now, provider),
                )
        return failure_count, cooled_down

    def clear_provider_cooldown(self, provider: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO provider_health (provider, cooldown_until, last_error, failure_count, updated_at)
                VALUES (?, NULL, NULL, 0, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    cooldown_until = NULL,
                    last_error = NULL,
                    failure_count = 0,
                    updated_at = excluded.updated_at
                """,
                (provider, _to_iso(_utc_now())),
            )

    def purge_expired(self) -> int:
        """Suresi dolmus onbellek satirlarini siler; veritabaninin sinirsiz buyumesini onler."""
        now = _to_iso(_utc_now())
        removed = 0
        with self.connect() as conn:
            for table in ("search_cache", "transcript_cache"):
                cursor = conn.execute(f"DELETE FROM {table} WHERE expires_at <= ?", (now,))
                removed += cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        return removed

    def create_run(self, run_id: str, topic: str, filters: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO run (run_id, topic, filters_json, created_at, result_json)
                VALUES (?, ?, ?, ?, NULL)
                """,
                (run_id, topic, json.dumps(filters, ensure_ascii=False), _to_iso(_utc_now())),
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
