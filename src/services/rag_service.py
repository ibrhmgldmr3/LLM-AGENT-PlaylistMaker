"""Kaynaga dayali soru-cevap: erisim, birlestirme ve UYDURMA KAPILARI.

Bu modulun asil isi cevap uretmek DEGIL, cevabin kaynakta gercekten var
oldugunu garanti etmek. Ozelligin vaadi "videolarimdan ve dokumanlarimdan
cevap ver" degil, "**yoksa yok de**" -- ve bir dil modeli, istendiginde her
zaman inandirici bir sey yazabilir.

Bu yuzden uc bagimsiz kapi var:

1. **Erisim esigi.** En iyi aday hem leksik olarak eslesmiyorsa hem de kosinus
   benzerligi `rag_min_similarity` altindaysa LLM **HIC CAGRILMIYOR**.
   Deterministik, ucretsiz ve modelin ikna kabiliyetinden bagimsiz.
2. **Uretim semasi.** Model `answered` bayragini ve KULLANDIGI parca
   numaralarini dondurmek zorunda (`RAG_ANSWER_SCHEMA`).
3. **Alinti dogrulamasi.** Modelin verdigi numaralar, baglama GERCEKTEN
   konulanlarla kesistiriliyor. Kesisim bossa yanit `answered=False`a
   dusuruluyor.

Ucuncusu pazarlik konusu degil: sema bir alanin VARLIGINI zorlar, ICERIGININ
dogrulugunu degil. "Cevapladim" deyip var olmayan bir kaynaga atif yapan bir
model halusinasyon uretmistir ve bunun sessizce gecmesi, ozelligin tum
degerini goturur.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from src.config import AppConfig
from src.models import Citation, RagAnswer, SourceChunk, SourceTextResult, VideoCandidate
from src.providers.errors import ProviderPermanentError
from src.services import embedding_service
from src.services.chunking import chunk_document, chunk_transcript
from src.services.document_parser import parse_document
from src.services.transcript_service import RunTranscriptState, get_transcript
from src.storage import DEFAULT_USER_ID, SQLiteStore
from src.utils.logging_utils import redact_secrets
from src.utils.text_utils import build_fts_query, coverage_score

_log = logging.getLogger(__name__)

# RRF sabiti. Literaturdeki yaygin deger; tek isi cok yuksek siralarin katkisini
# yumusatmak. Ayarlanabilir YAPILMADI -- kalibre edilecek bir sey degil ve her
# ayar bir daha bakilmayan bir dugme olma riski tasiyor.
_RRF_K = 60

# Her iki retriever'dan kac aday cekilecek. `rag_top_k`tan buyuk olmali ki
# birlestirme gercekten SECEBILSIN; ikisi esit olsaydi fuzyonun yapacak isi
# kalmazdi.
_CANDIDATE_MULTIPLIER = 4


def answer_question(
    config: AppConfig,
    store: SQLiteStore,
    llm,
    space_id: str,
    question: str,
    language: str = "Türkçe",
    user_id: str = DEFAULT_USER_ID,
) -> RagAnswer:
    """Alandaki kaynaklara dayanarak soruyu yanitlar; yoksa "bulamadim" der."""
    question = (question or "").strip()
    source_count = len(store.list_sources(space_id))
    if not question:
        return RagAnswer(
            answered=False,
            searched_sources=source_count,
            reason="Soru boş.",
        )

    if store.count_chunks(space_id) == 0:
        return RagAnswer(
            answered=False,
            searched_sources=source_count,
            reason=(
                "Bu öğrenme alanında henüz aranabilir içerik yok. "
                "Bir çalıştırma ekleyin ya da doküman yükleyin."
            ),
        )

    lexical, semantic = _retrieve(config, store, llm, space_id, question, user_id)

    # ------------------------------------------------------------- 1. KAPI
    best_similarity = semantic[0][1] if semantic else 0.0
    if not _lexical_is_evidence(config, store, lexical, question) and (
        best_similarity < config.rag_min_similarity
    ):
        # Esigin altinda kalan sorgunun EN IYI skoru loglaniyor: `rag_min_similarity`
        # bir tahmin ve kalibrasyonu ancak bu sayilarla yapilabilir. Yanlis
        # "bulamadim" ile halusinasyona kapi acmak arasindaki dengeyi gorunur
        # kilan tek veri bu (bkz. `docs/rag-plan.md` §4).
        _log.info(
            "RAG kacinma: space=%s en_iyi_benzerlik=%.3f esik=%.3f"
            " leksik_kapsam=%.2f kapsam_esigi=%.2f soru=%r",
            space_id,
            best_similarity,
            config.rag_min_similarity,
            _best_lexical_coverage(store, lexical, question),
            config.rag_min_lexical_coverage,
            redact_secrets(question[:120]),
        )
        return RagAnswer(
            answered=False,
            searched_sources=source_count,
            reason=_not_found_reason(source_count),
        )

    selected = _select_context(config, store, lexical, semantic)
    if not selected:
        return RagAnswer(
            answered=False,
            searched_sources=source_count,
            reason=_not_found_reason(source_count),
        )

    # ------------------------------------------------------------- 2. KAPI
    payload = llm.answer_from_context(question, selected, language)

    # ------------------------------------------------------------- 3. KAPI
    offered = {chunk["chunk_id"] for chunk in selected}
    cited = [chunk_id for chunk_id in payload.get("used_chunk_ids", []) if chunk_id in offered]
    invented = [
        chunk_id for chunk_id in payload.get("used_chunk_ids", []) if chunk_id not in offered
    ]
    if invented:
        _log.warning(
            "RAG uydurma alinti: space=%s verilmeyen parca numaralari=%s", space_id, invented
        )

    if not payload.get("answered") or not payload.get("answer") or not cited:
        return RagAnswer(
            answered=False,
            searched_sources=source_count,
            reason=payload.get("missing") or _not_found_reason(source_count),
        )

    by_id = {chunk["chunk_id"]: chunk for chunk in selected}
    return RagAnswer(
        answered=True,
        answer=payload["answer"],
        citations=[_to_citation(by_id[chunk_id]) for chunk_id in cited],
        searched_sources=source_count,
    )


# 1. kapinin leksik tarafinda kac parcaya bakilacagi. En iyi eslesmeler zaten
# basta; daha derine inmek kapiyi yalnizca gevsetirdi.
_COVERAGE_PROBE = 3


def _best_lexical_coverage(store, lexical: list[tuple[int, float]], question: str) -> float:
    """En iyi leksik eslesmelerin sorguyu ne kadar KARSILADIGI (0..1)."""
    if not lexical:
        return 0.0
    chunk_ids = [chunk_id for chunk_id, _score in lexical[:_COVERAGE_PROBE]]
    rows = store.get_chunks(chunk_ids)
    if not rows:
        return 0.0
    return max(coverage_score(row["text"], question) for row in rows)


def _lexical_is_evidence(config: AppConfig, store, lexical, question: str) -> bool:
    """Leksik eslesme 1. kapiyi acmaya YETECEK kadar guclu mu.

    Eskiden kapi "leksik eslesme VAR MI" diye soruyordu ve tek bir zayif
    eslesme yetiyordu. Olculdu: "filtre kahve nasil yapilir" sorusu, Kalman
    metninde "filtresi" gectigi icin kapiyi aciyor ve LLM'e gidiyordu -- konu
    disi bir soru icin para ve gecikme, ustelik dogruluk yalnizca 3. kapinin
    (alinti zorunlulugu) modelin durustluguna bagli kalmasi demek.

    Olcut SKOR degil KAPSAM: `bm25()` negatif ve korpusa gore degisiyor,
    `ts_rank` pozitif ve baska olcekte. Bir skor esigi iki lehcede baska
    anlama gelirdi. Kapsam ise metinden Python'da hesaplaniyor.

    Anlamsal yol KAPANMIYOR: kapsami dusuk ama anlamca yakin sorular
    ("bu konunun ana fikri ne") benzerlik esiginden gecmeye devam ediyor --
    olculdu, o sorunun token kapsami 0.00.
    """
    if not lexical:
        return False
    return _best_lexical_coverage(store, lexical, question) >= config.rag_min_lexical_coverage


def _not_found_reason(source_count: int) -> str:
    """Eksik olanin kullanicinin SORUSU degil HAVUZU oldugunu soyleyen mesaj."""
    if source_count == 1:
        return "1 kaynakta arandı; bu soruyu karşılayan bir bölüm bulunamadı."
    return f"{source_count} kaynakta arandı; bu soruyu karşılayan bir bölüm bulunamadı."


def _retrieve(
    config: AppConfig,
    store: SQLiteStore,
    llm,
    space_id: str,
    question: str,
    user_id: str,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Iki retriever'i calistirir. Anlamsal yol COKERSE leksik yol devam eder.

    Embedding cagrisi ag ustunden gidiyor ve saglayici gecici olarak
    dusebiliyor. Bunun bedeli "daha zayif arama" olmali, "hic arama yok"
    degil -- leksik yol tamamen yerel ve her zaman calisiyor.
    """
    limit = max(1, config.rag_top_k) * _CANDIDATE_MULTIPLIER
    lexical = store.search_chunks_fts(space_id, build_fts_query(question), limit=limit)

    semantic: list[tuple[int, float]] = []
    try:
        vector = embedding_service.embed_question(config, store, llm, question, user_id)
        semantic = embedding_service.similarity_search(store, config, space_id, vector, limit=limit)
    except Exception as exc:
        _log.warning("Anlamsal arama başarısız, leksik yola devam: %s", redact_secrets(str(exc)))

    return lexical, semantic


