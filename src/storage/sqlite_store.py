from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.models import PlaylistResult, TranscriptResult, VideoCandidate
from src.storage.crypto import SecretBox
from src.storage.dialect import Dialect, SqliteDialect


# Bir saglayicinin gecici olarak devre disi birakilmasi icin gereken ardisik hata sayisi.
# Tek bir videonun altyazisi yoksa saglayicinin tamami cezalandirilmamalidir.
DEFAULT_FAILURE_THRESHOLD = 3


# `VideoCandidate` alanlari degistiginde arttirin. Onbellek anahtarina karistigi
# icin eski kayitlar otomatik olarak gecersizlesir; aksi halde TTL dolana kadar
# (saatler) eksik alanli adaylar servis edilir ve siralama sessizce bozulur.
CACHE_SCHEMA_VERSION = 2

# Tek kullanicili kurulumda tum calistirmalarin sahibi. Cok kullanicili moda
# gecildiginde gercek kullanici kimligi yazilir. Kolonu BUGUN eklemek tek
# satirlik; sonradan eklemek mevcut kayitlari ve tum sorgulari elden gecirmek olur.
DEFAULT_USER_ID = "local"

# Saglayici sogumasinin kapsami. Kullanici basina DEGIL, SUNUCU GENELI.
#
# Soguma iki seye karsi koruyor ve ikisi de kullaniciya gore ayrismiyor:
#   - `yt_dlp` / `youtube_transcript_api`: sinir sunucunun IP'sine bagli
#   - `youtube_data_api`: anahtar PAYLASIMLI, kota proje basina
#
# Kapsam kullanici basinayken ikinci kullanici hic korunmuyordu: ayni IP'den
# ayni sinira tekrar giriyor ve engeli tipik olarak UZATIYORDU.
#
# `provider_cooldown.user_id` sutunu tarihsel adiyla duruyor; icerigi artik bir
# KAPSAM ve tek degeri bu sabit. Sutunu yeniden adlandirmadik: bu tabloda
# yerinde sema degisikligi bir kez CI'i kirdi (bkz. `_copy_legacy_provider_health`).
SERVER_SCOPE = "__server__"


@dataclass(frozen=True)
class RunAdmission:
    """Calistirma kabul edildi mi, edilmediyse NEDEN.

    `bool` yerine nesne: iki farkli ret sebebi var ve cagiranin bunlari
    ayirmasi sart -- "senin gunluk hakkin doldu" ile "servisin bugunku
    kapasitesi doldu" kullanici icin bambaska seyler. Yine de `__bool__`
    tanimli, cunku cagiranlarin cogunu yalnizca kabul edilip edilmedigi
    ilgilendiriyor.
    """

    accepted: bool
    reason: str | None = None

    def __bool__(self) -> bool:
        return self.accepted


ACCEPTED = RunAdmission(True)
USER_LIMIT = RunAdmission(False, "user_limit")
SERVICE_BUDGET = RunAdmission(False, "service_budget")

_log = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# YouTube Data API kotasi UTC'de DEGIL, Pasifik saatiyle gece yarisi sifirlaniyor.
# Gunu UTC'ye gore kovalamak, Turkiye saatiyle aksamustu "kotam doldu" derken
# Google'a gore gunun coktan donmus olmasi (ya da tersi) demekti -- 10 saate varan
# kayma. Tuketim raporunun tek isi bu soruyu dogru yanitlamak oldugu icin gun
# siniri Google'in kullandigi saate gore hesaplaniyor.
_QUOTA_TZ_NAME = "America/Los_Angeles"


def _quota_tz():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(_QUOTA_TZ_NAME)
    except Exception:
        # `tzdata` kurulu degil (ciplak Windows Python'u). Sabit PST'ye
        # dusuyoruz: yaz saatinde sinir 1 saat kayar ama UTC'ye dusmekten cok
        # daha yakin.
        _log.warning(
            "`%s` saat dilimi bulunamadi (tzdata kurulu mu?); kota gunu sabit UTC-8 ile hesaplaniyor",
            _QUOTA_TZ_NAME,
        )
        return timezone(timedelta(hours=-8))


def seconds_until_next_quota_day(moment: datetime | None = None) -> int:
    """Kota gununun donmesine kac saniye kaldi. `Retry-After` icin.

    Kullaniciya "yarin tekrar deneyin" demek yetmiyor: gun siniri Pasifik
    saatine gore ve Turkiye'den bakan biri icin gun ortasinda donuyor.
    """
    moment = moment or _utc_now()
    tz = _quota_tz()
    yerel = moment.astimezone(tz)
    ertesi = (yerel + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((ertesi - yerel).total_seconds()))


