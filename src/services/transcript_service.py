from __future__ import annotations

import os
import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path

from src.config import AppConfig
from src.models import TranscriptResult, VideoCandidate
from src.providers.errors import (
    ProviderPermanentError,
    ProviderRateLimitedError,
    ProviderTemporaryError,
    VideoUnavailableError,
)
from src.providers.faster_whisper_provider import FasterWhisperProvider
from src.providers.whisper_cpp_provider import WhisperCppProvider
from src.providers.youtube_transcript_api_provider import YouTubeTranscriptAPIProvider
from src.providers.ytdlp_provider import YtDlpProvider
from src.storage import DEFAULT_USER_ID, SQLiteStore
from src.utils.logging_utils import redact_secrets
from src.utils.retry_utils import retry_with_backoff


# Transcript downloads are mostly network-bound and may run in parallel. ASR
# is different: a single faster-whisper call can consume every CPU core (or a
# GPU), so it gets its own process-wide gate. Production has one AppConfig;
# retaining a gate per configured limit also keeps independently configured
# test/app instances from unexpectedly sharing a semaphore.
_ASR_GATES: dict[int, threading.BoundedSemaphore] = {}
_ASR_GATES_LOCK = threading.Lock()

# Arka ucun kendi zaman asimi ile cagiranin beklemesi arasindaki pay.
# Model yuklemesi (ilk cagride saniyeler surebilir) kod cozme butcesinin
# DISINDA kaldigi icin sabit ve comert tutuluyor; bkz. `_transcribe_with_timeout`.
_ASR_TIMEOUT_GRACE_SEC = 60


def _asr_gate(max_workers: int) -> threading.BoundedSemaphore:
    with _ASR_GATES_LOCK:
        return _ASR_GATES.setdefault(max_workers, threading.BoundedSemaphore(max_workers))


@dataclass
class RunTranscriptState:
    attempted_pairs: set[tuple[str, str]] = field(default_factory=set)
    downloaded_audio: dict[str, str] = field(default_factory=dict)
    asr_video_count: int = 0
    asr_seconds_used: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def claim_attempt(self, video_id: str, provider_name: str) -> bool:
        """Bu (video, saglayici) cifti icin denemeyi rezerve eder; ilk cagirana True doner."""
        with self.lock:
            key = (video_id, provider_name)
            if key in self.attempted_pairs:
                return False
            self.attempted_pairs.add(key)
            return True

    def claim_asr_slot(
        self, limit: int, duration_sec: int | None = None, seconds_budget: int = 0
    ) -> bool:
        """Calistirma basina ASR kotasindan yer ayirir.

        IKI ayri tavan var cunku "kac video" ile "ne kadar hesaplama" ayni sey
        degil: ASR'in bedeli video SURESIYLE orantili ve yalnizca adet sayan
        bir kota, 3 saatlik iki dersi de kabul ediyordu. Ikisi de 0 iken
        sinirsiz.

        Sure bilinmiyorsa (`duration_sec` None) butceden DUSULMUYOR ama adet
        tavani yine isliyor: bilinmeyen bir sureyi tahmin edip butceyi ona
        gore harcamak, olcmedigimiz bir seye gore karar vermek olurdu.
        """
        with self.lock:
            if limit and self.asr_video_count >= limit:
                return False
            if seconds_budget and duration_sec and (
                self.asr_seconds_used + duration_sec > seconds_budget
            ):
                return False
            self.asr_video_count += 1
            self.asr_seconds_used += duration_sec or 0
            return True


def _asr_skip_reason(config: AppConfig, candidate: VideoCandidate) -> str | None:
    """ASR'a hic girmemek icin bir sebep varsa dondurur.

    Bu kontroller SES INDIRMEDEN once yapilmali: canli bir yayinin sesini
    indirmek yayin bitene kadar surer, uzun bir videonunki ise kotanin
    tamamini tek basina yer. Ikisi de `VideoCandidate` uzerinde ZATEN duran
    ama ASR yolunda hic okunmayan alanlar.
    """
    if candidate.is_live:
        return "canlı yayın"
    limit = config.max_asr_video_duration_sec
    duration = candidate.duration_sec
    if limit and duration and duration > limit:
        return f"süre {duration}sn > tavan {limit}sn"
    return None


