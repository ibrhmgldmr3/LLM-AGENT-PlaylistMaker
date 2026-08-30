from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values, find_dotenv, load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


# Vite gelistirme sunucusu. `CORS_ALLOW_ORIGINS` tanimsizken kullanilan
# varsayilan; uretimde arayuz ayni surecten servis edildigi icin CORS'a gerek
# kalmiyor (bkz. `api/main._mount_frontend`).
DEV_CORS_ORIGINS: tuple[str, ...] = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


# Ortam degiskeni -> model alani eslemesi.
# Varsayilan degerler SADECE modelde tanimlidir; burada tekrarlanmaz.
ENV_TO_FIELD: dict[str, str] = {
    "LLM_PROVIDER": "llm_provider",
    "GEMINI_API_KEY": "gemini_api_key",
    "TOGETHER_API_KEY": "together_api_key",
    "TOGETHER_MODEL": "together_model",
    "OPENROUTER_API_KEY": "openrouter_api_key",
    "OPENROUTER_MODEL": "openrouter_model",
    "GEMINI_MODEL": "gemini_model",
    "GEMINI_THINKING_BUDGET": "gemini_thinking_budget",
    "YOUTUBE_DATA_API_KEY": "youtube_data_api_key",
    "YOUTUBE_OAUTH_CLIENT_SECRET_FILE": "youtube_oauth_client_secret_file",
    "YOUTUBE_OAUTH_CLIENT_ID": "youtube_oauth_client_id",
    "YOUTUBE_OAUTH_CLIENT_SECRET": "youtube_oauth_client_secret",
    "YOUTUBE_OAUTH_TOKEN_FILE": "youtube_oauth_token_file",
    "YOUTUBE_OAUTH_ALLOW_LOCAL_SERVER": "youtube_oauth_allow_local_server",
    "YOUTUBE_PLAYLIST_PRIVACY_STATUS": "youtube_playlist_privacy_status",
    "ASR_BACKEND": "asr_backend",
    "FASTER_WHISPER_MODEL_SIZE": "faster_whisper_model_size",
    "FASTER_WHISPER_DEVICE": "faster_whisper_device",
    "FASTER_WHISPER_COMPUTE_TYPE": "faster_whisper_compute_type",
    "FASTER_WHISPER_BEAM_SIZE": "faster_whisper_beam_size",
    "FASTER_WHISPER_CPU_THREADS": "faster_whisper_cpu_threads",
    "FASTER_WHISPER_NUM_WORKERS": "faster_whisper_num_workers",
    "WHISPER_CPP_CLI_PATH": "whisper_cpp_cli_path",
    "WHISPER_CPP_MODEL_PATH": "whisper_cpp_model_path",
    "WHISPER_CPP_TIMEOUT_SEC": "whisper_cpp_timeout_sec",
    "FASTER_WHISPER_TIMEOUT_SEC": "faster_whisper_timeout_sec",
    "FFMPEG_PATH": "ffmpeg_path",
    "YTDLP_PROXY": "ytdlp_proxy",
    "YTDLP_COOKIES_FROM_BROWSER": "ytdlp_cookies_from_browser",
    "YTDLP_COOKIES_FILE": "ytdlp_cookies_file",
    "YTDLP_JS_RUNTIME": "ytdlp_js_runtime",
    "ALLOW_UNSAFE_OPENMP_WORKAROUND": "allow_unsafe_openmp_workaround",
    "DATA_DIR": "data_dir",
    "SQLITE_PATH": "sqlite_path",
    "DATABASE_URL": "database_url",
    "SECRET_ENCRYPTION_KEY": "secret_encryption_key",
    "JOB_BACKEND": "job_backend",
    "REDIS_URL": "redis_url",
    "JOB_KEY_PREFIX": "job_key_prefix",
    "JOB_TTL_SEC": "job_ttl_sec",
    "AUTH_MODE": "auth_mode",
    "CORS_ALLOW_ORIGINS": "cors_allow_origins",
    "SESSION_TTL_SEC": "session_ttl_sec",
    "MAX_RUNS_PER_USER_PER_DAY": "max_runs_per_user_per_day",
    "ADMIN_USER_IDS": "admin_user_ids",
    "YOUTUBE_DAILY_QUOTA_UNITS": "youtube_daily_quota_units",
    "MAX_UNITS_PER_DAY": "max_units_per_day",
    "INCLUDE_ENGLISH_BY_DEFAULT": "include_english_by_default",
    "MAX_SUBTOPICS": "max_subtopics",
    "SEARCH_CANDIDATES_PER_SUBTOPIC": "search_candidates_per_subtopic",
    "METADATA_TOP_K": "metadata_top_k",
    "TRANSCRIPT_ENRICHMENT_TOP_K": "transcript_enrichment_top_k",
    "ENABLE_ASR_FALLBACK": "enable_asr_fallback",
    "MAX_ASR_VIDEOS_PER_RUN": "max_asr_videos_per_run",
    "MAX_ASR_VIDEO_DURATION_SEC": "max_asr_video_duration_sec",
    "MAX_ASR_SECONDS_PER_RUN": "max_asr_seconds_per_run",
    "ENABLE_STUDY_NOTES": "enable_study_notes",
    "STUDY_NOTE_TRANSCRIPT_CHAR_LIMIT": "study_note_transcript_char_limit",
    "CHANNEL_REPEAT_PENALTY": "channel_repeat_penalty",
    "MAX_SEARCH_WORKERS": "max_search_workers",
    "MAX_TRANSCRIPT_WORKERS": "max_transcript_workers",
    "MAX_ASR_WORKERS": "max_asr_workers",
    "REQUEST_TIMEOUT_SEC": "request_timeout_sec",
    "RETRY_MAX_ATTEMPTS": "retry_max_attempts",
    "RETRY_BASE_DELAY_SEC": "retry_base_delay_sec",
    "SEARCH_CACHE_TTL_SEC": "search_cache_ttl_sec",
    "TRANSCRIPT_CACHE_TTL_SEC": "transcript_cache_ttl_sec",
    "FAILURE_CACHE_TTL_SEC": "failure_cache_ttl_sec",
    "PROVIDER_COOLDOWN_SEC": "provider_cooldown_sec",
    "RATE_LIMIT_COOLDOWN_SEC": "rate_limit_cooldown_sec",
    "PROVIDER_FAILURE_THRESHOLD": "provider_failure_threshold",
    # RAG / ogrenme alani
    "ENABLE_RAG": "enable_rag",
    "EMBEDDING_PROVIDER": "embedding_provider",
    "GEMINI_EMBEDDING_MODEL": "gemini_embedding_model",
    "TOGETHER_EMBEDDING_MODEL": "together_embedding_model",
    "EMBEDDING_BATCH_SIZE": "embedding_batch_size",
    "RAG_CHUNK_CHARS": "rag_chunk_chars",
    "RAG_CHUNK_OVERLAP_CHARS": "rag_chunk_overlap_chars",
    "RAG_TOP_K": "rag_top_k",
    "RAG_MAX_CHUNKS_PER_SOURCE": "rag_max_chunks_per_source",
    "RAG_MIN_SIMILARITY": "rag_min_similarity",
    "RAG_CONTEXT_CHAR_LIMIT": "rag_context_char_limit",
    "MAX_SPACES_PER_USER": "max_spaces_per_user",
    "MAX_DOCUMENTS_PER_SPACE": "max_documents_per_space",
    "MAX_UPLOAD_BYTES": "max_upload_bytes",
}


