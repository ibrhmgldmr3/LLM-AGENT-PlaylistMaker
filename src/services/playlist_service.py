from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from src.config import AppConfig
from src.models import (
    ExportArtifacts,
    MetadataScore,
    PlaylistRequest,
    PlaylistResult,
    ProgressEvent,
    SubtopicResult,
    Subtopic,
    TranscriptResult,
    VideoCandidate,
)
from src.providers import GeminiLLMProvider
from src.services.metadata_ranker import rank_candidates
from src.services.playlist_publish_service import create_youtube_playlist
from src.services.recommendation_service import assign_recommendations
from src.services.topic_service import generate_subtopics
from src.services.transcript_service import RunTranscriptState, get_transcript
from src.services.youtube_search_service import search_candidates
from src.storage import DEFAULT_USER_ID, SQLiteStore
from src.utils.logging_utils import close_logger, redact_secrets, run_log_path, setup_logger


# Ilerleme cubugu icin faz sinirlari.
_P_PLANNING = 0.05
_P_SEARCH = 0.35
_P_RANKING = 0.40
_P_TRANSCRIPTS = 0.85
_P_SELECTION = 0.95


@dataclass
class _SubtopicWork:
    """Bir alt konunun boru hatti boyunca tasidigi durum."""

    subtopic: Subtopic
    query: str
    extra_queries: list[str] = field(default_factory=list)
    candidates: list[VideoCandidate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ranked: list[tuple[VideoCandidate, MetadataScore]] = field(default_factory=list)
    pool_size: int = 0

    @property
    def all_queries(self) -> list[str]:
        return [self.query, *self.extra_queries]


def _build_query(topic: str, subtopic) -> str:
    """LLM'in urettigi arama sorgusunu tercih eder.

    Ham "konu + alt konu" birlesimi tekrarli ve dogal olmayan bir sorgu uretiyor
    ("Makine ogrenmesi ile zaman serisi tahmini XGBoost ile zaman serisi tahmini").
    LLM alanı bildigi icin insanlarin gercekten aradigi terimleri verebiliyor.
    """
    if subtopic.search_query:
        return subtopic.search_query
    return f"{topic} {subtopic.title}"


def _build_queries(topic: str, subtopic, filters) -> list[str]:
    """Bu alt konu icin calistirilacak arama sorgulari.

    Iki dilli kesif acikken ana dilin yaninda Ingilizce sorgu da eklenir; Turkce
    gibi icerik havuzu sig olan dillerde aday kalitesini belirgin arttirir.
    Maliyeti alt konu basina bir ek `search.list` cagrisidir.
    """
    queries = [_build_query(topic, subtopic)]
    if filters.include_english and not filters.language.lower().startswith("en"):
        english = subtopic.search_query_en or subtopic.title
        if english and english not in queries:
            queries.append(english)
    return queries


def build_playlist(
    config: AppConfig,
    request: PlaylistRequest,
    progress_callback=None,
    run_id: str | None = None,
    user_id: str = DEFAULT_USER_ID,
) -> PlaylistResult:
    """Playlist uretir.

    `run_id` disaridan verilebilir: API katmani isi arka plana atmadan ONCE
    kimligi bilmeli ki cagirana hemen bir is numarasi donebilsin. Verilmezse
    eskisi gibi burada uretilir.

    `user_id` cok kullanicili moda hazirlik; tek kullanicili kurulumda
    varsayilan degerde kalir.
    """
    if os.name == "nt" and config.allow_unsafe_openmp_workaround:
        os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

    run_id = run_id or uuid4().hex
    run_dir = config.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    store = SQLiteStore(config.sqlite_path)
    log_file = run_log_path(str(run_dir))
    logger = setup_logger("playlist", log_file)

    try:
        return _run_pipeline(
            config, request, run_id, run_dir, store, logger, progress_callback, user_id
        )
    finally:
        close_logger("playlist", log_file)


def _run_pipeline(
    config: AppConfig,
    request: PlaylistRequest,
    run_id: str,
    run_dir: Path,
    store: SQLiteStore,
    logger,
    progress_callback,
    user_id: str = DEFAULT_USER_ID,
) -> PlaylistResult:
    store.create_run(run_id, request.topic, request.filters.model_dump(), user_id=user_id)

    def emit(stage: str, message: str, progress: float, current: int | None = None, total: int | None = None) -> None:
        # DIKKAT: yalnizca ana thread'den cagrilir. Worker thread'ler ilerleme
        # bildirmez; `as_completed` dongusu ana thread'de dondugu icin guvenli.
        if progress_callback:
            progress_callback(
                ProgressEvent(stage=stage, message=message, progress=progress, current=current, total=total)
            )

    result = PlaylistResult(
        run_id=run_id,
        topic=request.topic,
        filters=request.filters,
        subtopics=[],
        recommendations=[],
    )

    # ---------------------------------------------------------------- FAZ 0
    emit("topic_planning", "Alt konular üretiliyor", 0.02)
    llm = GeminiLLMProvider(config)
    subtopics = generate_subtopics(
        llm, request.topic, request.filters.language, max_items=config.max_subtopics, logger=logger
    )
    if not subtopics:
        result.warnings.append("Konu alt başlıklara ayrılamadı. Daha açık veya daha dar bir konu deneyin.")
        result.exports = export_playlist_artifacts(run_dir, result)
        store.finalize_run(run_id, result)
        emit("done", "Alt konu üretilemedi", 1.0)
        return result

    work = []
    for item in subtopics:
        queries = _build_queries(request.topic, item, request.filters)
        work.append(_SubtopicWork(subtopic=item, query=queries[0], extra_queries=queries[1:]))

    # ---------------------------------------------------------------- FAZ 1
    # Aramalar birbirinden bagimsiz ve tamamen ag-baglantili -> paralel.
    _search_all(config, store, request, work, logger, emit, user_id=user_id)

    # ---------------------------------------------------------------- FAZ 2
    # HAVUZLAMA: her alt konu, yalnizca kendi arama sonuclarini degil TUM alt
    # konularin sonuclarini gorur. Ek API cagrisi gerektirmez ama secim havuzunu
    # alt konu sayisi kadar buyutur; bir alt konunun aradigi video pekala baska
    # bir aramada cikmis olabilir.
    pool: dict[str, VideoCandidate] = {}
    for item in work:
        for candidate in item.candidates:
            pool.setdefault(candidate.video_id, candidate)
    pooled = list(pool.values())
    if logger:
        logger.info(
            "Candidate pool: %s unique videos from %s subtopic searches", len(pooled), len(work)
        )

    # Uyarilar havuzlamadan SONRA uretilir: kendi aramasi sonuc vermeyen bir alt
    # konu, ortak havuz sayesinde yine de iyi bir video bulabilir.
    for item in work:
        if item.candidates:
            continue
        detail = " (" + "; ".join(redact_secrets(note) for note in item.notes[:3]) + ")" if item.notes else ""
        if pooled:
            result.warnings.append(
                f"'{item.subtopic.title}' için yapılan arama sonuç vermedi; "
                f"diğer alt konuların havuzundan seçildi.{detail}"
            )
        else:
            result.warnings.append(f"'{item.subtopic.title}' için aday video bulunamadı.{detail}")

    emit("metadata_ranking", f"{len(pooled)} aday sıralanıyor", _P_RANKING)
    for item in work:
        item.ranked = rank_candidates(
            pooled,
            request.topic,
            item.subtopic.title,
            request.filters,
            subtopic_terms=item.subtopic.match_terms,
        )
        item.pool_size = len(pooled)

    # ---------------------------------------------------------------- FAZ 3
    # Transkriptler VIDEO bazinda tekillestirilip paralel cekilir. Onceki surumde
    # alt konu icinde seri calisiyordu ve toplam surenin ~%73'unu yiyordu.
    transcripts_by_video = _fetch_transcripts(
        config, store, request, work, run_id, run_dir, logger, emit, user_id=user_id
    )

    # ---------------------------------------------------------------- FAZ 4
    # Secim SIRALI olmak zorunda: `used_video_ids` alt konular arasinda
    # tekrar engelliyor ve sonuc alt konu sirasina bagli.
    emit("final_playlist_assembly", "Playlist derleniyor", _P_SELECTION)
    # Eslestirme GLOBAL: her video en iyi uydugu alt konuya gider. Alt konu
    # sirasina gore acgozlu secim, erken bir alt konunun sonraki bir alt konuya
    # cok daha iyi uyan videoyu kapmasina yol aciyordu.
    assignments = assign_recommendations(
        [(item.subtopic.title, item.ranked) for item in work],
        transcripts_by_video,
        channel_repeat_penalty=config.channel_repeat_penalty,
    )

    for index, item in enumerate(work, start=1):
        recommendation = assignments[index - 1]
        shortlisted = [candidate for candidate, _score in item.ranked[: config.metadata_top_k]]

        if recommendation is None:
            if item.candidates:
                result.warnings.append(
                    f"'{item.subtopic.title}' için benzersiz bir öneri bulunamadı "
                    "(tüm adaylar başka alt konularda kullanıldı)."
                )
            subtopic_result = SubtopicResult(
                subtopic=item.subtopic,
                # Iki dilli kesifte birden fazla sorgu calisir; tanilamada hepsi gorunsun.
                query=" | ".join(item.all_queries),
                candidates_considered=item.pool_size or len(item.candidates),
                shortlisted_candidates=shortlisted,
                selected_video_id=None,
                transcript_status="unavailable",
                notes=item.notes or ["No unique candidate survived ranking and deduplication."],
            )
        else:
            result.recommendations.append(recommendation)
            subtopic_result = SubtopicResult(
                subtopic=item.subtopic,
                # Iki dilli kesifte birden fazla sorgu calisir; tanilamada hepsi gorunsun.
                query=" | ".join(item.all_queries),
                candidates_considered=item.pool_size or len(item.candidates),
                shortlisted_candidates=shortlisted,
                selected_video_id=recommendation.video.video_id,
                transcript_status=recommendation.transcript_status,
                notes=[recommendation.why_selected],
            )
            store.add_run_video(run_id, "recommendation", recommendation.video.video_id, recommendation.model_dump())

        result.subtopics.append(subtopic_result)
        store.add_run_subtopic(run_id, index, subtopic_result.model_dump())

    # ---------------------------------------------------------------- FAZ 5
    if request.create_youtube_playlist:
        if not result.recommendations:
            result.warnings.append("Öneri üretilmediği için YouTube playlist'i oluşturulmadı.")
        else:
            try:
                publish = create_youtube_playlist(config, result, logger=logger)
                result.published_playlist_url = publish.url
                result.warnings.extend(publish.warnings)
            except Exception as exc:
                result.warnings.append(f"YouTube playlist oluşturulamadı: {redact_secrets(str(exc))}")
                if logger:
                    logger.exception("YouTube playlist creation failed")

    result.exports = export_playlist_artifacts(run_dir, result)
    store.finalize_run(run_id, result)

    try:
        removed = store.purge_expired()
        if removed and logger:
            logger.info("Purged %s expired cache rows", removed)
    except Exception:
        if logger:
            logger.warning("Cache purge failed", exc_info=True)

    emit("done", "Playlist hazır", 1.0)
    return result


def _search_all(
    config: AppConfig,
    store: SQLiteStore,
    request: PlaylistRequest,
    work: list[_SubtopicWork],
    logger,
    emit,
    user_id: str = DEFAULT_USER_ID,
) -> None:
    """Alt konu aramalarini paralel calistirir; sonuclari `work` uzerine yazar."""
    total = len(work)
    workers = max(1, min(config.max_search_workers, total))

    def run_one(item: _SubtopicWork) -> None:
        notes: list[str] = []
        merged: dict[str, VideoCandidate] = {}
        for query in item.all_queries:
            for candidate in search_candidates(
                config, store, query, request.filters, logger=logger, notes=notes, user_id=user_id
            ):
                merged.setdefault(candidate.video_id, candidate)
        item.candidates = list(merged.values())

        if not item.candidates:
            # GENISLETME: dar bir sorgu hic sonuc vermediyse yalnizca alt konu
            # basligiyla tekrar dene. Kalip kelimeler ("... ile ... tahmini")
            # bazen sonucu sifira dusuruyor.
            widened = item.subtopic.title
            if widened and widened != item.query:
                if logger:
                    logger.info("Widening search for %r -> %r", item.query, widened)
                item.candidates = search_candidates(
                    config, store, widened, request.filters, logger=logger, notes=notes, user_id=user_id
                )
                if item.candidates:
                    notes.append(f"genişletilmiş sorgu kullanıldı: {widened!r}")
        item.notes = notes

    def report(done: int, title: str) -> None:
        emit(
            "candidate_search",
            f"Adaylar aranıyor: {title}",
            _P_PLANNING + (done / total) * (_P_SEARCH - _P_PLANNING),
            done,
            total,
        )

    if workers == 1:
        for done, item in enumerate(work, start=1):
            _guard(run_one, item, logger, "search")
            report(done, item.subtopic.title)
        return

    emit("candidate_search", f"{total} alt konu için adaylar aranıyor", _P_PLANNING, 0, total)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="search") as pool:
        futures = {pool.submit(_guard, run_one, item, logger, "search"): item for item in work}
        for done, future in enumerate(as_completed(futures), start=1):
            report(done, futures[future].subtopic.title)