def select_transcription_backend(config: AppConfig):
    faster = FasterWhisperProvider(config)
    whisper_cpp = WhisperCppProvider(config)
    if config.asr_backend == "faster-whisper":
        return faster if faster.is_available() else None
    if config.asr_backend == "whisper.cpp":
        return whisper_cpp if whisper_cpp.is_available() else None
    if faster.is_available():
        return faster
    if whisper_cpp.is_available():
        return whisper_cpp
    return None


def get_transcript(
    config: AppConfig,
    store: SQLiteStore,
    candidate: VideoCandidate,
    run_dir: str,
    state: RunTranscriptState,
    logger=None,
    preferred_language: str | None = None,
    user_id: str = DEFAULT_USER_ID,
) -> TranscriptResult:
    language_hint = preferred_language or candidate.language
    providers: list[tuple[str, object]] = [
        ("youtube_transcript_api", lambda: _fetch_youtube_transcript(config, candidate, language_hint, logger)),
        ("yt_dlp_subtitles", lambda: _fetch_ytdlp_subtitles(config, candidate, language_hint, logger)),
    ]
    if config.enable_asr_fallback:
        providers.append(
            ("asr", lambda: _fetch_asr_transcript(config, candidate, run_dir, state, language_hint, logger))
        )

    attempted: list[str] = []
    for provider_name, loader in providers:
        attempted.append(provider_name)

        cached = store.get_transcript_cache(candidate.video_id, provider_name, language_hint)
        if cached is not None:
            cached.attempted_providers = attempted.copy()
            if cached.status == "available":
                return cached
            continue

        if store.get_provider_cooldown(provider_name):
            # ONEMLI: cooldown durumu ARTIK transkript onbellegine yazilmiyor.
            # Eskiden 15 dakikalik cooldown, 1 saatlik bir "cooldown" onbellek kaydi
            # birakiyor ve saglayici iyilestikten sonra bile atlanmaya devam ediyordu.
            if logger:
                logger.info("Skipping %s for %s: provider in cooldown", provider_name, candidate.video_id)
            continue

        if provider_name == "asr":
            skip_reason = _asr_skip_reason(config, candidate)
            if skip_reason:
                if logger:
                    logger.info("Skipping ASR for %s: %s", candidate.video_id, skip_reason)
                continue

        # Tekrarlanan bir deneme kit ASR kotasini TUKETMEMELI. Eskiden once
        # slot ayriliyor, ARDINDAN bu ciftin zaten islendigi fark ediliyordu --
        # yani ayrilan slot geri verilmeden yaniyordu.
        if not state.claim_attempt(candidate.video_id, provider_name):
            continue

        if provider_name == "asr" and not state.claim_asr_slot(
            config.max_asr_videos_per_run,
            candidate.duration_sec,
            config.max_asr_seconds_per_run,
        ):
            if logger:
                logger.info(
                    "Skipping ASR for %s: run limit of %s reached",
                    candidate.video_id,
                    config.max_asr_videos_per_run,
                )
            continue

        try:
            # Ses indirme ile transkripsiyonun tekrar deneme kurallari AYNI
            # DEGIL. `_fetch_asr_transcript` iki asamayi ayri ayri tekrarliyor
            # ve basarili bir indirmeyi, transkripsiyon tekrarlanirken
            # KORUYOR. Butun fonksiyonu burada sarmalamak, her gecici Whisper
            # hatasindan sonra ayni sesi bastan indirmek demekti.
            if provider_name == "asr":
                result = loader()
            else:
                result = retry_with_backoff(
                    loader,
                    attempts=config.retry_max_attempts,
                    base_delay=config.retry_base_delay_sec,
                    logger=logger,
                    on_exception=(ProviderTemporaryError,),
                )
        except ProviderRateLimitedError as exc:
            # Sunucu acikca "yavasla" diyor. Tekrar DENEME; saglayiciyi hemen
            # dinlendir. Eskiden bu hata genel "gecici hata" sayiliyor, her video
            # icin 3 kez tekrarlaniyor ve IP blogunu derinlestiriyordu.
            cooldown = exc.retry_after or config.rate_limit_cooldown_sec
            store.mark_provider_cooldown(provider_name, redact_secrets(str(exc)), cooldown)
            if logger:
                logger.warning(
                    "Rate limited on %s; cooling down for %ss without retrying", provider_name, cooldown
                )
            # Onbellege YAZMIYORUZ: hiz siniri videoyla ilgili degil, saglayiciyla.
            # Durum `provider_health` tablosunda zaten tutuluyor.
            continue
        except VideoUnavailableError as exc:
            # Videoya OZGU kalici durum: saglayici saglikli, sadece bu video uygun degil.
            result = TranscriptResult(
                video_id=candidate.video_id,
                status="failed_permanent",
                source=provider_name,
                error=redact_secrets(str(exc)),
                attempted_providers=attempted.copy(),
            )
            store.put_transcript_cache(result, config.transcript_cache_ttl_sec, language_hint)
            continue
        except ProviderPermanentError as exc:
            # Yapilandirma/kurulum hatasi: tekrar denemek fayda etmez, cezalandirmak da.
            #
            # Ama SORMAYI BIRAKMALIYIZ: bu hatalar (PO token istegi, okunamayan
            # Chrome cerezleri, eksik yt-dlp secenegi) TEK bir videoyla degil
            # kurulumla ilgili, yani kalan her aday icin de ayni sekilde
            # basarisiz olacak. Eskiden 12 adayin 12'sinde de deneniyordu.
            # Dinlenme sayaci ARTIRILMIYOR (`mark_provider_cooldown` ardisik
            # hata sayacina dokunmuyor): saglayici arizali degil, eksik
            # yapilandirilmis.
            store.mark_provider_cooldown(
                provider_name,
                redact_secrets(str(exc)),
                config.provider_cooldown_sec,
                event=store.MISCONFIGURED,
            )
            if logger:
                logger.error(
                    "Permanent transcript failure on %s (cooling down %ss): %s",
                    provider_name,
                    config.provider_cooldown_sec,
                    exc,
                )
            result = TranscriptResult(
                video_id=candidate.video_id,
                status="failed_permanent",
                source=provider_name,
                error=redact_secrets(str(exc)),
                attempted_providers=attempted.copy(),
            )
            store.put_transcript_cache(result, config.failure_cache_ttl_sec, language_hint)
            continue
        except Exception as exc:
            message = redact_secrets(str(exc))
            failure_count, cooled_down = store.record_provider_failure(
                provider_name,
                message,
                config.provider_cooldown_sec,
                threshold=config.provider_failure_threshold,
            )
            if logger:
                logger.warning(
                    "Transcript failure %s on %s for %s (cooldown=%s): %s",
                    failure_count,
                    provider_name,
                    candidate.video_id,
                    cooled_down,
                    message,
                )
            result = TranscriptResult(
                video_id=candidate.video_id,
                status="failed_temporary",
                source=provider_name,
                error=message,
                attempted_providers=attempted.copy(),
            )
            store.put_transcript_cache(result, config.failure_cache_ttl_sec, language_hint)
            continue

        result.attempted_providers = attempted.copy()
        if result.status == "available":
            store.put_transcript_cache(result, config.transcript_cache_ttl_sec, language_hint)
            store.clear_provider_cooldown(provider_name)
            store.record_provider_success(provider_name)
            return result

        store.put_transcript_cache(result, config.failure_cache_ttl_sec, language_hint)
        # Saglayici cevap verdi ama icerik yok: bu bir saglayici arizasi degil.
        store.clear_provider_cooldown(provider_name)

    return TranscriptResult(
        video_id=candidate.video_id,
        status="unavailable",
        source="none",
        attempted_providers=attempted,
        error="No transcript provider produced usable content",
    )