def _select_context(
    config: AppConfig,
    store: SQLiteStore,
    lexical: list[tuple[int, float]],
    semantic: list[tuple[int, float]],
) -> list[dict]:
    """Iki siralamayi RRF ile birlestirip baglama girecek parcalari secer.

    RRF (Reciprocal Rank Fusion) SKORLARI DEGIL SIRALARI kullaniyor. Alternatif
    olan "iki skoru agirliklandirip topla" yaklasimi, `bm25()` ile kosinusu ayni
    eksene indirmeyi gerektirirdi: birinin araligi korpusa gore degisiyor,
    digerininki `[-1, 1]`. Kalibre edilemeyen keyfi agirliklar cikardi.
    """
    ranked = _reciprocal_rank_fusion(lexical, semantic)
    if not ranked:
        return []

    rows = {row["chunk_id"]: row for row in store.get_chunks([cid for cid, _ in ranked])}

    selected: list[dict] = []
    per_source: dict[str, int] = {}
    budget = config.rag_context_char_limit
    used_chars = 0

    for chunk_id, _score in ranked:
        if len(selected) >= config.rag_top_k:
            break
        row = rows.get(chunk_id)
        if row is None:
            continue
        # KAYNAK CESITLILIGI: tek bir uzun videonun baglami tamamen doldurmasi,
        # daha iyi cevabi baska bir kaynakta olan sorularda o kaynagi hic
        # gostermemek demek. Ayni gerekce `channel_repeat_penalty`de.
        source_id = row["source_id"]
        if per_source.get(source_id, 0) >= config.rag_max_chunks_per_source:
            continue
        if used_chars + len(row["text"]) > budget and selected:
            continue
        per_source[source_id] = per_source.get(source_id, 0) + 1
        used_chars += len(row["text"])
        selected.append(
            {
                "chunk_id": chunk_id,
                "source_id": source_id,
                "title": row["title"] or "Kaynak",
                "text": row["text"],
                "location": _location_label(row),
                "url": row["url"],
                "start_sec": row["start_sec"],
                "page": row["page"],
            }
        )
    return selected


