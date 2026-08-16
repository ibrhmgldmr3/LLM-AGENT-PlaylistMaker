from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.models import PlaylistResult, TranscriptResult, VideoCandidate
from src.storage.crypto import SecretBox


# Bir saglayicinin gecici olarak devre disi birakilmasi icin gereken ardisik hata sayisi.
# Tek bir videonun altyazisi yoksa saglayicinin tamami cezalandirilmamalidir.
DEFAULT_FAILURE_THRESHOLD = 3

_BUSY_TIMEOUT_SEC = 30.0

# `VideoCandidate` alanlari degistiginde arttirin. Onbellek anahtarina karistigi
# icin eski kayitlar otomatik olarak gecersizlesir; aksi halde TTL dolana kadar
# (saatler) eksik alanli adaylar servis edilir ve siralama sessizce bozulur.
CACHE_SCHEMA_VERSION = 2

# Tek kullanicili kurulumda tum calistirmalarin sahibi. Cok kullanicili moda
# gecildiginde gercek kullanici kimligi yazilir. Kolonu BUGUN eklemek tek
# satirlik; sonradan eklemek mevcut kayitlari ve tum sorgulari elden gecirmek olur.
DEFAULT_USER_ID = "local"


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


def _enable_wal(conn: sqlite3.Connection) -> None:
    """WAL'i acar; baska bir baglanti ayni anda aciyorsa sessizce gecer.

    `journal_mode` degisimi dosya duzeyinde ozel kilit istiyor ve SQLite bu
    islemde `busy_timeout`u BEKLEMEDEN `SQLITE_BUSY` dondurebiliyor. Es zamanli
    acilislarda (her istek kendi store'unu kuruyor, is parcaciklari da) bu
    "database is locked" olarak disari vuruyordu.

    WAL dosyanin KALICI bir ozelligi: bir kez ayarlandiginda oyle kaliyor.
    Dolayisiyla "su anda baskasi ayarliyor" durumunda baglantiyi dusurmek
    yanlis -- sonuc yine WAL olacak. Yalnizca kilit/mesgul hatasi yutuluyor;
    digerleri yukseliyor.
    """
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "locked" not in message and "busy" not in message:
            raise