def _fetch_youtube_transcript(
    config: AppConfig, candidate: VideoCandidate, language_hint: str | None, logger=None
) -> TranscriptResult:
    provider = YouTubeTranscriptAPIProvider(config, logger=logger)
    transcript = provider.fetch(candidate.video_id, language_hint)
    if transcript:
        if logger:
            logger.info("Transcript resolved via %s for %s", provider.name, candidate.video_id)
        return transcript
    return TranscriptResult(video_id=candidate.video_id, status="unavailable", source=provider.name)


def _fetch_ytdlp_subtitles(
    config: AppConfig, candidate: VideoCandidate, language_hint: str | None, logger=None
) -> TranscriptResult:
    provider = YtDlpProvider(config)
    transcript = provider.fetch_subtitles(candidate.url, candidate.video_id, language_hint)
    if transcript:
        if logger:
            logger.info("Transcript resolved via yt-dlp subtitles for %s", candidate.video_id)
        return transcript
    return TranscriptResult(video_id=candidate.video_id, status="unavailable", source="yt_dlp_subtitles")


def _fetch_asr_transcript(
    config: AppConfig,
    candidate: VideoCandidate,
    run_dir: str,
    state: RunTranscriptState,
    language_hint: str | None,
    logger=None,
) -> TranscriptResult:
    backend = select_transcription_backend(config)
    if backend is None:
        return TranscriptResult(
            video_id=candidate.video_id,
            status="failed_permanent",
            source="asr",
            error="No ASR backend is available",
        )

    ytdlp = YtDlpProvider(config)
    audio_dir = Path(run_dir) / "audio"
    audio_path = state.downloaded_audio.get(candidate.video_id)
    if not audio_path or not os.path.exists(audio_path):
        audio_path = retry_with_backoff(
            lambda: ytdlp.download_audio(candidate.url, str(audio_dir), candidate.video_id),
            attempts=config.retry_max_attempts,
            base_delay=config.retry_base_delay_sec,
            logger=logger,
            on_exception=(ProviderTemporaryError,),
        )
        state.downloaded_audio[candidate.video_id] = audio_path

    if logger:
        logger.info("Using ASR backend %s for %s", backend.name, candidate.video_id)
    timed_out = False
    timed_out_worker: threading.Thread | None = None
    try:
        def transcribe_once() -> TranscriptResult:
            nonlocal timed_out, timed_out_worker
            result, timed_out, timed_out_worker = _transcribe_with_timeout(
                backend,
                audio_path,
                candidate.video_id,
                language_hint,
                config,
            )
            return result

        # Indirme PAHALI ama DEGISMEZ: ayni video ayni sesi verir. Gecici bir
        # transkripsiyon hatasi tekrarlanirken indirilen ses korunuyor -- aksi
        # halde ayni dosya uc kez cekilirdi.
        result = retry_with_backoff(
            transcribe_once,
            attempts=config.retry_max_attempts,
            base_delay=config.retry_base_delay_sec,
            logger=logger,
            on_exception=(ProviderTemporaryError,),
        )
    finally:
        if timed_out and timed_out_worker is not None:
            # Python, CTranslate2'yi baska bir thread'den GUVENLE sonlandiramaz;
            # zorla oldurmek paylasilan modeli bozabilir. Bu yuzden daemon
            # thread, yerel cagri gercekten donene kadar ASR kapisini tutmaya
            # devam ediyor; temizlik ardindan TAM BIR KEZ ve isi bloklamadan
            # calisiyor.
            threading.Thread(
                target=_cleanup_audio_after_thread,
                args=(state, candidate.video_id, audio_path, timed_out_worker),
                daemon=True,
                name="asr-cleanup",
            ).start()
        else:
            _cleanup_audio(state, candidate.video_id, audio_path)

    result.source = "asr"
    result.backend = backend.name
    if result.status != "available" and logger:
        logger.warning("ASR backend %s returned %s for %s", backend.name, result.status, candidate.video_id)
    return result