class UserCredentials(BaseModel):
    """KULLANICIYA ait sirlar.

    ARTIK NEREDEYSE BOS: LLM (Gemini/Together) ve YouTube Data API anahtarlari
    PAYLASIMLI sunucu anahtarlarina tasindi (bkz. `ServerConfig`) -- kullanici
    hicbir anahtar girmiyor, tum sorgular sunucunun kendi Together.ai/YouTube
    anahtariyla yapiliyor. Bu sinif yalnizca gercekten kullaniciya ozgu kalan
    (ve bugun API'den hic duzenlenemeyen) alanlari tutuyor.

    Maliyet/kota paylasimli oldugu icin kotuye kullanimi sinirlamak
    `ServerConfig.max_runs_per_user_per_day` ile yapiliyor, BYOK ile degil.

    Bu degerler ASLA API yanitinda donmemeli.
    """

    model_config = ConfigDict(extra="ignore")

    youtube_oauth_token_file: str = Field(default="data/cache/youtube_oauth_token.json")
    ytdlp_proxy: str | None = Field(default=None)
    ytdlp_cookies_from_browser: str | None = Field(default=None)
    ytdlp_cookies_file: str | None = Field(default=None)


class RunOptions(BaseModel):
    """Calistirma basina degisebilen secenekler.

    Streamlit'te kenar cubugu, API'de istek govdesi. Sunucu ayari DEGIL:
    iki kullanici ayni sunucuda farkli degerlerle calistirabilmeli.
    """

    model_config = ConfigDict(extra="ignore")

    gemini_model: str = Field(default="gemini-3.7-flash")
    # Together model kimligi. Hesaba gore degisebildigi icin yapilandirilabilir;
    # varsayilanin sizin hesabinizda kullanilabilir oldugunu dogrulayin.
    together_model: str = Field(default="meta-llama/Llama-3.3-70B-Instruct-Turbo")
    # OpenRouter model kimligi "<saglayici>/<model>" biciminde (ornegin
    # "openai/gpt-4o-mini"). Hesapta hangi modellerin acik oldugu OpenRouter
    # panelinden dogrulanmali.
    openrouter_model: str = Field(default="openai/gpt-4o-mini")
    youtube_playlist_privacy_status: str = Field(default="private")
    # Ana dil disinda Ingilizce arama da yapilsin mi (arayuzdeki anahtarin
    # varsayilani). Alt konu basina bir ek `search.list` cagrisi = +100 kota birimi.
    include_english_by_default: bool = Field(default=True)
    max_subtopics: int = Field(default=6)
    search_candidates_per_subtopic: int = Field(default=12)
    metadata_top_k: int = Field(default=4)
    transcript_enrichment_top_k: int = Field(default=2)
    # VARSAYILAN KAPALI. ASR bir videoyu indirip Whisper'dan gecirir; olculen maliyet
    # 11 dakikalik bir video icin ~90-120 sn (CPU, base model). Ilk iki transkript
    # saglayicisi vakalarin buyuk cogunlugunu zaten karsiliyor ve transkriptin
    # siralamaya katkisi en fazla +1.0 puan. Arayuzden acilabilir.
    enable_asr_fallback: bool = Field(default=False)
    max_asr_videos_per_run: int = Field(default=2)
    # ADET tavani maliyeti sinirlamak icin TEK BASINA yetmiyor: ASR'in bedeli
    # video SURESIYLE orantili. "En fazla 2 video" kurali, 3 saatlik iki dersi
    # de kabul ediyordu. Asagidaki iki tavan ayni sorunun iki yuzu:
    #   - video basina: tek bir uzun videonun butun kotayi yemesini engeller
    #   - calistirma basina: toplam hesaplama butcesini bagler
    # 0 = sinirsiz (ikisi de).
    max_asr_video_duration_sec: int = Field(default=3600)
    max_asr_seconds_per_run: int = Field(default=7200)
    # VARSAYILAN KAPALI. Transkripti olan her secilen video icin BIR EK LLM
    # cagrisi demek -- olculdu: transkript ortancasi ~16.000 karakter (~4.000
    # token), 6 alt basliklik bir playlist icin toplam girdi ~24.000 token.
    # Mevcut tek cagriya (~83 token) kiyasla maliyeti belirgin sekilde buyutuyor.
    enable_study_notes: bool = Field(default=False)
    # Transkriptten modele gecirilen ust sinir. Olculen transkriptlerin
    # cogu (ortanca ~16.000 krkt) bunun altinda kalip BUTUN transkript
    # geciyor; yalnizca en uzunlar (gorulen tavan ~50.000 krkt) kirpiliyor.
    # RAG DEGIL: dogrudan baglam, cunku tek bir transkript baglam penceresine
    # rahatca sigiyor -- parcalama/vektor depo gereksiz karmasiklik olurdu.
    study_note_transcript_char_limit: int = Field(default=24_000, ge=1_000)
    # Ayni kanaldan tekrar secim yapmanin bedeli. Kucuk tutuldugu icin yalnizca
    # yakin skorlu adaylarda belirleyici olur; playlist'in tek kanala saplanmasini
    # engeller ama acikca daha iyi bir videoyu elemez.
    channel_repeat_penalty: float = Field(default=0.6)

    @field_validator("youtube_playlist_privacy_status")
    @classmethod
    def validate_privacy_status(cls, value: str) -> str:
        allowed = {"private", "unlisted", "public"}
        if value not in allowed:
            raise ValueError(f"YOUTUBE_PLAYLIST_PRIVACY_STATUS must be one of {sorted(allowed)}")
        return value

    @field_validator("max_subtopics")
    @classmethod
    def validate_max_subtopics(cls, value: int) -> int:
        if not 1 <= value <= 20:
            raise ValueError("MAX_SUBTOPICS must be between 1 and 20")
        return value

    @field_validator("search_candidates_per_subtopic")
    @classmethod
    def validate_search_candidates_per_subtopic(cls, value: int) -> int:
        if not 10 <= value <= 50:
            raise ValueError("SEARCH_CANDIDATES_PER_SUBTOPIC must be between 10 and 50")
        return value

    @field_validator("metadata_top_k")
    @classmethod
    def validate_metadata_top_k(cls, value: int) -> int:
        if not 1 <= value <= 10:
            raise ValueError("METADATA_TOP_K must be between 1 and 10")
        return value

    @field_validator("transcript_enrichment_top_k")
    @classmethod
    def validate_enrichment_top_k(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("TRANSCRIPT_ENRICHMENT_TOP_K must be greater than 0")
        return value

    @field_validator(
        "max_asr_videos_per_run",
        "max_asr_video_duration_sec",
        "max_asr_seconds_per_run",
    )
    @classmethod
    def validate_max_asr_videos(cls, value: int) -> int:
        # 0 ANLAMLI bir deger: "sinirsiz". Negatif degil.
        if value < 0:
            raise ValueError("ASR limitleri sıfır veya daha büyük olmalı")
        return value


class ServerConfig(BaseModel):
    """Kurulumun tamamina ait ayarlar; `.env`'de kalir.

    Yollar, zaman asimlari, worker sayilari, ASR arka uclari, onbellek TTL'leri.
    Kullaniciya gore degismez.
    """

    model_config = ConfigDict(extra="ignore")

    # PAYLASIMLI LLM anahtari: tum kullanicilarin sorgulari BU anahtarla
    # gidiyor, kullanici kendi anahtarini girmiyor. Hangi saglayicinin
    # kullanilacagini da bu belirliyor (kullaniciya ozgu degil).
    llm_provider: Literal["gemini", "together", "openrouter"] = Field(default="gemini")
    gemini_api_key: str | None = Field(default=None, description="Gemini API key")
    together_api_key: str | None = Field(default=None, description="Together.ai API key")
    openrouter_api_key: str | None = Field(default=None, description="OpenRouter API key")
    # PAYLASIMLI YouTube Data API anahtari (arama icin). Kota PROJE basina
    # gunde 10.000 birim ve bir calistirma ~1.200 birim tuketiyor -- yani
    # paylasimli anahtarla TUM kullanicilar icin toplam ~8 calistirma/gun.
    # Bu yuzden `max_runs_per_user_per_day` ile kullanici basina sinirlamak
    # sart (asagida).
    youtube_data_api_key: str | None = Field(default=None)

    # Negatif = ayari hic gonderme (VARSAYILAN). 0 = "thinking" kapali.
    # Olcumde `thinking_budget=0` kabul eden modelde hiz kazandirmadi, kabul
    # etmeyen modelleri ise 400 ile tamamen kirdi.
    gemini_thinking_budget: int = Field(default=-1)
    youtube_oauth_allow_local_server: bool = Field(default=True)

    asr_backend: str = Field(default="auto")
    faster_whisper_model_size: str = Field(default="base")
    faster_whisper_device: str = Field(default="cpu")
    faster_whisper_compute_type: str = Field(default="int8")
    faster_whisper_beam_size: int = Field(default=1)
    # A single ASR task may use all CPU cores. It is deliberately independent
    # from transcript HTTP concurrency, which can safely be higher.
    faster_whisper_cpu_threads: int = Field(default=0)
    faster_whisper_num_workers: int = Field(default=1)
    whisper_cpp_cli_path: str | None = Field(default=None)
    whisper_cpp_model_path: str | None = Field(default=None)
    whisper_cpp_timeout_sec: int = Field(default=1800)
    # faster-whisper'in KENDI zaman asimi. whisper.cpp ayri bir surec oldugu
    # icin `subprocess timeout` ile sinirlanabiliyordu; faster-whisper surec
    # icinde calisiyor ve hicbir tavani yoktu -- yani `auto` modun VARSAYILAN
    # arka ucu sinirsizdi. Asili kalan tek bir is transkript worker'ini
    # suresiz blokluyor, calistirma hic ilerlemiyordu.
    faster_whisper_timeout_sec: int = Field(default=1800)
    ffmpeg_path: str | None = Field(default=None)
    ytdlp_js_runtime: str | None = Field(default=None)
    allow_unsafe_openmp_workaround: bool = Field(default=True)

    data_dir: str = Field(default="data")
    sqlite_path: str = Field(default="data/cache/app.db")
    # Tanimliysa depo SQLite yerine Postgres kullanir ve `sqlite_path`
    # yoksayilir. Yatay olceklendirmenin son adimi: SQLite tek bir DOSYA ve
    # makineler arasi paylasilamiyor (WAL, ag dosya sistemlerinin guvenilir
    # bicimde saglamadigi kilitlemeye dayaniyor). Bkz. `src/storage/factory.py`.
    database_url: str | None = Field(default=None)
    # Saklanan sirlari (OAuth jetonlari) sifrelemek icin kullanilir. Tanimsizsa
    # jetonlar duz metin yazilir. `python -m src.storage.crypto` ile uretilebilir.
    secret_encryption_key: str | None = Field(default=None)

    # `single_user` (VARSAYILAN): her istek `DEFAULT_USER_ID`'ye ait, oturum
    # cerezi yok. Bugunku kurulum ve gelistirme akisi boyle calisiyor.
    #
    # `multi_user`: istek bir oturum cerezi tasimali (Google girisi). LLM ve
    # YouTube arama anahtarlari HER IKI MODDA DA `.env`'den, PAYLASIMLI olarak
    # okunur -- kullanici hicbir anahtar girmiyor. `multi_user`'in tek farki
    # kim oldugunu bilmek (gunluk sinir + yayinlama icin kisisel YouTube OAuth
    # izni); anahtar yonetimiyle ilgisi yok.
    # Isleri nerede yurutecegimiz.
    #
    # `memory` (VARSAYILAN): `InProcessJobRunner`. Is durumu bellekte, yani
    # uygulama TEK SUREC calismak zorunda -- ikinci surece dusen istek isi
    # tanimaz. Tek instance icin yeterli ve hicbir altyapi gerektirmiyor.
    #
    # `redis`: is web surecinde degil ayri bir worker'da calisir
    # (`python -m src.jobs.worker`). Web tarafi coklu replika olabilir ve ASR
    # gibi islemci yogun isler web CPU'sunu yemez.
    job_backend: Literal["memory", "redis"] = Field(default="memory")
    redis_url: str = Field(default="redis://localhost:6379/0")
    job_key_prefix: str = Field(default="map")
    # Bitmis islerin okunabilir kalma suresi. `InProcessJobRunner` son 100 isi
    # tutuyordu; paylasimli depoda sayiya gore budamak yaris uretir.
    job_ttl_sec: int = Field(default=24 * 3600, ge=60)

    auth_mode: Literal["single_user", "multi_user"] = Field(default="single_user")

    # Tarayicidan cerezle istek atmasina izin verilen kokenler; virgulle ayrik.
    #
    # Tanimsizsa YALNIZCA Vite gelistirme sunucusu kabul edilir. Uretimde
    # arayuz ayni surecten servis edildigi icin (bkz. `api/main._mount_frontend`)
    # CORS'a hic gerek kalmaz ve bos birakmak DOGRU varsayilandir. Ayri bir
    # alan adindan servis edilecekse burasi tek dugme -- eskiden kod icinde
    # sabitti ve o kurulumun hicbir cikis yolu yoktu.
    #
    # `*` BILEREK desteklenmiyor: `allow_credentials=True` ile birlikte
    # tarayicilar zaten reddediyor ve oturum cerezi tasiyan bu API'de joker
    # koken, herhangi bir sitenin kullanici adina istek atmasi demek olurdu.
    cors_allow_origins: str | None = Field(default=None)

    # OAuth ISTEMCISI kuruluma ait, kullaniciya degil: uygulamanin Google'a
    # kayitli kimligi bu. Kullanici basina olsaydi herkesin kendi Google Cloud
    # OAuth istemcisini acip kendi yonlendirme adresini eklemesi gerekirdi.
    # Kullanicidan gelen sey JETON (yayin izni), istemci degil.
    youtube_oauth_client_secret_file: str | None = Field(default=None)
    youtube_oauth_client_id: str | None = Field(default=None)
    youtube_oauth_client_secret: str | None = Field(default=None)

    # Oturum omru. Varsayilan 14 gun: kullaniciyi her gun Google'a geri
    # gondermeyecek kadar uzun, calinan bir cerezin suresiz gecerli olmayacagi
    # kadar kisa.
    session_ttl_sec: int = Field(default=14 * 24 * 3600, ge=300)

    # Kullanici basina 24 saatlik calistirma siniri. 0 = SINIRSIZ (varsayilan).
    #
    # LLM ve YouTube anahtarlari artik PAYLASIMLI (BYOK yok), yani maliyet/kota
    # sunucu sahibine biniyor: sinir olmadan tek bir kullanici gunluk YouTube
    # kotasinin tamamini tuketebilir (kota proje basina 10.000 birim, bir
    # calistirma ~612-1200 birim -- yani ~8-16 calistirma TUM kullanicilar
    # icin toplam). `multi_user` kurulumlari icin `.env.example` bunu 3 olarak
    # ONERIR; varsayilan burada 0 kaliyor ki mevcut `single_user` kurulumlarin
    # davranisi sessizce degismesin.
    max_runs_per_user_per_day: int = Field(default=0, ge=0)

    # Kullanim raporunu (`GET /api/admin/usage`) gorebilecek kullanicilar.
    # Virgulle ayrilmis kimlik listesi, ornegin `google:1234,google:5678`.
    #
    # `single_user` modda ONEMSIZ: zaten tek kullanici var ve o da sunucunun
    # sahibi, dolayisiyla rapor her zaman acik. Liste yalnizca `multi_user`
    # modda anlam kazaniyor ve orada BOS BIRAKILIRSA rapor hic kimseye
    # acilmiyor -- "yapilandirmayi unutan herkese acik kalsin" yanlis varsayilan
    # olurdu (rapor kullanici kimliklerini ve kullanim aliskanligini gosteriyor).
    admin_user_ids: str | None = Field(default=None)

    # Google Cloud Console'dan kota arttirildiysa buradan bildirilir. `None` ise
    # YouTube'un varsayilani (10.000 birim/gun) kabul edilir; sayi tek yerde,
    # `src/providers/youtube_data_api_provider.py` icinde tanimli.
    youtube_daily_quota_units: int | None = Field(default=None, ge=1)

    # SERVIS GENELI gunluk kota tavani (birim). Kullanici basina sinirin
    # (`max_runs_per_user_per_day`) yaninda, TOPLAM tuketimi sinirlar.
    #
    # `None` (varsayilan) = tavan `youtube_daily_quota_units`, yani projenin
    # kendi kotasi. Burasi BILEREK "sinirsiz" degil: gercek tavan zaten var ve
    # ona carpmanin bedeli YouTube'dan 403 alip calistirmanin YARIDA olmesi --
    # LLM cagrisi da bosa gider. Onceden reddetmek her durumda daha iyi.
    #
    # Daha DUSUK bir deger vermek anlamli: gunun bir kismini kendine ya da
    # beklenmedik yuke pay birakmak icin.
    max_units_per_day: int | None = Field(default=None, ge=1)

    def daily_unit_budget(self) -> int:
        """Bugun harcanabilecek toplam kota birimi."""
        from src.providers.youtube_data_api_provider import DEFAULT_DAILY_QUOTA_UNITS

        project_quota = self.youtube_daily_quota_units or DEFAULT_DAILY_QUOTA_UNITS
        return min(self.max_units_per_day or project_quota, project_quota)

    def admin_ids(self) -> set[str]:
        """`admin_user_ids` alanini kume olarak verir. Bos/bosluk girdiler elenir."""
        if not self.admin_user_ids:
            return set()
        return {part.strip() for part in self.admin_user_ids.split(",") if part.strip()}

    def cors_origins(self) -> list[str]:
        """CORS icin izin verilen kokenler. Tanimsizsa gelistirme kokenleri.

        `*` ELENIYOR (sessizce degil -- `cors_wildcard_rejected` ile bildiriliyor):
        `allow_credentials=True` ile joker koken tarayicilarca zaten reddedilir,
        ama daha onemlisi oturum cerezi tasiyan bu API'de o ayar herhangi bir
        sitenin kullanici adina istek atmasi anlamina gelirdi.
        """
        if not self.cors_allow_origins:
            return list(DEV_CORS_ORIGINS)
        origins = [part.strip() for part in self.cors_allow_origins.split(",") if part.strip()]
        return [origin for origin in origins if origin != "*"]

    def cors_wildcard_rejected(self) -> bool:
        """`*` yazilmis ve elenmis mi. Cagiran taraf bunu gorunur kilmali."""
        if not self.cors_allow_origins:
            return False
        return any(part.strip() == "*" for part in self.cors_allow_origins.split(","))

    # ----------------------------------------------------------------- RAG
    #
    # VARSAYILAN KAPALI -- `enable_study_notes` ile ayni gerekce: her soru bir
    # embedding + bir uretim cagrisi demek ve anahtarlar PAYLASIMLI, yani
    # fatura sunucu sahibinde (bkz. docs/react-migration-plan.md §2).

    enable_rag: bool = Field(default=False)

    # Bos = `llm_provider`i izle. Ayri tutulabilmesinin sebebi: bir saglayicinin
    # uretim modeli tercih edilirken embedding modeli digerininki olabilir
    # (fiyat, boyut, dil destegi ayri ayri degerlendiriliyor).
    embedding_provider: Literal["gemini", "together"] | None = Field(default=None)
    # OLCULDU: `text-embedding-004` bu API surumunde ARTIK YOK (404
    # NOT_FOUND). `gemini-embedding-001` bugunun kararli modeli.
    #
    # Uretim tarafindaki gibi bir YEDEK MODEL LISTESI bilerek YOK: bir alanin
    # yarisi bir modelle yarisi digeriyle gomulurse aralarindaki kosinus
    # benzerligi anlamsizlasir ve arama SESSIZCE bozulur. Model bulunamazsa
    # hata yukseliyor, sessizce baskasina dusulmuyor.
    gemini_embedding_model: str = Field(default="gemini-embedding-001")
    # Cok dilli olmasi Turkce icin BELIRLEYICI: yalnizca Ingilizce egitilmis bir
    # embedding modelinde Turkce soru ile Turkce transkript arasindaki benzerlik
    # gurultuye gomuluyor.
    together_embedding_model: str = Field(default="BAAI/bge-m3")
    embedding_batch_size: int = Field(default=64, ge=1, le=512)

    rag_chunk_chars: int = Field(default=1200, ge=200)
    rag_chunk_overlap_chars: int = Field(default=200, ge=0)
    # Baglama giren parca sayisi ve kaynak cesitliligi tavani. Ikincisi tek bir
    # uzun videonun baglami tamamen doldurmasini engelliyor -- ayni gerekce
    # `channel_repeat_penalty`de.
    rag_top_k: int = Field(default=8, ge=1, le=50)
    rag_max_chunks_per_source: int = Field(default=3, ge=1, le=20)
    # KACINMA ESIGI (bkz. `rag_service` 1. kapi). En iyi aday bunun altindaysa
    # ve leksik eslesme de yoksa LLM HIC CAGRILMIYOR, "bulamadim" donuyor.
    #
    # OLCULDU (`gemini-embedding-001`, 3 parca x 7 soru):
    #   ilgili sorular   : 0.804 - 0.852
    #   alakasiz sorular : 0.496 - 0.542
    # Ilk deger 0.55 idi ve alakasiz tavanina yalnizca 0.008 kaliyordu -- yani
    # kapi pratikte hic kapanmiyordu. 0.70, iki kumenin arasindaki 0.26'lik
    # bosluga her iki yandan paylı oturuyor.
    #
    # DIKKAT, deger MODELE OZGU: Gemini embedding modellerinde iki alakasiz
    # metnin kosinusu bile ~0.5 tabaninda kaliyor (vektorler dar bir koni
    # icinde). Bastan tahmin edilen "0.55 makul gorunuyor" degeri tam da bu
    # yuzden yanlisti. Baska bir modele gecilirse (ornegin `BAAI/bge-m3`) bu
    # sayi YENIDEN olculmeli; `rag_service` esigin altinda kalip reddedilen
    # sorgularin en iyi skorunu tam da bunun icin logluyor.
    rag_min_similarity: float = Field(default=0.70, ge=0.0, le=1.0)
    rag_context_char_limit: int = Field(default=12_000, ge=1_000)

    # Depolama ve embedding maliyeti sunucu sahibinde; tavanlar bu yuzden var.
    max_spaces_per_user: int = Field(default=10, ge=1)
    max_documents_per_space: int = Field(default=25, ge=1)
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1024)

    def effective_embedding_provider(self) -> str:
        """Embedding hangi saglayicidan alinacak.

        OpenRouter'in embedding destegi modele gore degisiyor ve cogu modelde
        YOK; bu yuzden `llm_provider=openrouter` iken embedding otomatik olarak
        Gemini'ye (anahtari yoksa Together'a) dusuyor. Acik secim icin
        `EMBEDDING_PROVIDER` hala baskin.
        """
        if self.embedding_provider:
            return self.embedding_provider
        if self.llm_provider in ("gemini", "together"):
            return self.llm_provider
        return "gemini" if self.gemini_api_key else "together"

    def embedding_model(self) -> str:
        """SECILEN embedding saglayicisinin model kimligi.

        Model adi vektorlerle birlikte saklaniyor (`chunk_embedding.model`):
        farkli modellerin vektorleri arasinda kosinus benzerligi anlamsizdir ve
        model degistiginde eski satirlar yeniden uretilmeli.
        """
        if self.effective_embedding_provider() == "together":
            return self.together_embedding_model
        return self.gemini_embedding_model

    max_search_workers: int = Field(default=4)
    max_transcript_workers: int = Field(default=4)
    max_asr_workers: int = Field(default=1)
    request_timeout_sec: int = Field(default=30)
    retry_max_attempts: int = Field(default=3)
    retry_base_delay_sec: float = Field(default=1.0)

    search_cache_ttl_sec: int = Field(default=21600)
    transcript_cache_ttl_sec: int = Field(default=2592000)
    failure_cache_ttl_sec: int = Field(default=900)
    provider_cooldown_sec: int = Field(default=900)
    # HTTP 429 / IP blogu icin ayri (ve daha uzun) dinlenme suresi. YouTube'un
    # hiz sinirlari dakikalarca surer; 15 dk sonra tekrar denemek bogmaya devam eder.
    rate_limit_cooldown_sec: int = Field(default=1800)
    provider_failure_threshold: int = Field(default=3)

    @field_validator("asr_backend")
    @classmethod
    def validate_asr_backend_server(cls, value: str) -> str:
        allowed = {"auto", "faster-whisper", "whisper.cpp"}
        if value not in allowed:
            raise ValueError(f"ASR_BACKEND must be one of {sorted(allowed)}")
        return value

    @field_validator(
        "request_timeout_sec",
        "retry_max_attempts",
        "whisper_cpp_timeout_sec",
        "faster_whisper_timeout_sec",
        "provider_failure_threshold",
        "max_search_workers",
        "max_transcript_workers",
        "max_asr_workers",
        "faster_whisper_beam_size",
        "faster_whisper_num_workers",
    )
    @classmethod
    def validate_positive_ints_server(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Value must be greater than 0")
        return value

    @field_validator("faster_whisper_cpu_threads")
    @classmethod
    def validate_faster_whisper_cpu_threads(cls, value: int) -> int:
        # faster-whisper uses 0 to mean its own default (all available cores).
        if value < 0:
            raise ValueError("FASTER_WHISPER_CPU_THREADS must be zero or greater")
        return value

    @field_validator(
        "search_cache_ttl_sec",
        "transcript_cache_ttl_sec",
        "failure_cache_ttl_sec",
        "provider_cooldown_sec",
        "rate_limit_cooldown_sec",
    )
    @classmethod
    def validate_non_negative_ints_server(cls, value: int) -> int:
        if value < 0:
            raise ValueError("Value must be zero or greater")
        return value

    @field_validator("retry_base_delay_sec")
    @classmethod
    def validate_retry_base_delay_sec(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("RETRY_BASE_DELAY_SEC must be greater than 0")
        return value


class AppConfig(ServerConfig, UserCredentials, RunOptions):
    """Servislere gecirilen BIRLESIK gorunum.

    Uc kaynagin (sunucu ayari + kullanici sirri + calistirma secenegi) tek bir
    nesnede birlesmis hali. Servis ve saglayici katmani bu ayrimi hic gormez —
    tek bir config alir, bugunku gibi.

    Cok kullanicili moda gecildiginde `compose()` her istek icin farkli bir
    kullanici/secenek bilesimiyle cagrilir; `.env` yalnizca sunucu ayarlarini
    besler.
    """

    model_config = ConfigDict(extra="ignore")

    # load_config tarafindan doldurulur; kullaniciya gosterilecek yapilandirma uyarilari.
    config_warnings: list[str] = Field(default_factory=list)

    @classmethod
    def compose(
        cls,
        server: ServerConfig,
        credentials: UserCredentials,
        options: RunOptions,
        config_warnings: list[str] | None = None,
    ) -> "AppConfig":
        """Uc kaynagi tek bir calisma yapilandirmasina birlestirir."""
        return cls.model_validate(
            {
                **server.model_dump(),
                **credentials.model_dump(),
                **options.model_dump(),
                "config_warnings": config_warnings or [],
            }
        )

    def server_config(self) -> ServerConfig:
        return ServerConfig.model_validate(self.model_dump())

    def credentials(self) -> UserCredentials:
        return UserCredentials.model_validate(self.model_dump())

    def run_options(self) -> RunOptions:
        return RunOptions.model_validate(self.model_dump())

    def llm_api_key(self) -> str | None:
        """SECILEN saglayicinin anahtari.

        Anahtar secimini tek bir yerde topluyor; cagiran taraflarin hangi
        saglayicinin hangi alani kullandigini bilmesi gerekmiyor.
        """
        if self.llm_provider == "together":
            return self.together_api_key
        if self.llm_provider == "openrouter":
            return self.openrouter_api_key
        return self.gemini_api_key

    def public_capabilities(self) -> dict[str, bool]:
        """Arayuzun hangi kontrolleri acabilecegini anlatir. SIR ICERMEZ.

        `GET /api/config` bunu doner; anahtarlarin kendisi asla disari cikmaz.
        """
        return {
            # `gemini_configured` GERIYE DONUK UYUM icin duruyor: arayuz ve
            # testler onu okuyor. Saglayici secilebilir hale geldigi icin asil
            # soru "LLM yapilandirilmis mi" ve onu `llm_configured` yanitliyor.
            "gemini_configured": bool(self.gemini_api_key),
            "llm_configured": bool(self.llm_api_key()),
            "youtube_search_configured": bool(self.youtube_data_api_key),
            "youtube_publish_configured": bool(
                self.youtube_oauth_client_secret_file
                or (self.youtube_oauth_client_id and self.youtube_oauth_client_secret)
            ),
            "asr_available": self.asr_backend_available(),
            "cookies_configured": bool(self.ytdlp_cookies_from_browser or self.ytdlp_cookies_file),
            # RAG hem ACIK hem de bir LLM anahtari kurulu olmali: gomme ve
            # yanit uretimi ayni saglayicidan geliyor. Ikisinden biri eksikse
            # arayuz "Ogren" sekmesini hic acmamali -- acip her soruda hata
            # gostermek kullaniciya ozelligin BOZUK oldugunu dusundururdu,
            # oysa yalnizca yapilandirilmamis.
            "rag_available": bool(self.enable_rag and self.llm_api_key()),
        }

    def asr_backend_available(self) -> bool:
        """`select_transcription_backend` gercekten bir arka uc bulabilir mi.

        Eskiden bu soru `asr_backend != "auto" or whisper_cpp_cli_path` ile
        yanitlaniyordu ve iki yonden de yanlisti: `auto` modda `faster-whisper`
        kurulu olsa bile `False` donuyordu (secim mantiginda ONCELIKLI olan o),
        `auto` disi modlarda ise arka ucun kurulu olup olmadigina hic bakmadan
        `True` donuyordu.

        `transcript_service` import EDILMIYOR: bu modul yapilandirma katmani ve
        servis katmanini cagirmasi dairesel bagimlilik yaratirdi. Kontrol,
        saglayicilarin `is_available()` govdeleriyle birebir ayni -- ikisi
        birlikte degismek zorunda.
        """
        import importlib.util

        faster_whisper = importlib.util.find_spec("faster_whisper") is not None
        whisper_cpp = bool(
            self.whisper_cpp_cli_path
            and self.whisper_cpp_model_path
            and os.path.exists(self.whisper_cpp_cli_path)
            and os.path.exists(self.whisper_cpp_model_path)
        )
        if self.asr_backend == "faster-whisper":
            return faster_whisper
        if self.asr_backend == "whisper.cpp":
            return whisper_cpp
        return faster_whisper or whisper_cpp

    # NOT: Dogrulayicilar artik alanlarin ait oldugu alt modellerde
    # (ServerConfig / UserCredentials / RunOptions) tanimli ve kalitimla geliyor.

    @property
    def runs_dir(self) -> Path:
        return Path(self.data_dir) / "runs"

    @property
    def cache_dir(self) -> Path:
        return Path(self.data_dir) / "cache"

    def ensure_directories(self) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        Path(self.sqlite_path).parent.mkdir(parents=True, exist_ok=True)

    def effective_enrichment_top_k(self) -> int:
        """Transkript zenginlestirmesi kisa listenin disina tasmamali."""
        return max(1, min(self.transcript_enrichment_top_k, self.metadata_top_k))


def _check_dotenv_encoding(dotenv_path: str) -> None:
    """`.env` icinde null bayt varsa anlasilir bir hata verir.

    PowerShell'in `>>` operatoru dosyaya UTF-16 yazar; UTF-8 bir `.env`'e boyle
    bir satir eklendiginde `python-dotenv` `ValueError: embedded null character`
    ile patlar ve yigin izi sorunun nedenini hic anlatmaz.
    """
    try:
        with open(dotenv_path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return
    if b"\x00" not in raw:
        return

    damaged = [
        str(index)
        for index, line in enumerate(raw.split(b"\n"), start=1)
        if b"\x00" in line
    ]
    raise RuntimeError(
        f"`.env` dosyası bozuk: {len(damaged)} satırda null bayt var "
        f"(satır {', '.join(damaged[:5])}). Dosyaya UTF-16 kodlu bir satır eklenmiş; "
        "PowerShell'de `>>` veya `Out-File` kullanıldığında bu olur.\n"
        "Düzeltmek için o satırları silip UTF-8 olarak yeniden yazın, ya da "
        "PowerShell'de şunu kullanın:\n"
        '  Add-Content .env "ANAHTAR=deger" -Encoding utf8'
    )


def _collect_env_warnings() -> list[str]:
    """`.env` icindeki taninmayan anahtarlari bildirir.

    `extra="ignore"` yazim hatalarini sessizce yutuyordu; artik kullaniciya
    "bu ayari yazdim ama hicbir sey degismedi" durumunu acikca soyluyoruz.
    """
    dotenv_path = find_dotenv(usecwd=True)
    if not dotenv_path:
        return []
    try:
        entries = dotenv_values(dotenv_path)
    except Exception:
        return []
    unknown = sorted(key for key in entries if key and key not in ENV_TO_FIELD)
    if not unknown:
        return []
    return [
        f"`.env` içinde tanınmayan anahtar(lar) yok sayıldı: {', '.join(unknown)}",
    ]


def load_config() -> AppConfig:
    # Iki arama BILEREK zincirli: `usecwd=True` calisma dizininden yukari arar,
    # parametresiz cagri BU dosyadan (settings.py) yukari arar. Ikincisi, sunucu
    # proje kokunun disindan baslatildiginda `.env`'i yine de buluyor.
    #
    # Eskiden yedek yol `load_dotenv()`'i PARAMETRESIZ cagiriyordu ve bu, ayni
    # aramayi python-dotenv'in kendi icinde yaptiriyordu: bulunan dosya
    # `_check_dotenv_encoding`'i HIC gormeden yukleniyordu -- yani fonksiyonun
    # tam da engellemek icin var oldugu durum yedek yoldan sizabiliyordu.
    # Artik once YOL cozuluyor, sonra kontrol edilen dosyanin AYNISI yukleniyor.
    dotenv_path = find_dotenv(usecwd=True) or find_dotenv()
    if dotenv_path:
        _check_dotenv_encoding(dotenv_path)
        try:
            load_dotenv(dotenv_path)
        except ValueError as exc:
            raise RuntimeError(
                f"`.env` dosyası okunamadı ({exc}). Dosyanın UTF-8 kodlu olduğundan emin olun."
            ) from exc
    values: dict[str, str] = {}
    for env_name, field_name in ENV_TO_FIELD.items():
        raw = os.getenv(env_name)
        if raw is None:
            continue
        raw = raw.strip()
        if raw == "":
            # Bos birakilan opsiyonel degisken, modeldeki varsayilani ezmemeli.
            continue
        values[field_name] = raw

    warnings = _collect_env_warnings()
    try:
        config = AppConfig.model_validate({**values, "config_warnings": warnings})
    except ValidationError as exc:
        if "gemini_api_key" in str(exc):
            raise RuntimeError(
                "GEMINI_API_KEY tanımlı değil. `.env` dosyanıza ekleyin (örnek için `.env.example`)."
            ) from exc
        raise RuntimeError(f"Invalid configuration: {exc}") from exc
    config.ensure_directories()
    return config


# --------------------------------------------------- surec geneli yapilandirma
#
# `load_config` her cagrida `.env`i yeniden okur. Surec boyunca TEK bir taban
# yapilandirma olmali ve bunu SORACAK olan iki taraf var: HTTP katmani
# (`api/deps`) ve isi calistiran taraf (`src/jobs/runtime`).
#
# Erisim tek noktada toplandi cunku ayrildigi anda iki taraf FARKLI
# yapilandirma okuyabiliyor. Bu somut olarak yasandi: is katmani dogrudan
# `load_config()` cagirinca testlerin `api/deps` uzerine koydugu yama
# baypas edildi ve testler gercek `.env`i okuyup GERCEK API cagrisi yapti.
#
# Cagiranlar bu fonksiyonu MODUL UZERINDEN cagirmali
# (`settings.base_config()`), `from ... import base_config` ile degil:
# yerel bir ada baglanan import, tek yama noktasi olma ozelligini bozar.


@functools.lru_cache(maxsize=1)
def base_config() -> AppConfig:
    """Surec omru boyunca bir kez okunan taban yapilandirma."""
    return load_config()


def reset_base_config() -> None:
    """Onbellegi bosaltir. Testler ve yapilandirma degisikligi sonrasi icin."""
    base_config.cache_clear()