def quota_day(moment: datetime | None = None) -> str:
    """Verilen anin ait oldugu YouTube kota gunu (`YYYY-MM-DD`, Pasifik saati)."""
    return (moment or _utc_now()).astimezone(_quota_tz()).date().isoformat()


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
    def __init__(
        self,
        db_path: str,
        encryption_key: str | None = None,
        dialect: Dialect | None = None,
    ):
        # Imza DEGISMEDI: `SQLiteStore(path)` cagrilari oldugu gibi calisiyor.
        # `dialect` verilmezse SQLite kuruluyor -- Postgres yalnizca ACIKCA
        # istendiginde devreye giriyor.
        self.db_path = db_path
        self._dialect = dialect or SqliteDialect(db_path)
        # Sirlar (OAuth jetonlari) bu kutu ile sifrelenir. Anahtar yoksa duz
        # metin yazilir ve eski davranis korunur.
        self._secrets = SecretBox(encryption_key)
        self._ensure_schema()

    @contextmanager
    def connect(self):
        """Baglanti acar. Islem yonetimi ve lehce farklari `dialect` icinde."""
        with self._dialect.connect() as conn:
            yield conn

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
                    -- ISTENEN dil (sonuctaki dil degil): saglayicinin ne
                    -- yapacagini belirleyen girdi budur. Bkz.
                    -- `get_transcript_cache`.
                    language TEXT NOT NULL DEFAULT '',
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
                    user_id TEXT NOT NULL DEFAULT 'local',
                    -- Sunucu yeniden baslarken YARIDA kalan calistirmalar.
                    -- Gunluk hak sayiminda haric tutulur: kullanici hicbir sey
                    -- almadan hakkini kaybetmemeli.
                    interrupted_at TEXT,
                    -- Kabul aninda butceden AYRILAN kota. Calistirma bitince
                    -- (ya da yarida kaldigi anlasilinca) sifirlanir.
                    reserved_units INTEGER NOT NULL DEFAULT 0
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
                CREATE TABLE IF NOT EXISTS provider_event (
                    day TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    event TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (day, provider, event)
                );
                CREATE TABLE IF NOT EXISTS api_usage (
                    day TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    calls INTEGER NOT NULL DEFAULT 0,
                    units INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (day, user_id, provider, endpoint)
                );
                CREATE TABLE IF NOT EXISTS session (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    email TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                -- Bekleyen OAuth yetkilendirmeleri (PKCE).
                --
                -- SUREC ICINDE bir sozlukte tutuluyordu ve bu, web tarafini
                -- cogaltmanin onundeki somut engellerden biriydi: kullanici A
                -- replikasinda akisi baslatiyor, Google B replikasina donuyor
                -- ve state bulunamadigi icin giris basarisiz oluyordu.
                --
                -- `code_verifier` bir SIR (PKCE) ve `oauth_token` ile ayni
                -- sekilde sifrelenerek yaziliyor.
                CREATE TABLE IF NOT EXISTS oauth_state (
                    state TEXT PRIMARY KEY,
                    user_id TEXT,
                    code_verifier TEXT,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                -- ------------------------------------------------- ogrenme alani
                -- Bir "alan" (space) kullanicinin kalici bilgi havuzu: birden
                -- cok calistirmanin videolari + yukledigi dokumanlar. Calistirma
                -- (`run`) ile ARASINDA bag yok -- alan ondan uzun yasiyor ve
                -- calistirma silinse de icerigi alanda kalmali.
                CREATE TABLE IF NOT EXISTS space (
                    space_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_space_user ON space (user_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS space_source (
                    space_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    kind TEXT NOT NULL,          -- video | document
                    ref_id TEXT NOT NULL,        -- video_id | saklanan dosya adi
                    title TEXT NOT NULL,
                    url TEXT,
                    language TEXT,
                    status TEXT NOT NULL,        -- pending | indexed | no_text | failed
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    byte_size INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (space_id, source_id)
                );
                CREATE TABLE IF NOT EXISTS chunk (
                    chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    space_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    start_sec REAL,
                    end_sec REAL,
                    page INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_chunk_space ON chunk (space_id);
                CREATE INDEX IF NOT EXISTS idx_chunk_source ON chunk (space_id, source_id);
                CREATE TABLE IF NOT EXISTS chunk_embedding (
                    chunk_id INTEGER PRIMARY KEY,
                    model TEXT NOT NULL,
                    dim INTEGER NOT NULL,
                    vector BLOB NOT NULL          -- float32, little-endian
                );
                -- ARAMA INDEKSI. `content=` KULLANILMIYOR (harici icerik kipi):
                -- o kipte silme, `INSERT INTO chunk_fts(chunk_fts, 'delete', ...)`
                -- gibi ozel komutlar ve satirin ESKI degerinin birebir
                -- tekrarlanmasini gerektiriyor; senkron kalmayan bir indeks ise
                -- sessizce yanlis sonuc verir. Kendi kopyasini tutan duz bir FTS
                -- tablosunda silme siradan bir DELETE. Bedeli metnin ikinci bir
                -- kopyasi -- alan basina birkac bin parcada onemsiz.
                --
                -- `search_text` = `text_utils.search_key(...)`: Turkce harfler
                -- Latin karsiligina indirgenmis hali. Sorgu tarafi AYNI
                -- fonksiyondan geciyor; kritik olan donusum degil TEK olmasi.
                CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
                    search_text,
                    chunk_id UNINDEXED,
                    space_id UNINDEXED,
                    tokenize="unicode61 remove_diacritics 2"
                );
                CREATE INDEX IF NOT EXISTS idx_session_expires ON session (expires_at);
                CREATE INDEX IF NOT EXISTS idx_search_cache_expires ON search_cache (expires_at);
                CREATE INDEX IF NOT EXISTS idx_transcript_cache_expires ON transcript_cache (expires_at);
                """
            )
            self._migrate(conn, self._dialect)

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
            INSERT INTO provider_cooldown
                (user_id, provider, cooldown_until, last_error, failure_count, updated_at)
            SELECT 'local', provider, cooldown_until, last_error, {failure}, updated_at
            FROM provider_health
            -- `WHERE true` GEREKLI, sussuz degil: SQLite'ta `INSERT ... SELECT`
            -- ile birlikte yazilan upsert cumlesi ayristirilamiyor -- `ON`un
            -- SELECT'in JOIN'ine mi yoksa `ON CONFLICT`e mi ait oldugu belirsiz
            -- kaliyor ve `near "DO": syntax error` veriyor. Bos bir WHERE
            -- belirsizligi kaldiriyor. Postgres de ayni yazimi kabul ediyor.
            WHERE true
            ON CONFLICT(user_id, provider) DO NOTHING
            """
        )

    @staticmethod
    def _collapse_cooldowns_to_server_scope(conn: sqlite3.Connection) -> None:
        """Kullanici basina yazilmis eski sogumalari sunucu kapsamina toplar.

        Kapsam degisince eski satirlar ARTIK OKUNMUYOR; iclerinde suresi
        dolmamis bir soguma varsa koruma bir anligina kaybolurdu. Saglayici
        basina EN KORUMACI degeri (en ileri `cooldown_until`) sunucu kaydina
        tasiyoruz.

        Yikici adim YOK: tablo dusurulmuyor, sutun degistirilmiyor: yalnizca
        satir yaziliyor. (Bu tabloda yerinde sema degisikligi bir kez CI'i
        kirmisti -- bkz. `_copy_legacy_provider_health`.) Islem tekrarlanabilir:
        ikinci calistirmada tasinacak satir kalmadigi icin etkisiz.
        """
        conn.execute(
            """
            INSERT INTO provider_cooldown
                (user_id, provider, cooldown_until, last_error, failure_count, updated_at)
            SELECT ?, provider, MAX(cooldown_until), MAX(last_error), 0, MAX(updated_at)
            FROM provider_cooldown
            WHERE user_id != ? AND cooldown_until IS NOT NULL
            GROUP BY provider
            ON CONFLICT(user_id, provider) DO UPDATE SET
                cooldown_until = MAX(
                    COALESCE(provider_cooldown.cooldown_until, ''),
                    COALESCE(excluded.cooldown_until, '')
                ),
                updated_at = excluded.updated_at
            """,
            (SERVER_SCOPE, SERVER_SCOPE),
        )
        # Tasinan satirlar artik okunmuyor; birakmak veritabanini sisirirdi.
        conn.execute("DELETE FROM provider_cooldown WHERE user_id != ?", (SERVER_SCOPE,))

    @classmethod
    def _migrate(cls, conn: sqlite3.Connection, dialect: Dialect) -> None:
        # Eski sema onarimlari YALNIZCA gecmisi olan veritabaninda
        # (bkz. `supports_legacy_migration`); Postgres semasi zaten eksiksiz.
        if dialect.supports_legacy_migration:
            cls._legacy_repairs(conn)

        # Indeks EN SONDA: dayandigi `user_id` sutununu eski veritabanlarina
        # yukaridaki onarim ekliyor. Basa alindiginda "no such column: user_id"
        # veriyordu -- sema kurulumunu tumden dusuren bir sira hatasi.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_run_user_created ON run (user_id, created_at DESC)"
        )

    @classmethod
    def _legacy_repairs(cls, conn: sqlite3.Connection) -> None:
        """Onceki surumlerin biraktigi SQLite dosyalarini bugunku semaya getirir."""
        cls._add_column_if_missing(
            conn, "run", "user_id", f"user_id TEXT NOT NULL DEFAULT '{DEFAULT_USER_ID}'"
        )
        cls._add_column_if_missing(conn, "run", "interrupted_at", "interrupted_at TEXT")
        cls._add_column_if_missing(
            conn, "run", "reserved_units", "reserved_units INTEGER NOT NULL DEFAULT 0"
        )
        # Mevcut satirlar '' (dil belirtilmemis) olarak isaretlenir; bir sonraki
        # dilli istek onlari iskalar ve yeniden ceker. Bir kerelik isabet
        # kaybi, yanlis dilde transkript dondurmenin yanina bile yaklasmaz.
        cls._add_column_if_missing(
            conn, "transcript_cache", "language", "language TEXT NOT NULL DEFAULT ''"
        )
        cls._copy_legacy_provider_health(conn)
        cls._collapse_cooldowns_to_server_scope(conn)

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
                INSERT INTO search_cache (
                    cache_key, provider, query, filters_json, payload_json, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        provider = excluded.provider,
                        query = excluded.query,
                        filters_json = excluded.filters_json,
                        payload_json = excluded.payload_json,
                        expires_at = excluded.expires_at,
                        created_at = excluded.created_at
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

    # Onbellek anahtari ISTENEN dili de icerir. Dil, saglayicinin davranisini
    # DOGRUDAN degistiriyor (hangi altyazi izi secilir, ceviri yapilir mi);
    # anahtarda olmamasi iki yonden de yanlis sonuc veriyordu: Turkce bir kosu
    # "altyazi yok" yazinca Ingilizce kosu da o kaydi okuyor, tersi durumda ise
    # 30 gun boyunca YANLIS DILDE transkript donuyordu.
    #
    # Birincil anahtar (video_id, provider) olarak KALIYOR: dili anahtara
    # eklemek tabloyu yeniden kurmayi gerektirirdi. Bunun yerine yazma
    # ustune yaziyor, okuma ise dil esitligi ariyor -- yani iki dil ayni anda
    # onbellekte duramaz, ama YANLIS dilde bir kayit ASLA okunmaz. Kaciran
    # okuma zaten yeniden cekiyor; kabul edilen bedel isabet orani, dogruluk
    # degil.

    @staticmethod
    def _normalize_cache_language(language: str | None) -> str:
        return (language or "").strip().lower()

    def get_transcript_cache(
        self, video_id: str, provider: str, language: str | None = None
    ) -> TranscriptResult | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json, expires_at
                FROM transcript_cache
                WHERE video_id = ? AND provider = ? AND language = ?
                """,
                (video_id, provider, self._normalize_cache_language(language)),
            ).fetchone()
        if not row or _is_expired(row["expires_at"]):
            return None
        return TranscriptResult.model_validate(json.loads(row["payload_json"]))

    def put_transcript_cache(
        self, transcript: TranscriptResult, ttl_sec: int, language: str | None = None
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO transcript_cache
                    (video_id, provider, language, status, payload_json, expires_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(video_id, provider) DO UPDATE SET
                        language = excluded.language,
                        status = excluded.status,
                        payload_json = excluded.payload_json,
                        expires_at = excluded.expires_at,
                        updated_at = excluded.updated_at
                """,
                (
                    transcript.video_id,
                    transcript.source,
                    self._normalize_cache_language(language),
                    transcript.status,
                    json.dumps(transcript.model_dump(), ensure_ascii=False),
                    _iso_in(ttl_sec),
                    _to_iso(_utc_now()),
                ),
            )

    # ---------------------------------------------------- saglayici olaylari
    #
    # Soguma ANLIK bir durum: `provider_cooldown` yalnizca "su an dinleniyor mu"
    # sorusunu yanitliyor ve sure dolunca iz birakmadan kayboluyor. "Bu IP
    # gunde kac kez hiz sinirina takildi" sorusunun ise hicbir cevabi yoktu --
    # es zamanlilik ayarlarini (kac calistirma x kac isci) tahminle degil
    # olcumle degistirebilmek icin gereken sayi tam olarak bu.

    RATE_LIMITED = "rate_limited"
    FAILURE = "failure"
    COOLDOWN = "cooldown"
    SUCCESS = "success"
    # Yapilandirma eksigi yuzunden dinlendirme (PO token istegi, okunamayan
    # cerezler). Hiz sinirindan AYRI sayiliyor: ikisini ayni kovaya atmak
    # "bugun kac kez hiz sinirina takildik" sorusunun cevabini bozardi ve o
    # sayi es zamanlilik ayarlarini olcumle degistirmenin tek dayanagi.
    MISCONFIGURED = "misconfigured"

    def _record_provider_event(self, conn: sqlite3.Connection, provider: str, event: str) -> None:
        """Olayi gunluk sayaca ekler. ACIK bir baglanti aliyor: sayac, sayilan
        durumu yazan islemle AYNI islemde artmali, yoksa ikisi ayrisabilir."""
        conn.execute(
            """
            INSERT INTO provider_event (day, provider, event, count)
            VALUES (?, ?, ?, 1)
            -- `count` TABLO ADIYLA nitelenmis: niteliksiz hali Postgres'te
            -- `column reference "count" is ambiguous` veriyor. SQLite de
            -- nitelenmis yazimi kabul ediyor.
            ON CONFLICT(day, provider, event)
            DO UPDATE SET count = provider_event.count + 1
            """,
            (quota_day(), provider, event),
        )

    def get_provider_events(self, day: str | None = None) -> list[dict[str, Any]]:
        """Bir gunun saglayici olay dokumu; en sik olandan aza dogru.

        Gun kovasi kota raporuyla AYNI (Pasifik). Hiz sinirlari icin Pasifik
        sinirinin kendi basina bir anlami yok, ama tek bir raporda iki farkli
        "bugun" tanimi olmasi okuyani yanilturdu.
        """
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT provider, event, count FROM provider_event
                WHERE day = ? ORDER BY count DESC, provider
                """,
                (day or quota_day(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_provider_success(self, provider: str) -> None:
        """Kullanilabilir bir saglayici sonucunu sayar.

        Yalnizca hata ve dinlenme sayilari, bir yedegin GERCEKTEN calisip
        calismadigini soyleyemez -- sadece denendigini soyler. "yt-dlp altyazi
        yolu vakalarin yuzde kacinda ise yariyor" sorusunun cevabi bu sayaca
        bagli ve o cevap, es zamanlilik ayarlarini tahminle degil olcumle
        degistirmenin tek dayanagi.

        Diger saglayici olay yazimlarinin YANINDA duruyor ki cagiran taraflarin
        veritabani semasini bilmesi gerekmesin.
        """
        with self.connect() as conn:
            self._record_provider_event(conn, provider, self.SUCCESS)

    def get_provider_cooldown(self, provider: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT cooldown_until FROM provider_cooldown WHERE user_id = ? AND provider = ?",
                (SERVER_SCOPE, provider),
            ).fetchone()
        if not row:
            return None
        cooldown_until = row["cooldown_until"]
        parsed = _parse_iso(cooldown_until)
        if parsed and parsed > _utc_now():
            return cooldown_until
        return None

    def mark_provider_cooldown(
        self, provider: str, error: str, cooldown_sec: int, event: str | None = None
    ) -> None:
        """Saglayiciyi SUNUCU GENELINDE dinlendirir (bkz. `SERVER_SCOPE`).

        `event` dinlendirmenin SEBEBINI etiketler ve varsayilani hiz siniri:
        bu metodun uzun sure tek cagrilma sebebi oydu. Yapilandirma kaynakli
        dinlendirmeler `MISCONFIGURED` gecmeli, yoksa hiz siniri sayaci
        gercekte yasanmamis olaylarla sisiyordu.
        """
        user_id = SERVER_SCOPE
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
            # Sayac BURADA, store icinde artiyor: cagri yerlerinde artirmak
            # unutulabilir bir adim olurdu ve bir kez unutuldugunda olcum
            # sessizce eksik kalirdi.
            self._record_provider_event(conn, provider, event or self.RATE_LIMITED)

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

        Kapsam SUNUCU GENELI: buraya dusen hatalar altyapi hatalari. Videoya
        OZGU kalici durumlar (`VideoUnavailableError`) cagiran tarafta ayri
        yakalaniyor ve saglayiciyi hic cezalandirmiyor.
        """
        user_id = SERVER_SCOPE
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
            self._record_provider_event(conn, provider, self.FAILURE)
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
                self._record_provider_event(conn, provider, self.COOLDOWN)
        return failure_count, cooled_down

    def clear_provider_cooldown(self, provider: str) -> None:
        """Basarili cagridan sonra ARDISIK HATA SAYACINI sifirlar.

        Suresi DOLMAMIS bir sogumayi IPTAL ETMEZ. Kapsam sunucu geneline
        cikinca bu bir yarisa donusuyordu: A calistirmasi hiz sinirina takilip
        30 dakikalik soguma yaziyor, tam o sirada ucusta olan B calistirmasinin
        basarili bir cagrisi sogumayi siliyor ve herkes yeniden ayni sinira
        giriyordu. Ayrica dogrusu da bu: sunucu "1800 saniye bekle" dediyse
        baska bir istegin sansli donmesi o talimati gecersiz kilmaz.

        Soguma zaten kendiliginden sona eriyor -- `get_provider_cooldown`
        suresi gecmis kaydi zaten `None` sayiyor.
        """
        now = _to_iso(_utc_now())
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO provider_cooldown (user_id, provider, cooldown_until, last_error, failure_count, updated_at)
                VALUES (?, ?, NULL, NULL, 0, ?)
                ON CONFLICT(user_id, provider) DO UPDATE SET
                    cooldown_until = CASE
                        WHEN provider_cooldown.cooldown_until IS NOT NULL
                             AND provider_cooldown.cooldown_until > excluded.updated_at
                        THEN provider_cooldown.cooldown_until
                        ELSE NULL
                    END,
                    last_error = NULL,
                    failure_count = 0,
                    updated_at = excluded.updated_at
                """,
                (SERVER_SCOPE, provider, now),
            )

    # Gunluk sayaclarin (`api_usage`, `provider_event`) saklanma suresi.
    # Isletme sorularinin tamami "bugun/bu hafta" olceginde; uc aylik gecmis
    # egilim icin fazlasiyla yeterli ve satirlar minik.
    COUNTER_RETENTION_DAYS = 90

    def purge_expired(self) -> dict[str, int]:
        """Suresi dolmus/eskimis satirlari siler. Silinenleri tablo bazinda doner.

        Eskiden YALNIZCA iki onbellek tablosuna bakiyordu. Digerleri sessizce
        sinirsiz buyuyordu:

        - `session`: suresi gecen kayit yalnizca O JETONLA tekrar gelindiginde
          siliniyordu. Kimse donmezse satir kaliyor -- herkese acik bir serviste
          bu, giris yapip bir daha gelmeyen her ziyaretci demek.
        - `api_usage` / `provider_event`: gunluk sayaclar, hicbir zaman.
        """
        now = _to_iso(_utc_now())
        cutoff_day = quota_day(_utc_now() - timedelta(days=self.COUNTER_RETENTION_DAYS))
        removed: dict[str, int] = {}

        def _count(cursor) -> int:
            return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0

        with self.connect() as conn:
            for table in ("search_cache", "transcript_cache"):
                removed[table] = _count(
                    conn.execute(f"DELETE FROM {table} WHERE expires_at <= ?", (now,))
                )
            removed["session"] = _count(
                conn.execute("DELETE FROM session WHERE expires_at <= ?", (now,))
            )
            for table in ("api_usage", "provider_event"):
                removed[table] = _count(
                    conn.execute(f"DELETE FROM {table} WHERE day < ?", (cutoff_day,))
                )
        return removed

    def mark_interrupted_runs(self) -> int:
        """Yarida kalmis calistirmalari isaretler; isaretlenen sayiyi doner.

        YALNIZCA ACILISTA cagrilmali. Olcut "sonucu yok" -- ve bu, ancak hicbir
        is calismiyorken dogru bir olcut: surec icinde calisan bir calistirmanin
        da sonucu henuz yoktur. Acilista tanim geregi hicbir is calismiyor
        (`InProcessJobRunner` durumu bellekte tutuyor ve yeniden baslamayla
        kayboluyor), dolayisiyla sonucu olmayan her satir gercekten yarida
        kalmis demektir.

        Neden isaretleniyor da SILINMIYOR: gecmis listesinde "yarim kaldi"
        olarak gorunmeye devam etmeli. Kullaniciya yalan soylemeden hakkini
        geri vermenin yolu bu.
        """
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE run SET interrupted_at = ?, reserved_units = 0"
                " WHERE result_json IS NULL AND interrupted_at IS NULL",
                (_to_iso(_utc_now()),),
            )
            return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0

    def list_run_ids(self) -> set[str]:
        """Veritabaninda kayitli tum calistirma kimlikleri.

        Diskteki sahipsiz calistirma dizinlerini bulmak icin: dosya sistemi
        ile veritabanini karsilastiran taraf servis katmani (depolama katmani
        disk hakkinda hicbir sey bilmiyor)."""
        with self.connect() as conn:
            return {row["run_id"] for row in conn.execute("SELECT run_id FROM run")}

    def create_run(
        self, run_id: str, topic: str, filters: dict[str, Any], user_id: str = DEFAULT_USER_ID
    ) -> None:
        with self.connect() as conn:
            # `INSERT OR REPLACE` DEGIL: is parcacigi calistirmaya baslarken bu
            # satiri yeniden yaziyor ve REPLACE, kabul aninda ayrilan
            # `reserved_units` ile `created_at`i sifirlardi -- rezervasyon
            # kaybolunca butce kontrolu de anlamini yitirirdi. Cakismada
            # yalnizca istegin tasidigi alanlar guncelleniyor.
            conn.execute(
                """
                INSERT INTO run (run_id, topic, filters_json, created_at, result_json, user_id)
                VALUES (?, ?, ?, ?, NULL, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    topic = excluded.topic,
                    filters_json = excluded.filters_json,
                    user_id = excluded.user_id
                """,
                (run_id, topic, json.dumps(filters, ensure_ascii=False), _to_iso(_utc_now()), user_id),
            )

    def create_run_within_daily_limit(
        self,
        run_id: str,
        topic: str,
        filters: dict[str, Any],
        user_id: str = DEFAULT_USER_ID,
        *,
        max_per_day: int,
        within_sec: int = 86400,
        max_units_per_day: int = 0,
        estimated_units: int = 0,
    ) -> RunAdmission:
        """Iki tavani birden kontrol edip satiri AYNI islemde yazar.

        1. `max_per_day` -- KULLANICI basina gunluk calistirma (0 = sinirsiz)
        2. `max_units_per_day` -- SERVIS geneli gunluk kota butcesi (0 = kapali)

        Ikisi de burada, tek `BEGIN IMMEDIATE` islemi icinde: sayma ve yazma
        ayrildigi anda iki es zamanli istek de kontrolu ayni (eski) sayiyla
        gecip tavani asabiliyor. Kullanici sinirinde bu olculdu -- 3 sinirinda
        12 es zamanli istekten 11'i kabul ediliyordu. Servis butcesi icin ayni
        yaris daha da pahali: asildiginda YouTube 403 doner ve o gun HERKES
        icin biter.

        `estimated_units` EN KOTU durum tahmini (bkz. `estimate_run_units`).
        Onbellek isabetinde gercek maliyet sifira inebilir ama bunu onceden
        bilmenin yolu yok; dusuk tahmin, butceyi asip yarida olen bir
        calistirma demek olurdu.

        Ayri bir sayac tablosu YOK: `run` tablosu zaten `user_id` ve
        `created_at`, `api_usage` da gunluk birimleri tasiyor. Ucuncu bir
        kaynak tutmak, birbirinden ayrilabilecek bir yer daha yaratirdi.
        """
        with self.connect() as conn:
            self._dialect.lock(conn, "run_admission")
            if max_per_day:
                cutoff = _to_iso(_utc_now() - timedelta(seconds=within_sec))
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM run"
                    " WHERE user_id = ? AND created_at >= ? AND interrupted_at IS NULL",
                    (user_id, cutoff),
                ).fetchone()
                if (int(row["n"]) if row else 0) >= max_per_day:
                    return USER_LIMIT
            if max_units_per_day:
                spent = int(
                    conn.execute(
                        "SELECT COALESCE(SUM(units), 0) AS total FROM api_usage WHERE day = ?",
                        (quota_day(),),
                    ).fetchone()["total"]
                )
                # UCUSTAKI calistirmalarin ayrilmis kotasi da sayiliyor.
                #
                # Yalnizca gerceklesmis harcamaya bakmak YETMIYOR: harcama
                # kabulden DAKIKALAR sonra olusuyor, dolayisiyla es zamanli
                # istekler ayni "harcanan" degerini okuyup hepsi geciyordu.
                # Olculdu: 5 calistirmalik butceye 6 calistirma kabul edildi.
                reserved = int(
                    conn.execute(
                        "SELECT COALESCE(SUM(reserved_units), 0) AS total FROM run"
                        " WHERE result_json IS NULL AND interrupted_at IS NULL"
                    ).fetchone()["total"]
                )
                # Ucustaki bir calistirma hem rezervasyonunu hem o ana kadarki
                # gercek harcamasini tasiyor, yani kisa sureligine IKI KEZ
                # sayiliyor. Bilerek: fazla saymanin bedeli birkac yuz birimlik
                # kullanilmayan pay, eksik saymanin bedeli o gunu herkes icin
                # bitiren bir 403.
                if spent + reserved + estimated_units > max_units_per_day:
                    return SERVICE_BUDGET
            conn.execute(
                """
                INSERT INTO run
                    (run_id, topic, filters_json, created_at, result_json, user_id, reserved_units)
                VALUES (?, ?, ?, ?, NULL, ?, ?)
                    ON CONFLICT(run_id) DO UPDATE SET
                        topic = excluded.topic,
                        filters_json = excluded.filters_json,
                        created_at = excluded.created_at,
                        result_json = excluded.result_json,
                        user_id = excluded.user_id,
                        reserved_units = excluded.reserved_units,
                        interrupted_at = NULL
                """,
                (
                    run_id,
                    topic,
                    json.dumps(filters, ensure_ascii=False),
                    _to_iso(_utc_now()),
                    user_id,
                    estimated_units,
                ),
            )
            return ACCEPTED

    def add_run_subtopic(self, run_id: str, position: int, payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO run_subtopic (run_id, position, subtopic_json)
                VALUES (?, ?, ?)
                    ON CONFLICT(run_id, position) DO UPDATE SET
                        subtopic_json = excluded.subtopic_json
                """,
                (run_id, position, json.dumps(payload, ensure_ascii=False)),
            )

    def add_run_video(self, run_id: str, stage: str, video_id: str, payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO run_video (run_id, stage, video_id, payload_json)
                VALUES (?, ?, ?, ?)
                    ON CONFLICT(run_id, stage, video_id) DO UPDATE SET
                        payload_json = excluded.payload_json
                """,
                (run_id, stage, video_id, json.dumps(payload, ensure_ascii=False)),
            )

    def release_run_reservation(self, run_id: str) -> None:
        """Basarisiz/iptal edilmis bir calistirmanin kota rezervasyonunu birakir.

        `finalize_run` rezervasyonu YALNIZCA basari yolunda birakiyordu. Basarisiz
        bir calistirmada `result_json` NULL ve `interrupted_at` NULL kaliyor, yani
        satir `create_run_within_daily_limit`in "ucustaki rezervasyonlar"
        toplamina SUREKLI giriyordu -- oysa is bitmisti ve hicbir sey harcamamis
        olabilirdi.

        Sonucu olculdu: 3.000 birimlik butcede ust uste basarisiz olan uc
        calistirma, GERCEK harcama sifirken butcenin tamamini kilitledi ve o
        gunku her istegi "servisin kapasitesi doldu" ile reddettirdi. Rezervasyon
        ancak surec yeniden baslayip `mark_interrupted_runs` calisinca
        cozuluyordu.

        `result_json`a DOKUNULMUYOR: calistirmanin sonucu yok ve oyle kalmali.
        Birakilan tek sey butce uzerindeki tutuş.
        """
        with self.connect() as conn:
            conn.execute("UPDATE run SET reserved_units = 0 WHERE run_id = ?", (run_id,))

    def finalize_run(self, run_id: str, result: PlaylistResult) -> None:
        with self.connect() as conn:
            # Rezervasyon BURADA birakiliyor: calistirma bitti, artik gercek
            # harcamasi `api_usage`ta ve iki kez sayilmasina gerek yok.
            conn.execute(
                "UPDATE run SET result_json = ?, reserved_units = 0 WHERE run_id = ?",
                (json.dumps(result.model_dump(), ensure_ascii=False), run_id),
            )

    # ------------------------------------------------------------ kota olcumu
    #
    # Neden GERCEK cagrilar sayiliyor da "calistirma sayisi x 1.200" tahmini
    # kullanilmiyor: arama sonuclari 6 saat onbellekleniyor (`search_cache`),
    # yani ayni konuyu tekrar isleyen bir calistirma SIFIR kota harciyor.
    # Tahmin, gercekte harcanmayan kotayi harcanmis gosterip "hakkim doldu mu"
    # sorusunu yanlis yanitlardi.

    def record_api_usage(
        self, user_id: str, provider: str, endpoint: str, units: int, day: str | None = None
    ) -> None:
        """Bir API cagrisini gunluk sayaca ekler.

        Cagri BASARILI olduğunda cagriliyor; dolayisiyla rapor bir ALT SINIR.
        Kotasi dolmus (403) bir istek Google tarafindan da ucretlendirilmiyor,
        yani alt sinir pratikte gercege cok yakin.
        """
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO api_usage (day, user_id, provider, endpoint, calls, units)
                VALUES (?, ?, ?, ?, 1, ?)
                -- Sutunlar TABLO ADIYLA nitelikli. Niteliksiz `calls`,
                -- Postgres'te hedef satirla `excluded` arasinda BELIRSIZ
                -- (`AmbiguousColumn`); SQLite ise kabul ediyor, yani hata
                -- yalnizca Postgres'te ve yalnizca CALISMA aninda cikiyor.
                ON CONFLICT(day, user_id, provider, endpoint) DO UPDATE SET
                    calls = api_usage.calls + 1,
                    units = api_usage.units + excluded.units
                """,
                (day or quota_day(), user_id, provider, endpoint, int(units)),
            )

    def sum_api_units(self, user_id: str | None = None, day: str | None = None) -> int:
        """Bir kota gununde harcanan toplam birim. `user_id=None` = tum kullanicilar."""
        sql = "SELECT COALESCE(SUM(units), 0) AS total FROM api_usage WHERE day = ?"
        params: list[Any] = [day or quota_day()]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        with self.connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row["total"]) if row else 0

    def get_api_usage(self, day: str | None = None) -> list[dict[str, Any]]:
        """Bir kota gununun tuketim dokumu; cok harcayandan aza dogru."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT user_id, provider, endpoint, calls, units
                FROM api_usage WHERE day = ?
                ORDER BY units DESC, endpoint
                """,
                (day or quota_day(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_runs_since(self, user_id: str, within_sec: int = 86400) -> int:
        """Son `within_sec` saniyede bu kullanicinin baslattigi calistirma sayisi.

        Yalnizca RAPORLAMA icin. Gunluk siniri UYGULAYAN yol bu degil --
        o kontrol `create_run_within_daily_limit` icinde, yazmayla ayni islemde
        yapiliyor (bkz. oradaki TOCTOU aciklamasi). Ikisi bilerek ayri: burada
        yarisa duyarli olmayan bir okuma yeterli.
        """
        cutoff = _to_iso(_utc_now() - timedelta(seconds=within_sec))
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM run"
                " WHERE user_id = ? AND created_at >= ? AND interrupted_at IS NULL",
                (user_id, cutoff),
            ).fetchone()
        return int(row["n"]) if row else 0

    def count_runs_by_user_since(self, within_sec: int = 86400) -> list[dict[str, Any]]:
        cutoff = _to_iso(_utc_now() - timedelta(seconds=within_sec))
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT user_id, COUNT(*) AS runs FROM run
                WHERE created_at >= ? GROUP BY user_id ORDER BY runs DESC
                """,
                (cutoff,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_active_cooldowns(self) -> list[dict[str, Any]]:
        """Su an devre disi olan saglayicilar. Suresi gecmis satirlar elenir."""
        now = _to_iso(_utc_now())
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT user_id AS scope, provider, cooldown_until, last_error, failure_count
                FROM provider_cooldown
                WHERE cooldown_until IS NOT NULL AND cooldown_until > ?
                ORDER BY cooldown_until DESC
                """,
                (now,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------- sifre cozme yardimcisi

    def _decrypt_or_none(self, stored: str, what: str) -> str | None:
        """Cozulemeyen kaydi HATA yerine "yok" sayar.

        `SECRET_ENCRYPTION_KEY` degisir ya da bozulursa `decrypt()` `ValueError`
        firlatiyor. Bu istisna yukari birakildiginda okuma uclari 500 doner ve
        kullanicinin kendi kendine kurtulacagi bir yol KALMAZ: "bagli mi"
        sorusu bile patladigi icin arayuz baglantiyi kesme dugmesini bile
        gosteremiyordu, hesap kalici olarak kilitleniyordu.

        Cozulemeyen kayit zaten KULLANILAMAZ durumda; onu "yok" saymak
        kullaniciya normal yeniden yetkilendirme akisini geri veriyor ve ilk
        basarili yazma bozuk satirin uzerine biniyor. Sessiz kalmamak icin
        durum WARNING olarak loglaniyor.
        """
        try:
            return self._secrets.decrypt(stored)
        except ValueError as exc:
            _log.warning(
                "%s cozulemedi (SECRET_ENCRYPTION_KEY degismis ya da kayit bozulmus olabilir): %s",
                what,
                exc,
            )
            return None

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
                INSERT INTO session (token_hash, user_id, email, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(token_hash) DO UPDATE SET
                        user_id = excluded.user_id,
                        email = excluded.email,
                        created_at = excluded.created_at,
                        expires_at = excluded.expires_at
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

    # ------------------------------------------------------- OAuth state (PKCE)
    #
    # Bekleyen yetkilendirmeler SUREC ICINDE bir sozlukte duruyordu. Bu, web
    # tarafini cogaltmanin onundeki somut engellerden biriydi: kullanici A
    # replikasinda akisi baslatiyor, Google B replikasina donuyor ve state
    # bulunamadigi icin giris basarisiz oluyordu.

    def put_oauth_state(
        self,
        state: str,
        user_id: str | None,
        code_verifier: str | None,
        ttl_sec: int,
        max_pending: int,
    ) -> None:
        """Bekleyen bir yetkilendirmeyi kaydeder; suresi dolmuslari toplar.

        `max_pending` ust siniri ZORUNLU: bu ucu cagirmak oturum gerektirmiyor
        (gerektiremez -- giris yapmak icin once giris yapmis olmak gerekirdi),
        yani tek sinir TTL olsaydi kimliksiz biri 10 dakikalik pencerede
        tabloyu istedigi kadar buyutebilirdi.

        Tavan asilinca EN ESKI bekleyenler atiliyor, yeni istek REDDEDILMIYOR:
        reddetmek, tabloyu doldurmayi basaran birinin TUM yeni girisleri
        kilitlemesi demek olurdu. Az once tiklamis gercek kullanicinin kaydi en
        TAZE olan, yani en son atilacak olan.
        """
        now = _utc_now()
        with self.connect() as conn:
            conn.execute("DELETE FROM oauth_state WHERE expires_at < ?", (_to_iso(now),))
            conn.execute(
                """
                INSERT INTO oauth_state
                    (state, user_id, code_verifier, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(state) DO UPDATE SET
                        user_id = excluded.user_id,
                        code_verifier = excluded.code_verifier,
                        expires_at = excluded.expires_at,
                        created_at = excluded.created_at
                """,
                (
                    state,
                    user_id,
                    self._secrets.encrypt(code_verifier) if code_verifier else None,
                    _to_iso(now + timedelta(seconds=ttl_sec)),
                    _to_iso(now),
                ),
            )
            if max_pending > 0:
                conn.execute(
                    """
                    -- "En TAZE `max_pending` disindakileri sil" biciminde
                    -- yaziliyor. Once `LIMIT -1 OFFSET ?` kullaniliyordu:
                    -- SQLite `-1`i "sinir yok" sayiyor, Postgres ise
                    -- `LIMIT must not be negative` ile reddediyor. Bu yazim
                    -- ayni sonucu iki lehcede de veriyor.
                    DELETE FROM oauth_state WHERE state NOT IN (
                        SELECT state FROM oauth_state
                        ORDER BY created_at DESC, state DESC
                        LIMIT ?
                    )
                    """,
                    (max_pending,),
                )

    def consume_oauth_state(self, state: str) -> tuple[str | None, str | None] | None:
        """State'i TEK KULLANIMLIK olarak tuketir; `(user_id, code_verifier)`.

        Okuma ve silme AYNI `BEGIN IMMEDIATE` islemi icinde: ayrildiklari anda
        ayni state ile gelen iki es zamanli callback de kaydi okuyup ikisi de
        gecerdi. Tek kullanimlik olmasi CSRF korumasinin kendisi.

        Suresi dolmus kayit YOK sayiliyor (ve temizleniyor): TTL kontrolunu
        cagiran tarafa birakmak, unutuldugu gun sessizce suresiz gecerli
        state'ler demekti.
        """
        now = _utc_now()
        with self.connect() as conn:
            self._dialect.lock(conn, "oauth_state")
            row = conn.execute(
                "SELECT user_id, code_verifier, expires_at FROM oauth_state WHERE state = ?",
                (state,),
            ).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM oauth_state WHERE state = ?", (state,))
            expires_at = _parse_iso(row["expires_at"])
            if expires_at is not None and expires_at <= now:
                return None
            verifier = row["code_verifier"]
            return (
                row["user_id"],
                self._decrypt_or_none(verifier, "PKCE doğrulayıcısı") if verifier else None,
            )

    def count_oauth_states(self) -> int:
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) AS n FROM oauth_state").fetchone()["n"])

    # ---------------------------------------------------------- OAuth token
    # Token KULLANICI BASINA saklanir: dosya yolu tek kullanicili varsayimdi ve
    # cok kullanicili moda gecerken en cok direnc gosteren yerdi.
    #
    # `SECRET_ENCRYPTION_KEY` tanimliysa jetonlar SIFRELI yazilir. Anahtar
    # yoksa duz metin kalir (eski davranis) ve okuma her iki bicimi de destekler,
    # boylece anahtar sonradan eklenebilir.

    def save_oauth_token(self, user_id: str, provider: str, token_json: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO oauth_token (user_id, provider, token_json, updated_at)
                VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id, provider) DO UPDATE SET
                        token_json = excluded.token_json,
                        updated_at = excluded.updated_at
                """,
                (user_id, provider, self._secrets.encrypt(token_json), _to_iso(_utc_now())),
            )

    def get_oauth_token(self, user_id: str, provider: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT token_json FROM oauth_token WHERE user_id = ? AND provider = ?",
                (user_id, provider),
            ).fetchone()
        return self._decrypt_or_none(row["token_json"], f"`{provider}` OAuth jetonu") if row else None

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

    # ------------------------------------------------------- ogrenme alanlari
    #
    # SAHIPLIK her sorguda `user_id` ile suzuluyor ve cagiran bunu ES GECEMEZ:
    # `user_id` opsiyonel bir filtre DEGIL, zorunlu parametre. `run` tarafinda
    # `None` = "tum kullanicilar" seklinde bir yonetim kacisi var; burada
    # bilerek yok -- alanlar kullanicinin YUKLEDIGI dosyalari tasiyor ve
    # "yanlislikla None gecildi" durumunun bedeli orada cok daha agir.

    def create_space(self, space_id: str, user_id: str, name: str) -> None:
        now = _to_iso(_utc_now())
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO space (space_id, user_id, name, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (space_id, user_id, name, now, now),
            )

    def get_space(self, space_id: str, user_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT space_id, user_id, name, created_at, updated_at FROM space"
                " WHERE space_id = ? AND user_id = ?",
                (space_id, user_id),
            ).fetchone()
        return dict(row) if row else None

    def list_spaces(
        self, user_id: str, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT s.space_id, s.name, s.created_at, s.updated_at,
                       (SELECT COUNT(*) FROM space_source ss
                         WHERE ss.space_id = s.space_id) AS source_count,
                       (SELECT COUNT(*) FROM chunk c
                         WHERE c.space_id = s.space_id) AS chunk_count
                FROM space s
                WHERE s.user_id = ?
                ORDER BY s.updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (user_id, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_spaces(self, user_id: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM space WHERE user_id = ?", (user_id,)
            ).fetchone()
        return int(row["n"]) if row else 0

    def touch_space(self, space_id: str) -> None:
        """`updated_at`i tazeler. Alan listesi buna gore siralaniyor."""
        with self.connect() as conn:
            conn.execute(
                "UPDATE space SET updated_at = ? WHERE space_id = ?",
                (_to_iso(_utc_now()), space_id),
            )

    def delete_space(self, space_id: str, user_id: str) -> bool:
        """Alani ve BAGLI HER SEYI siler: kaynaklar, parcalar, FTS, vektorler.

        Tek transaction icinde: yarim kalan bir silme, arama indeksinde sahibi
        olmayan satirlar birakirdi ve o satirlar sonuclara sizardi.

        Diskteki yuklenmis dosyalara BURADA dokunulmuyor -- depo katmani dosya
        sistemini tanimiyor (ayni gerekce `run_retention.py` ust aciklamasinda
        yazili). Onu `space_retention.delete_space` yapiyor.
        """
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM space WHERE space_id = ? AND user_id = ?",
                (space_id, user_id),
            )
            if not cursor.rowcount:
                return False
            self._delete_space_content(conn, space_id)
        return True

    @staticmethod
    def _delete_space_content(conn: sqlite3.Connection, space_id: str) -> None:
        conn.execute(
            "DELETE FROM chunk_embedding WHERE chunk_id IN"
            " (SELECT chunk_id FROM chunk WHERE space_id = ?)",
            (space_id,),
        )
        conn.execute("DELETE FROM chunk_fts WHERE space_id = ?", (space_id,))
        conn.execute("DELETE FROM chunk WHERE space_id = ?", (space_id,))
        conn.execute("DELETE FROM space_source WHERE space_id = ?", (space_id,))

    # -------------------------------------------------------------- kaynaklar

    def add_source(
        self,
        space_id: str,
        source_id: str,
        *,
        kind: str,
        ref_id: str,
        title: str,
        url: str | None = None,
        language: str | None = None,
        status: str = "pending",
        byte_size: int = 0,
    ) -> None:
        """Kaynagi ekler ya da mevcut kaydin uzerine yazar.

        `INSERT OR REPLACE`: ayni videoyu iceren ikinci bir calistirma alana
        eklendiginde ya da basarisiz bir indeksleme tekrarlandiginda kayit
        TAZELENMELI, ikinci kez eklenmemeli. Tekillik zaten
        `(space_id, source_id)` birincil anahtarinda.
        """
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO space_source (
                    space_id, source_id, kind, ref_id, title, url, language,
                    status, chunk_count, byte_size, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, NULL, ?)
                    ON CONFLICT(space_id, source_id) DO UPDATE SET
                        kind = excluded.kind,
                        ref_id = excluded.ref_id,
                        title = excluded.title,
                        url = excluded.url,
                        language = excluded.language,
                        status = excluded.status,
                        chunk_count = excluded.chunk_count,
                        byte_size = excluded.byte_size,
                        error = excluded.error,
                        created_at = excluded.created_at
                """,
                (
                    space_id,
                    source_id,
                    kind,
                    ref_id,
                    title,
                    url,
                    language,
                    status,
                    int(byte_size),
                    _to_iso(_utc_now()),
                ),
            )

    def update_source(
        self,
        space_id: str,
        source_id: str,
        *,
        status: str,
        chunk_count: int = 0,
        error: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE space_source SET status = ?, chunk_count = ?, error = ?"
                " WHERE space_id = ? AND source_id = ?",
                (status, int(chunk_count), error, space_id, source_id),
            )

    _SOURCE_COLUMNS = (
        "source_id, kind, ref_id, title, url, language, status,"
        " chunk_count, byte_size, error, created_at"
    )

    def list_sources(self, space_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT {self._SOURCE_COLUMNS} FROM space_source"
                " WHERE space_id = ? ORDER BY created_at, source_id",
                (space_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_source(self, space_id: str, source_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                f"SELECT {self._SOURCE_COLUMNS} FROM space_source"
                " WHERE space_id = ? AND source_id = ?",
                (space_id, source_id),
            ).fetchone()
        return dict(row) if row else None

    def delete_source(self, space_id: str, source_id: str) -> bool:
        """Kaynagi ve parcalarini siler. Alanin kendisine dokunmaz."""
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM space_source WHERE space_id = ? AND source_id = ?",
                (space_id, source_id),
            )
            if not cursor.rowcount:
                return False
            self._delete_source_chunks(conn, space_id, source_id)
        return True

    @staticmethod
    def _delete_source_chunks(
        conn: sqlite3.Connection, space_id: str, source_id: str
    ) -> None:
        selection = "SELECT chunk_id FROM chunk WHERE space_id = ? AND source_id = ?"
        conn.execute(
            f"DELETE FROM chunk_embedding WHERE chunk_id IN ({selection})",
            (space_id, source_id),
        )
        conn.execute(
            f"DELETE FROM chunk_fts WHERE chunk_id IN ({selection})",
            (space_id, source_id),
        )
        conn.execute(
            "DELETE FROM chunk WHERE space_id = ? AND source_id = ?",
            (space_id, source_id),
        )

    # ---------------------------------------------------------------- parcalar

    def replace_chunks(self, space_id: str, source_id: str, drafts) -> int:
        """Bir kaynagin parcalarini SIFIRDAN yazar; yazilan sayiyi doner.

        "Ekle" degil "degistir": yeniden indeksleme (parcalama ayari degisti,
        transkript tazelendi) eskilerin YANINA degil YERINE yazmali. Aksi halde
        ayni icerik iki kez aranir ve baglami tekrarla doldurur.

        `search_text` BURADA uretiliyor: cagiranlarin bunu hatirlamasi
        gerekmemeli ve unutulan tek bir cagri yeri arama indeksini sessizce
        eksik birakirdi.
        """
        from src.utils.text_utils import search_key

        with self.connect() as conn:
            self._delete_source_chunks(conn, space_id, source_id)
            written = 0
            for draft in drafts:
                # `RETURNING`, `cursor.lastrowid` DEGIL: ikincisi SQLite'a ozgu
                # (psycopg2'de yok, Postgres uretilen anahtari `RETURNING` ile
                # veriyor). SQLite 3.35+ da `RETURNING` destekliyor, yani tek
                # yazim iki lehcede de calisiyor ve bu satir tasinabilirlik
                # seam'inin disinda kaliyor.
                cursor = conn.execute(
                    "INSERT INTO chunk (space_id, source_id, ordinal, text,"
                    " start_sec, end_sec, page) VALUES (?, ?, ?, ?, ?, ?, ?)"
                    " RETURNING chunk_id",
                    (
                        space_id,
                        source_id,
                        draft.ordinal,
                        draft.text,
                        draft.start_sec,
                        draft.end_sec,
                        draft.page,
                    ),
                )
                chunk_id = cursor.fetchone()["chunk_id"]
                conn.execute(
                    "INSERT INTO chunk_fts (search_text, chunk_id, space_id)"
                    " VALUES (?, ?, ?)",
                    (search_key(draft.text), chunk_id, space_id),
                )
                written += 1
        return written

    def count_chunks(self, space_id: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM chunk WHERE space_id = ?", (space_id,)
            ).fetchone()
        return int(row["n"]) if row else 0

    def list_source_chunks(self, space_id: str, source_id: str) -> list[dict[str, Any]]:
        """Belirli bir kaynaga ait parcalari sira ile dondurur."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT chunk_id, space_id, source_id, ordinal, text,
                       start_sec, end_sec, page
                FROM chunk
                WHERE space_id = ? AND source_id = ?
                ORDER BY ordinal ASC
                """,
                (space_id, source_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def opening_chunk_ids(self, space_id: str, per_source: int = 1) -> list[int]:
        """Her kaynagin ILK `per_source` parcasinin kimlikleri.

        DEFTER DUZEYINDE sorular icin ("bu defterde neler var", "ozetle").
        Benzerlik aramasi bu sorulari yanitlayamaz: cevap tek bir parcada degil,
        defterin BUTUNUNDE. Aranan sey en yakin parca degil, her kaynaktan bir
        TEMSILCI.

        Neden ILK parca: transkriptlerde ve dokumanlarda giris bolumu kaynagin
        ne hakkinda oldugunu soyleyen yerdir ("bugun size Zustand'i
        anlatacagim"). Ortadan alinan bir parca konunun ayrintisina girer ve
        kaynagi TEMSIL ETMEZ.

        Pencere fonksiyonu iki lehcede de calisiyor (SQLite 3.25+, Postgres);
        `ORDER BY ordinal` her kaynak icin bagimsiz.
        """
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT chunk_id FROM (
                    SELECT chunk_id, source_id, ordinal,
                           ROW_NUMBER() OVER (
                               PARTITION BY source_id ORDER BY ordinal
                           ) AS rank_in_source
                    FROM chunk WHERE space_id = ?
                ) ranked
                WHERE rank_in_source <= ?
                ORDER BY source_id, ordinal
                """,
                (space_id, max(1, per_source)),
            ).fetchall()
        return [int(row["chunk_id"]) for row in rows]

    def get_chunks(self, chunk_ids: list[int]) -> list[dict[str, Any]]:
        """Verilen kimliklerdeki parcalari kaynak bilgisiyle birlikte dondurur.

        Sira KORUNMUYOR (SQL `IN` sirasiz); cagiran taraf zaten kendi
        siralamasini uyguluyor.
        """
        if not chunk_ids:
            return []
        placeholders = ",".join("?" for _ in chunk_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT c.chunk_id, c.space_id, c.source_id, c.ordinal, c.text,
                       c.start_sec, c.end_sec, c.page,
                       s.title, s.url, s.kind
                FROM chunk c
                LEFT JOIN space_source s
                       ON s.space_id = c.space_id AND s.source_id = c.source_id
                WHERE c.chunk_id IN ({placeholders})
                """,
                list(chunk_ids),
            ).fetchall()
        return [dict(row) for row in rows]

    def search_chunks_fts(
        self, space_id: str, match_query: str, limit: int = 20
    ) -> list[tuple[int, float]]:
        """Leksik arama. `(chunk_id, skor)` listesi, EN IYIDEN kotuye.

        Skor sozlesmesi: BUYUK olan daha iyi. Iki veritabaninin ham skoru bu
        sozu kendiliginden vermiyor (FTS5 `bm25()` negatif, Postgres `ts_rank`
        pozitif); isaret cevirisi lehcede yapiliyor, bkz. `Dialect.fts_search`.

        Gecersiz FTS sorgusu (dengesiz tirnak, yalniz basina `AND`) burada
        istisna DEGIL bos sonuc: sorgu metnini kullanici yaziyor ve bir soru
        yuzunden 500 donmek yanlis olurdu. Cagiran taraf zaten tokenlardan
        guvenli bir sorgu kuruyor; bu ikinci savunma hatti.

        Ikinci hat iki asamali: lehce sorguyu kendi diline CEVIREMEZSE (`None`)
        veritabanina hic gidilmiyor, gidilip de hata alinirsa da yutuluyor.
        """
        if not match_query.strip():
            return []
        prepared = self._dialect.fts_search(space_id, match_query, max(1, limit))
        if prepared is None:
            _log.warning(
                "FTS sorgusu %s lehcesine cevrilemedi: %r",
                self._dialect.name,
                match_query,
            )
            return []
        statement, params = prepared
        try:
            with self.connect() as conn:
                rows = conn.execute(statement, params).fetchall()
        except self._dialect.query_errors as exc:
            _log.warning("FTS sorgusu calistirilamadi (%s): %r", exc, match_query)
            return []
        return [(int(row["chunk_id"]), float(row["score"])) for row in rows]

    # --------------------------------------------------------------- vektorler

    def chunks_missing_embeddings(
        self, space_id: str, model: str
    ) -> list[tuple[int, str]]:
        """Bu MODEL ile gomulmemis parcalar.

        Olcut yalnizca "kayit var mi" DEGIL, "AYNI modelle mi": farkli
        modellerin vektorleri arasinda kosinus benzerligi anlamsizdir. Model
        degistiginde eski satirlar gomulmemis sayilip tembel bicimde yeniden
        uretiliyor -- toplu bir goc adimina gerek kalmiyor.
        """
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT c.chunk_id, c.text FROM chunk c"
                " LEFT JOIN chunk_embedding e ON e.chunk_id = c.chunk_id"
                " WHERE c.space_id = ? AND (e.chunk_id IS NULL OR e.model != ?)"
                " ORDER BY c.chunk_id",
                (space_id, model),
            ).fetchall()
        return [(int(row["chunk_id"]), row["text"]) for row in rows]

    def embedded_chunk_counts(self, space_id: str, model: str) -> dict[str, int]:
        """Kaynak basina BU MODELLE gomulu parca sayisi.

        Neden SAKLANMIYOR da her seferinde sayiliyor: duzeltilmek istenen hata
        tam olarak "saklanan durum yalan soyluyordu" idi. `space_source.status`
        gomme basarisiz olsa da `indexed` kalir (bilerek -- leksik arama
        calisiyor ve is DUSMEMELI), dolayisiyla yanina saklanacak ikinci bir
        sayac da ayni sekilde bayatlardi. Canli sayim bu yalani YAPISAL olarak
        imkansiz kiliyor.

        Bedeli onemsiz: alan basina birkac bin satirda tek bir gruplu sayim, ve
        yalnizca defter acilirken calisiyor.

        Olcut MODELE BAGLI, `load_embeddings` ve `chunks_missing_embeddings` ile
        birebir ayni: farkli modellerin vektorleri arasinda kosinus benzerligi
        anlamsiz, yani baska modelle gomulmus bir parca erisim icin GOMULU
        DEGILDIR. Arayuzun bunu "tamam" gostermesi, kullaniciya calismayan bir
        aramayi calisiyor diye anlatmak olurdu.
        """
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT c.source_id AS source_id, COUNT(*) AS n FROM chunk c"
                " JOIN chunk_embedding e ON e.chunk_id = c.chunk_id"
                " WHERE c.space_id = ? AND e.model = ?"
                " GROUP BY c.source_id",
                (space_id, model),
            ).fetchall()
        return {row["source_id"]: int(row["n"]) for row in rows}

    def put_embeddings(self, rows: list[tuple[int, str, int, bytes]]) -> None:
        """`(chunk_id, model, dim, vector)` satirlarini yazar."""
        if not rows:
            return
        with self.connect() as conn:
            conn.executemany(
                "INSERT INTO chunk_embedding"
                " (chunk_id, model, dim, vector) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(chunk_id) DO UPDATE SET"
                " model = excluded.model, dim = excluded.dim, vector = excluded.vector",
                rows,
            )

    def load_embeddings(self, space_id: str, model: str) -> list[tuple[int, bytes]]:
        """Alanin bu modele ait tum vektorleri.

        `bytes(...)` bir LEHCE SIZINTISINI kapatiyor: psycopg2 `BYTEA`yi
        `memoryview` olarak veriyor, sqlite3 `bytes`. Bugunku tek okuyucu
        (`embedding_service.unpack_vector`) ikisini de kabul ediyor -- yani
        fark, ancak `.hex()` ya da bir karsilastirma yazan bir sonraki
        okuyucuda ve TEK bir veritabaninda ortaya cikardi. Imza `bytes`
        diyor; oyle olsun.
        """
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT e.chunk_id, e.vector FROM chunk_embedding e"
                " JOIN chunk c ON c.chunk_id = e.chunk_id"
                " WHERE c.space_id = ? AND e.model = ?"
                " ORDER BY e.chunk_id",
                (space_id, model),
            ).fetchall()
        return [(int(row["chunk_id"]), bytes(row["vector"])) for row in rows]


def _run_summary_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "run_id": row["run_id"],
        "user_id": row["user_id"],
        "topic": row["topic"],
        "filters": json.loads(row["filters_json"]),
        "created_at": row["created_at"],
        "is_complete": bool(row["is_complete"]),
    }