def _transcribe_with_timeout(
    backend,
    audio_path: str,
    video_id: str,
    language_hint: str | None,
    config: AppConfig,
) -> tuple[TranscriptResult, bool, threading.Thread | None]:
    """ASR'i kendi es zamanlilik kapisi ve SINIRLI bir cagiran beklemesiyle calistirir.

    faster-whisper yerel kodu SUREC ICINDE calistiriyor; zaman asimina ugramis
    bir thread'i zorla oldurmek paylasilan modeli bozabilirdi. Bu yuzden isci
    bir daemon: sure dolunca API isi gecici bir hata aliyor, kapi ise yerel
    cagri gercekten bitene kadar tutulmaya devam ediyor. Yani "cagirani
    bekletmeyi birakmak" ile "isi durdurmak" AYRI seyler.
    """
    timeout = config.faster_whisper_timeout_sec if backend.name == "faster_whisper" else config.whisper_cpp_timeout_sec
    # Dis bekleme, arka ucun KENDI butcesinden bir miktar UZUN olmali.
    #
    # Iki arka uc da kendini zaten sinirliyor: faster-whisper kod cozme
    # dongusunde her segmentte saate bakiyor, whisper.cpp `subprocess`
    # zaman asimi kullaniyor. Ikisi de temiz durur ve `failed_temporary`
    # doner. Buradaki bekleme ayni degeri kullanirsa ONCE O doluyor --
    # cunku sayaci daha erken basliyor (thread kurulumu ve model yuklemesi
    # araya giriyor) -- ve her seferinde asagidaki pahali yola sapiliyordu:
    # terk edilmis daemon thread, tutulmaya devam eden ASR kapisi, ertelenmis
    # temizlik. Pay birakinca temiz durdurma kazaniyor ve bu yol yalnizca
    # arka uc gercekten kendini sinirlayamadiginda calisiyor.
    wait_timeout = timeout + _ASR_TIMEOUT_GRACE_SEC
    gate = _asr_gate(config.max_asr_workers)
    if not gate.acquire(timeout=timeout):
        return (
            TranscriptResult(
                video_id=video_id,
                status="failed_temporary",
                source="asr",
                backend=backend.name,
                error=f"ASR worker was unavailable for {timeout} seconds",
            ),
            False,
            None,
        )

    outcomes: queue.Queue[object] = queue.Queue(maxsize=1)

    def work() -> None:
        try:
            outcomes.put(backend.transcribe(audio_path, video_id, language_hint))
        except BaseException as exc:  # ozgun saglayici hatasini retry katmanina tasi
            outcomes.put(exc)
        finally:
            gate.release()

    worker = threading.Thread(target=work, daemon=True, name="asr-transcribe")
    try:
        worker.start()
    except BaseException:
        # Izni BURADA geri veriyoruz cunku `work` hic calismadi, yani onun
        # `finally` dali da calismayacak. Thread baslatilamamasi ("can't start
        # new thread") nadir ama sonucu kalici: varsayilan `MAX_ASR_WORKERS=1`
        # ile tek bir sizinti kapiyi sonsuza kadar kapali birakir ve sonraki
        # her ASR istegi zaman asimina ugrayip "worker unavailable" doner --
        # surec yeniden baslatilana kadar.
        gate.release()
        raise

    try:
        outcome = outcomes.get(timeout=wait_timeout)
    except queue.Empty:
        return (
            TranscriptResult(
                video_id=video_id,
                status="failed_temporary",
                source="asr",
                backend=backend.name,
                error=f"{backend.name} did not finish within {wait_timeout} seconds",
            ),
            True,
            worker,
        )
    if isinstance(outcome, BaseException):
        raise outcome
    return outcome, False, None


def _cleanup_audio_after_thread(
    state: RunTranscriptState, video_id: str, audio_path: str, worker: threading.Thread
) -> None:
    """Zaman asimina ugramis daemon sesi okumayi BIRAKANA KADAR bekler, sonra siler.

    Dosyayi hemen silmek, hala okumakta olan yerel cagriyi ortasindan vururdu.
    """
    worker.join()
    _cleanup_audio(state, video_id, audio_path)


def _cleanup_audio(state: RunTranscriptState, video_id: str, audio_path: str) -> None:
    """Ses dosyasini siler ve state'teki yolu da kaldirir.

    Eskiden dosya siliniyor ama `state.downloaded_audio` silinmis dosyanin yolunu
    tutmaya devam ediyordu; tekrar giris halinde var olmayan dosya kullanilirdi.
    """
    if os.path.exists(audio_path):
        try:
            os.remove(audio_path)
        except OSError:
            pass
    with state.lock:
        state.downloaded_audio.pop(video_id, None)