def _fetch_transcripts(
    config: AppConfig,
    store: SQLiteStore,
    request: PlaylistRequest,
    work: list[_SubtopicWork],
    run_id: str,
    run_dir: Path,
    logger,
    emit,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, TranscriptResult]:
    """Zenginlestirilecek videolari tekillestirip paralel transkript ceker."""
    enrichment_top_k = config.effective_enrichment_top_k()

    targets: dict[str, VideoCandidate] = {}
    for item in work:
        for candidate, _score in item.ranked[:enrichment_top_k]:
            targets.setdefault(candidate.video_id, candidate)

    results: dict[str, TranscriptResult] = {}
    if not targets:
        emit("transcript_enrichment", "Zenginleştirilecek aday yok", _P_TRANSCRIPTS)
        return results

    state = RunTranscriptState()
    total = len(targets)
    workers = max(1, min(config.max_transcript_workers, total))

    def run_one(candidate: VideoCandidate) -> TranscriptResult:
        return get_transcript(
            config,
            store,
            candidate,
            str(run_dir),
            state,
            logger=logger,
            preferred_language=request.filters.language,
            user_id=user_id,
        )

    def store_result(video_id: str, transcript: TranscriptResult) -> None:
        results[video_id] = transcript
        store.add_run_video(run_id, "transcript", video_id, transcript.model_dump())

    def report(done: int) -> None:
        emit(
            "transcript_enrichment",
            f"Transkriptler alınıyor ({done}/{total})",
            _P_RANKING + (done / total) * (_P_TRANSCRIPTS - _P_RANKING),
            done,
            total,
        )

    if workers == 1:
        for done, (video_id, candidate) in enumerate(targets.items(), start=1):
            transcript = _guard(run_one, candidate, logger, "transcript")
            if transcript is not None:
                store_result(video_id, transcript)
            report(done)
        return results

    emit("transcript_enrichment", f"{total} video için transkript alınıyor", _P_RANKING, 0, total)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="transcript") as pool:
        futures = {pool.submit(_guard, run_one, candidate, logger, "transcript"): video_id
                   for video_id, candidate in targets.items()}
        for done, future in enumerate(as_completed(futures), start=1):
            transcript = future.result()
            if transcript is not None:
                store_result(futures[future], transcript)
            report(done)
    return results