class SQLiteStore:
    def __init__(self, db_path: str, encryption_key: str | None = None):
        self.db_path = db_path
        # Sirlar (OAuth jetonlari) bu kutu ile sifrelenir. Anahtar yoksa duz
        # metin yazilir ve eski davranis korunur.
        self._secrets = SecretBox(encryption_key)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=_BUSY_TIMEOUT_SEC)
        conn.row_factory = sqlite3.Row
        try:
            # SIRA ONEMLI: once `busy_timeout`, sonra `journal_mode`. Ikincisi
            # kilit bekleyebiliyor ve zaman asimi once kurulmus olmali.
            conn.execute("PRAGMA busy_timeout=%d" % int(_BUSY_TIMEOUT_SEC * 1000))
            _enable_wal(conn)
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
                CREATE TABLE IF NOT EXISTS provider_cooldown (
                    user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    cooldown_until TEXT,
                    last_error TEXT,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, provider)
                );
                CREATE TABLE IF NOT EXISTS run (
                    run_id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    filters_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    result_json TEXT,
                    user_id TEXT NOT NULL DEFAULT 'local'
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
                CREATE TABLE IF NOT EXISTS oauth_token (
                    user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    token_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, provider)
                );
                CREATE TABLE IF NOT EXISTS user_credential (
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, name)
                );
                CREATE TABLE IF NOT EXISTS session (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    email TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_session_expires ON session (expires_at);
                CREATE INDEX IF NOT EXISTS idx_search_cache_expires ON search_cache (expires_at);
                CREATE INDEX IF NOT EXISTS idx_transcript_cache_expires ON transcript_cache (expires_at);
                """
            )
            self._migrate(conn)

    @staticmethod
    def _add_column_if_missing(
        conn: sqlite3.Connection, table: str, column: str, definition: str
    ) -> None:
        """Sutunu ekler; baska bir baglanti onceden eklediyse sessizce gecer.

        "Once bak, sonra ekle" YARIS ICERIYOR: semayi ayni anda kuran iki
        baglanti da sutunu eksik gorur ve ikisi de `ALTER` calistirir; ikincisi
        `duplicate column name` ile patlar. SQLite'ta `ALTER` icin baglantilar
        arasi bir kilit yok, dolayisiyla kontrolu genisletmek cozmez.

        Hatanin kendisi zaten "baskasi uygulamis" sinyali oldugu icin dogru
        davranis onu yutmak. Yalnizca O hata yutuluyor; baska bir
        `OperationalError` (yazma izni, bozuk dosya) yukselmeye devam ediyor.
        """
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column in columns:
            return
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise

    @staticmethod
    def _copy_legacy_provider_health(conn: sqlite3.Connection) -> None:
        """Eski `provider_health` satirlarini `provider_cooldown`a kopyalar.

        Eski tabloda birincil anahtar yalnizca `provider` idi, yani saglayici
        sagligi KURULUM GENELINDE tutuluyordu: bir kullanicinin kota asimi
        DIGER HERKESIN aramasini soguturdu.

        Ilk denemede tablo `DROP` + `RENAME` ile yerinde degistiriliyordu ve bu
        YANLISTI: bir baglanti tabloyu dusururken bir digeri ona bakip
        "no such table" aliyordu. CI'da tam olarak boyle kirildi. Hata
        toleransini gocun ICINE koymak yetmiyor, cunku pencere disariya da
        acik -- dogru cozum pencereyi daraltmak degil HIC ACMAMAK.

        Bu yuzden yeni tablo AYRI ADLA kuruluyor ve eskisine dokunulmuyor:
        yikici adim yok, dolayisiyla yaris da yok. Eski tablo (varsa) artik
        okunmuyor; bir sonraki bakim adiminda silinebilir.
        """
        legacy = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='provider_health'"
        ).fetchone()
        if not legacy:
            return
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(provider_health)")}
        if "user_id" in columns:
            return  # zaten yeni sekilde; kopyalanacak eski veri yok

        failure = "COALESCE(failure_count, 0)" if "failure_count" in columns else "0"
        conn.execute(
            f"""
            INSERT OR IGNORE INTO provider_cooldown
                (user_id, provider, cooldown_until, last_error, failure_count, updated_at)
            SELECT 'local', provider, cooldown_until, last_error, {failure}, updated_at
            FROM provider_health
            """
        )

    @classmethod
    def _migrate(cls, conn: sqlite3.Connection) -> None:
        cls._add_column_if_missing(
            conn, "run", "user_id", f"user_id TEXT NOT NULL DEFAULT '{DEFAULT_USER_ID}'"
        )
        cls._copy_legacy_provider_health(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_run_user_created ON run (user_id, created_at DESC)")

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

    def get_provider_cooldown(self, provider: str, user_id: str = DEFAULT_USER_ID) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT cooldown_until FROM provider_cooldown WHERE user_id = ? AND provider = ?",
                (user_id, provider),
            ).fetchone()
        if not row:
            return None
        cooldown_until = row["cooldown_until"]
        parsed = _parse_iso(cooldown_until)
        if parsed and parsed > _utc_now():
            return cooldown_until
        return None

    def mark_provider_cooldown(
        self, provider: str, error: str, cooldown_sec: int, user_id: str = DEFAULT_USER_ID
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO provider_cooldown (user_id, provider, cooldown_until, last_error, failure_count, updated_at)
                VALUES (?, ?, ?, ?, COALESCE(
                    (SELECT failure_count FROM provider_cooldown WHERE user_id = ? AND provider = ?), 0), ?)
                ON CONFLICT(user_id, provider) DO UPDATE SET
                    cooldown_until = excluded.cooldown_until,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                """,
                (user_id, provider, _iso_in(cooldown_sec), error, user_id, provider, _to_iso(_utc_now())),
            )

    def record_provider_failure(
        self,
        provider: str,
        error: str,
        cooldown_sec: int,
        threshold: int = DEFAULT_FAILURE_THRESHOLD,
        user_id: str = DEFAULT_USER_ID,
    ) -> tuple[int, bool]:
        """Ardisik hata sayacini arttirir; esik asilirsa cooldown uygular.

        Tek bir videonun hatasi artik tum saglayiciyi kapatmaz. (int, bool) olarak
        (guncel ardisik hata sayisi, cooldown uygulandi mi) doner.
        """
        now = _to_iso(_utc_now())
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO provider_cooldown (user_id, provider, cooldown_until, last_error, failure_count, updated_at)
                VALUES (?, ?, NULL, ?, 1, ?)
                ON CONFLICT(user_id, provider) DO UPDATE SET
                    last_error = excluded.last_error,
                    failure_count = provider_cooldown.failure_count + 1,
                    updated_at = excluded.updated_at
                """,
                (user_id, provider, error, now),
            )
            row = conn.execute(
                "SELECT failure_count FROM provider_cooldown WHERE user_id = ? AND provider = ?",
                (user_id, provider),
            ).fetchone()
            failure_count = int(row["failure_count"]) if row else 1
            cooled_down = failure_count >= threshold
            if cooled_down:
                conn.execute(
                    "UPDATE provider_cooldown SET cooldown_until = ?, failure_count = 0, updated_at = ?"
                    " WHERE user_id = ? AND provider = ?",
                    (_iso_in(cooldown_sec), now, user_id, provider),
                )
        return failure_count, cooled_down

    def clear_provider_cooldown(self, provider: str, user_id: str = DEFAULT_USER_ID) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO provider_cooldown (user_id, provider, cooldown_until, last_error, failure_count, updated_at)
                VALUES (?, ?, NULL, NULL, 0, ?)
                ON CONFLICT(user_id, provider) DO UPDATE SET
                    cooldown_until = NULL,
                    last_error = NULL,
                    failure_count = 0,
                    updated_at = excluded.updated_at
                """,
                (user_id, provider, _to_iso(_utc_now())),
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

    def create_run(
        self, run_id: str, topic: str, filters: dict[str, Any], user_id: str = DEFAULT_USER_ID
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO run (run_id, topic, filters_json, created_at, result_json, user_id)
                VALUES (?, ?, ?, ?, NULL, ?)
                """,
                (run_id, topic, json.dumps(filters, ensure_ascii=False), _to_iso(_utc_now()), user_id),
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

    # ---------------------------------------------------------- OAuth token
    # Token KULLANICI BASINA saklanir: dosya yolu tek kullanicili varsayimdi ve
    # cok kullanicili moda gecerken en cok direnc gosteren yerdi.
    #
    # `SECRET_ENCRYPTION_KEY` tanimliysa jetonlar SIFRELI yazilir. Anahtar
    # yoksa duz metin kalir (eski davranis) ve okuma her iki bicimi de destekler,
    # boylece anahtar sonradan eklenebilir.

    # ------------------------------------------------- kullanici anahtarlari
    #
    # BYOK: cok kullanicili kurulumda her kullanici kendi API anahtarini
    # getiriyor. Paylasimli anahtar mumkun degil -- YouTube Data API kotasi
    # PROJE basina gunde 10.000 birim ve bir calistirma ~1.200 birim tuketiyor,
    # yani ikinci kullanici gunu bitiriyor.
    #
    # Degerler jetonlarla AYNI kutuyla sifreleniyor; anahtar yoksa duz metin
    # yazilir ve eski davranis korunur.

    def save_user_credential(self, user_id: str, name: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO user_credential (user_id, name, value, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, name, self._secrets.encrypt(value), _to_iso(_utc_now())),
            )

    def get_user_credentials(self, user_id: str) -> dict[str, str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT name, value FROM user_credential WHERE user_id = ?", (user_id,)
            ).fetchall()
        return {row["name"]: self._secrets.decrypt(row["value"]) for row in rows}

    def delete_user_credential(self, user_id: str, name: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM user_credential WHERE user_id = ? AND name = ?", (user_id, name)
            )
            return cursor.rowcount > 0

    # ------------------------------------------------------------- oturumlar
    #
    # Jetonun KENDISI degil, SHA-256 ozeti saklaniyor: veritabani sizsa bile
    # oturumlar devralinamaz. Ayni sebeple jeton yalnizca uretildigi anda,
    # cagirana bir kez donuyor.

    @staticmethod
    def hash_session_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create_session(self, token: str, user_id: str, email: str | None, ttl_sec: int) -> None:
        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO session (token_hash, user_id, email, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    self.hash_session_token(token),
                    user_id,
                    email,
                    _to_iso(now),
                    _to_iso(now + timedelta(seconds=ttl_sec)),
                ),
            )

    def get_session(self, token: str) -> dict[str, str] | None:
        """Suresi gecmemis oturumu dondurur; gecmisse silip `None` doner."""
        token_hash = self.hash_session_token(token)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT user_id, email, expires_at FROM session WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            if _is_expired(row["expires_at"]):
                conn.execute("DELETE FROM session WHERE token_hash = ?", (token_hash,))
                return None
        return {"user_id": row["user_id"], "email": row["email"]}

    def delete_session(self, token: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM session WHERE token_hash = ?", (self.hash_session_token(token),)
            )
            return cursor.rowcount > 0

    def save_oauth_token(self, user_id: str, provider: str, token_json: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO oauth_token (user_id, provider, token_json, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, provider, self._secrets.encrypt(token_json), _to_iso(_utc_now())),
            )

    def get_oauth_token(self, user_id: str, provider: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT token_json FROM oauth_token WHERE user_id = ? AND provider = ?",
                (user_id, provider),
            ).fetchone()
        return self._secrets.decrypt(row["token_json"]) if row else None

    def delete_oauth_token(self, user_id: str, provider: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM oauth_token WHERE user_id = ? AND provider = ?", (user_id, provider)
            )
        return bool(cursor.rowcount)

    # --------------------------------------------------------------- okuma
    # `run` tablolari uzun sure yalnizca YAZILIYORDU. Veri zaten duruyordu ama
    # erisilemiyordu; asagidakiler gecmis listesi ve tekil calistirma goruntuleme
    # icin gerekli minimum okuma yuzeyi.

    def get_run(self, run_id: str) -> PlaylistResult | None:
        """Tamamlanmis bir calistirmanin sonucunu dondurur.

        Henuz bitmemis (result_json NULL) calistirmalar icin None doner.
        """
        with self.connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM run WHERE run_id = ?", (run_id,)
            ).fetchone()
        if not row or not row["result_json"]:
            return None
        return PlaylistResult.model_validate(json.loads(row["result_json"]))

    def get_run_summary(self, run_id: str) -> dict[str, Any] | None:
        """Sonucun tamamini yuklemeden calistirmanin ust bilgisi."""
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT run_id, user_id, topic, filters_json, created_at,
                       result_json IS NOT NULL AS is_complete
                FROM run WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        return _run_summary_row(row) if row else None

    def list_runs(
        self,
        user_id: str | None = DEFAULT_USER_ID,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Calistirma gecmisi, yeniden eskiye.

        `user_id=None` tum kullanicilari dondurur (yonetim/tanilama icin).
        """
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        query = """
            SELECT run_id, user_id, topic, filters_json, created_at,
                   result_json IS NOT NULL AS is_complete
            FROM run
        """
        params: list[Any] = []
        if user_id is not None:
            query += " WHERE user_id = ?"
            params.append(user_id)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_run_summary_row(row) for row in rows]

    def count_runs(self, user_id: str | None = DEFAULT_USER_ID) -> int:
        with self.connect() as conn:
            if user_id is None:
                row = conn.execute("SELECT COUNT(*) AS n FROM run").fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) AS n FROM run WHERE user_id = ?", (user_id,)).fetchone()
        return int(row["n"]) if row else 0

    def count_recent_runs(self, user_id: str, within_sec: int = 86400) -> int:
        """Son `within_sec` saniyede bu kullanicinin baslattigi calistirma sayisi.

        Ayri bir sayac tablosu YOK: `run` tablosu zaten `user_id` ve
        `created_at` tasiyor. Ikinci bir kaynak tutmak, ikisinin birbirinden
        ayrilabilecegi bir yer daha yaratirdi.
        """
        cutoff = _to_iso(_utc_now() - timedelta(seconds=within_sec))
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM run WHERE user_id = ? AND created_at >= ?",
                (user_id, cutoff),
            ).fetchone()
        return int(row["n"]) if row else 0

    def delete_run(self, run_id: str, user_id: str | None = DEFAULT_USER_ID) -> bool:
        """Bir calistirmayi ve bagli kayitlarini siler."""
        with self.connect() as conn:
            if user_id is None:
                cursor = conn.execute("DELETE FROM run WHERE run_id = ?", (run_id,))
            else:
                cursor = conn.execute(
                    "DELETE FROM run WHERE run_id = ? AND user_id = ?", (run_id, user_id)
                )
            if not cursor.rowcount:
                return False
            conn.execute("DELETE FROM run_subtopic WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM run_video WHERE run_id = ?", (run_id,))
        return True


def _run_summary_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "run_id": row["run_id"],
        "user_id": row["user_id"],
        "topic": row["topic"],
        "filters": json.loads(row["filters_json"]),
        "created_at": row["created_at"],
        "is_complete": bool(row["is_complete"]),
    }