def _reciprocal_rank_fusion(*rankings: list[tuple[int, float]]) -> list[tuple[int, float]]:
    """`1 / (k + sira)` katkilarini toplar; en iyiden kotuye siralar."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for position, (chunk_id, _score) in enumerate(ranking):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (_RRF_K + position + 1)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def _location_label(row) -> str:
    """Insan okunur konum: video icin `12:34`, dokuman icin `s. 4`."""
    start = row["start_sec"]
    if start is not None:
        return _format_timestamp(start)
    page = row["page"]
    if page is not None:
        return f"s. {page}"
    return ""


def _format_timestamp(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _to_citation(chunk: dict) -> Citation:
    return Citation(
        source_id=chunk["source_id"],
        title=chunk["title"],
        url=_url_with_timestamp(chunk.get("url"), chunk.get("start_sec")),
        start_sec=chunk.get("start_sec"),
        page=chunk.get("page"),
        quote=_shorten(chunk["text"]),
    )


def _url_with_timestamp(url: str | None, start_sec: float | None) -> str | None:
    """Video baglantisina `t=` parametresi ekler.

    Saniye YOKSA parametre de eklenmiyor. `?` varligina bakiliyor cunku kaynak
    URL'i hem `watch?v=ID` hem de kisa (`youtu.be/ID`) bicimde gelebiliyor.
    """
    if not url or start_sec is None:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}t={int(start_sec)}s"


def _shorten(text: str, limit: int = 240) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


# --------------------------------------------------------------------------
# ICERI ALMA (ingest)
#
# Kaynaklar alana IKI yoldan giriyor: tamamlanmis bir calistirmanin videolari
# ve kullanicinin yukledigi dokumanlar. Ikisi de ayni sonuca variyor -- `chunk`
# tablosunda aranabilir parcalar -- ve ayni durum makinesini kullaniyor:
# `pending` -> `indexed` | `no_text` | `failed`.
# --------------------------------------------------------------------------


@dataclass
class IngestReport:
    """Bir iceri alma isinin sonucu. Kullaniciya AYRINTILI rapor veriliyor.

    "5 kaynak eklendi" yetmiyor: hangi videonun transkripti olmadigi, yani
    neyin ARANAMAYACAGI kullanicinin bilmesi gereken sey. Aksi halde o konuda
    "bulamadim" yanitini alip sistemi bozuk sanir.
    """

    added: int = 0
    indexed: int = 0
    skipped_no_text: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    chunks: int = 0
    embedded: int = 0


def ingest_run(
    config: AppConfig,
    store: SQLiteStore,
    llm,
    space_id: str,
    run_id: str,
    user_id: str = DEFAULT_USER_ID,
    progress=None,
) -> IngestReport:
    """Tamamlanmis bir calistirmanin onerilen videolarini alana ekler.

    Transkript icin YENI BIR YOL YAZILMIYOR: mevcut
    `transcript_service.get_transcript` cagriliyor ve saglayici sirasi,
    onbellek, sogutma, hiz siniri mantiginin tamami oldugu gibi devraliniyor.
    Calistirma sirasinda zaten cekilmis transkriptler 30 gunluk onbellekten
    geldigi icin bu adim cogunlukla BEDAVA.
    """
    result = store.get_run(run_id)
    if result is None:
        raise ProviderPermanentError("Çalıştırma tamamlanmamış ya da bulunamadı")

    report = IngestReport()
    recommendations = result.recommendations
    total = len(recommendations)
    if not total:
        return report

    state = RunTranscriptState()
    work_dir = Path(config.cache_dir) / "ingest" / space_id
    work_dir.mkdir(parents=True, exist_ok=True)
    language = result.filters.language

    for index, recommendation in enumerate(recommendations, start=1):
        video = recommendation.video
        source_id = f"video:{video.video_id}"
        if progress:
            progress(index, total, video.title)

        store.add_source(
            space_id,
            source_id,
            kind="video",
            ref_id=video.video_id,
            title=video.title,
            url=video.url,
            language=video.language,
            status="pending",
        )
        report.added += 1

        try:
            transcript = get_transcript(
                config,
                store,
                video,
                str(work_dir),
                state,
                logger=_log,
                preferred_language=language,
                user_id=user_id,
            )
        except Exception as exc:
            message = redact_secrets(str(exc))[:300]
            store.update_source(space_id, source_id, status="failed", error=message)
            report.failed.append(video.title)
            continue

        if transcript.status != "available" or not transcript.text:
            store.update_source(
                space_id,
                source_id,
                status="no_text",
                error="Bu video için transkript bulunamadı",
            )
            report.skipped_no_text.append(video.title)
            continue

        drafts = chunk_transcript(
            transcript.segments,
            transcript.text,
            max_chars=config.rag_chunk_chars,
            overlap_chars=config.rag_chunk_overlap_chars,
        )
        written = store.replace_chunks(space_id, source_id, drafts)
        store.update_source(
            space_id, source_id, status="indexed", chunk_count=written
        )
        report.indexed += 1
        report.chunks += written

    report.embedded = _embed_space(config, store, llm, space_id, user_id, progress)
    store.touch_space(space_id)
    return report


def ingest_document(
    config: AppConfig,
    store: SQLiteStore,
    llm,
    space_id: str,
    source_id: str,
    path: Path,
    title: str,
    user_id: str = DEFAULT_USER_ID,
    progress=None,
) -> IngestReport:
    """Yuklenmis bir dokumani alana ekler.

    Kaynak kaydi cagiran tarafta ZATEN olusturulmus oluyor (yukleme ucu dosyayi
    diske yazarken yaziyor); burada durumu guncelliyoruz. Boylece yarida kalan
    bir is bile arayuzde "bekliyor" olarak gorunuyor, hic gorunmemek yerine.
    """
    report = IngestReport(added=1)
    if progress:
        progress(1, 2, title)

    try:
        pages = parse_document(path, filename=title)
    except ProviderPermanentError as exc:
        store.update_source(
            space_id, source_id, status="failed", error=redact_secrets(str(exc))[:300]
        )
        report.failed.append(title)
        return report

    if not pages:
        store.update_source(
            space_id,
            source_id,
            status="no_text",
            error="Bu dosyadan metin çıkarılamadı (taranmış bir belge olabilir).",
        )
        report.skipped_no_text.append(title)
        return report

    drafts = chunk_document(
        pages,
        max_chars=config.rag_chunk_chars,
        overlap_chars=config.rag_chunk_overlap_chars,
    )
    written = store.replace_chunks(space_id, source_id, drafts)
    store.update_source(space_id, source_id, status="indexed", chunk_count=written)
    report.indexed = 1
    report.chunks = written

    if progress:
        progress(2, 2, title)
    report.embedded = _embed_space(config, store, llm, space_id, user_id, progress)
    store.touch_space(space_id)
    return report


def _embed_space(
    config: AppConfig, store: SQLiteStore, llm, space_id: str, user_id: str, progress
) -> int:
    """Eksik vektorleri uretir. BASARISIZLIK ISI DUSURMUYOR.

    Parcalar zaten yazildi ve leksik arama onlarla CALISIYOR. Embedding
    saglayicisi gecici olarak duserse alan "anlamsal arama olmadan" kullanilir
    hale gelir; eksik vektorler bir sonraki iceri almada tembel bicimde
    tamamlanir (`chunks_missing_embeddings` onlari zaten eksik goruyor).

    Alternatifi tum isi basarisiz saymakti: kullanicinin 20 videosu indekslenmis
    olurdu ama arayuz "basarisiz" derdi ve elindeki calisir durumdaki alani
    gormezdi.
    """
    try:
        return embedding_service.embed_missing(
            config,
            store,
            llm,
            space_id,
            user_id,
            progress=(lambda done, total: progress(done, total, "Vektörler")) if progress else None,
        )
    except Exception as exc:
        _log.warning(
            "Vektörler üretilemedi (leksik arama çalışmaya devam ediyor): %s",
            redact_secrets(str(exc)),
        )
        return 0


def get_source_text(
    store: SQLiteStore, space_id: str, source_id: str
) -> SourceTextResult | None:
    """Ogrenme alanindaki bir kaynagin tam metnini ve parcalarini dondurur."""
    source = store.get_source(space_id, source_id)
    if source is None:
        return None
    raw_chunks = store.list_source_chunks(space_id, source_id)
    chunks = [
        SourceChunk(
            chunk_id=row["chunk_id"],
            ordinal=row["ordinal"],
            text=row["text"],
            start_sec=row["start_sec"],
            end_sec=row["end_sec"],
            page=row["page"],
        )
        for row in raw_chunks
    ]
    full_text = "\n\n".join(chunk.text for chunk in chunks)

    transcript_source = None
    transcript_source = None
    transcript_backend = None
    if source["kind"] == "video":
        with store.connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json FROM transcript_cache
                WHERE video_id = ? AND status = 'available'
                ORDER BY CASE provider WHEN 'asr' THEN 1 ELSE 2 END, updated_at DESC
                LIMIT 1
                """,
                (source["ref_id"],),
            ).fetchone()
            if row:
                cached_data = json.loads(row["payload_json"])
                transcript_source = cached_data.get("source")
                transcript_backend = cached_data.get("backend")

    return SourceTextResult(
        source_id=source["source_id"],
        title=source["title"],
        kind=source["kind"],
        status=source["status"],
        url=source.get("url"),
        language=source.get("language"),
        full_text=full_text,
        chunk_count=len(chunks),
        chunks=chunks,
        transcript_source=transcript_source,
        transcript_backend=transcript_backend,
        error=source.get("error"),
    )