def _guard(func, argument, logger, label: str):
    """Bir worker'daki hata tum calistirmayi dusurmemeli."""
    try:
        return func(argument)
    except Exception:
        if logger:
            logger.exception("Unhandled error in %s worker", label)
        return None


def export_playlist_artifacts(run_dir: Path, result: PlaylistResult) -> ExportArtifacts:
    json_path = run_dir / "result.json"
    markdown_path = run_dir / "study_plan.md"
    json_path.write_text(json.dumps(result.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_render_markdown(result), encoding="utf-8")
    return ExportArtifacts(json_path=str(json_path), markdown_path=str(markdown_path))


def _render_markdown(result: PlaylistResult) -> str:
    lines = [
        f"# Study Plan: {result.topic}",
        "",
        f"Generated at: {result.created_at}",
        "",
        "## Playlist",
        "",
    ]
    if not result.recommendations:
        lines.extend(["_No recommendations were produced._", ""])
    for recommendation in result.recommendations:
        lines.extend(
            [
                f"{recommendation.position}. [{recommendation.video.title}]({recommendation.video.url})",
                f"   - Subtopic: {recommendation.subtopic}",
                f"   - Why: {recommendation.why_selected}",
                f"   - Confidence: {recommendation.confidence_score}",
                f"   - Transcript: {recommendation.transcript_status}",
                "",
            ]
        )
    if result.warnings:
        lines.extend(["## Warnings", ""])
        lines.extend([f"- {warning}" for warning in result.warnings])
        lines.append("")
    return "\n".join(lines)