def transcribe_space_source(
    config: AppConfig,
    store: SQLiteStore,
    llm,
    space_id: str,
    source_id: str,
    user_id: str = DEFAULT_USER_ID,
    progress=None,
) -> IngestReport:
    """Calisma odasindaki bir video icin ASR (Whisper) ile transkript cikarir ve indeksler."""
    source = store.get_source(space_id, source_id)
    if source is None:
        raise ProviderPermanentError("Kaynak bulunamadı")
    if source["kind"] != "video":
        raise ProviderPermanentError("Yalnızca video kaynakları için transkript çıkarılabilir")

    report = IngestReport(added=1)
    if progress:
        progress(1, 3, f"ASR başlatılıyor: {source['title']}")

    store.update_source(space_id, source_id, status="pending", error=None)

    asr_config = config.model_copy(update={"enable_asr_fallback": True})
    state = RunTranscriptState()
    work_dir = Path(config.cache_dir) / "ingest" / space_id
    work_dir.mkdir(parents=True, exist_ok=True)

    candidate = VideoCandidate(
        video_id=source["ref_id"],
        url=source["url"] or f"https://www.youtube.com/watch?v={source['ref_id']}",
        title=source["title"],
        language=source.get("language"),
    )

    try:
        transcript = get_transcript(
            asr_config,
            store,
            candidate,
            str(work_dir),
            state,
            logger=_log,
            preferred_language=source.get("language"),
            user_id=user_id,
        )
    except Exception as exc:
        message = redact_secrets(str(exc))[:300]
        store.update_source(space_id, source_id, status="failed", error=message)
        report.failed.append(source["title"])
        return report

    if transcript.status != "available" or not transcript.text:
        error_msg = transcript.error or "Bu video için transkript çıkarılamadı"
        store.update_source(
            space_id,
            source_id,
            status="no_text",
            error=error_msg,
        )
        report.skipped_no_text.append(source["title"])
        return report

    store.put_transcript_cache(transcript, config.transcript_cache_ttl_sec, source.get("language"))

    if progress:
        progress(2, 3, f"Metin parçalanıyor: {source['title']}")

    drafts = chunk_transcript(
        transcript.segments,
        transcript.text,
        max_chars=config.rag_chunk_chars,
        overlap_chars=config.rag_chunk_overlap_chars,
    )
    written = store.replace_chunks(space_id, source_id, drafts)
    store.update_source(
        space_id, source_id, status="indexed", chunk_count=written, error=None
    )
    report.indexed = 1
    report.chunks = written

    if progress:
        progress(3, 3, "Vektörler üretiliyor")
    report.embedded = _embed_space(config, store, llm, space_id, user_id, progress)
    store.touch_space(space_id)
    return report

