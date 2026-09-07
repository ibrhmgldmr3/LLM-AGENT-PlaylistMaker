"""Ogrenme alani isleri: kaynak ekleme, dokuman yutma, ASR.

`playlist_task` ile ayni gerekce: `api/` altinda DEGIL, cunku bu kodu
calistiran taraf worker ve HTTP katmanini tanimasi gerekmiyor. Yapilandirma
payload'da tasinmiyor, ortamdan kuruluyor (bkz. `src/jobs/runtime.py`).

Payload yalnizca KIMLIKLERI tasiyor. `store` ve `llm` gibi nesneler zaten
seri hale getirilemezdi; onlari yapilandirmadan yeniden kurmak hem tek
mumkun yol hem de dogru olan -- bir baglantiyi kuyruktan gecirmenin anlami yok.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from src.config import AppConfig
from src.jobs.runtime import runtime_config, runtime_store
from src.jobs.tasks import TaskContext, register_task
from src.models import ProgressEvent
from src.providers import create_rag_llm_provider
from src.services import rag_service

INGEST_RUN = "space_ingest_run"
INGEST_DOCUMENT = "space_ingest_document"
TRANSCRIBE_SOURCE = "space_transcribe_source"


def report_message(report) -> str:
    """Iceri alma ozetini kullanicinin okuyabilecegi tek satira cevirir.

    Kapsam DISI kalanlar acikca sayiliyor: kullanici neyin aranamayacagini
    bilmeli, yoksa o konuda "bulamadim" yanitini alip sistemi bozuk sanir.
    """
    parts = [f"{report.indexed} kaynak indekslendi"]
    if report.skipped_no_text:
        parts.append(f"{len(report.skipped_no_text)} kaynakta metin yok")
    if report.failed:
        parts.append(f"{len(report.failed)} kaynak başarısız")
    if report.embedding_pending:
        # Vektoru olmayan parca = o parcada ANLAMSAL arama yok. Kaynak yine
        # "indekslendi" (leksik arama calisiyor), dolayisiyla hicbir sey
        # soylenmezse is TAMAMEN basarili gorunuyor. Canli veritabaninda
        # yasandi: 130 parcanin 66'si vektorsuz kaldi, 6 kaynagin 3'unde HIC
        # vektor yoktu ve kullanici bunu yalnizca "bulamadim" yanitlarindan
        # sezebildi (bkz. `rag_service.IngestReport.embedding_pending`).
        parts.append(f"{report.embedding_pending} parça için anlamsal arama eksik")
    return ", ".join(parts)


def _progress_reporter(emit: Callable[[ProgressEvent], None], stage: str):
    """`rag_service`in bekledigi `(done, total, label)` bildiricisi."""

    def progress(done: int, total: int, label: str) -> None:
        emit(
            ProgressEvent(
                stage=stage,
                message=f"İşleniyor: {label}",
                progress=(done / total) if total else 0.0,
                current=done,
                total=total,
            )
        )

    return progress


def _prepare(context: TaskContext) -> tuple[AppConfig, Any, Any]:
    config = runtime_config()
    return config, runtime_store(config), create_rag_llm_provider(config)


def run_ingest_run(context: TaskContext, emit: Callable[[ProgressEvent], None]) -> Any:
    config, store, llm = _prepare(context)
    emit(ProgressEvent(stage="space_ingest", message="Kaynaklar ekleniyor", progress=0.0))
    report = rag_service.ingest_run(
        config,
        store,
        llm,
        context.payload["space_id"],
        context.payload["run_id"],
        context.user_id,
        progress=_progress_reporter(emit, "space_ingest"),
    )
    emit(ProgressEvent(stage="done", message=report_message(report), progress=1.0))
    return report


def run_ingest_document(context: TaskContext, emit: Callable[[ProgressEvent], None]) -> Any:
    config, store, llm = _prepare(context)
    report = rag_service.ingest_document(
        config,
        store,
        llm,
        context.payload["space_id"],
        context.payload["source_id"],
        # Payload'da DIZE tasiniyor (`Path` JSON'a cevrilemez), ama servis
        # katmani `Path` bekliyor -- donusum SINIRDA, burada yapiliyor.
        # Dizeyi oldugu gibi gecirmek `path.read_text` cagrisinda patliyordu.
        Path(context.payload["target"]),
        context.payload["display_name"],
        context.user_id,
        progress=_progress_reporter(emit, "space_ingest"),
    )
    emit(ProgressEvent(stage="done", message=report_message(report), progress=1.0))
    return report


def run_transcribe_source(context: TaskContext, emit: Callable[[ProgressEvent], None]) -> Any:
    config, store, llm = _prepare(context)
    emit(ProgressEvent(stage="space_transcribe", message="ASR başlatılıyor", progress=0.0))
    report = rag_service.transcribe_space_source(
        config,
        store,
        llm,
        context.payload["space_id"],
        context.payload["source_id"],
        context.user_id,
        progress=_progress_reporter(emit, "space_transcribe"),
    )
    emit(ProgressEvent(stage="done", message=report_message(report), progress=1.0))
    return report


register_task(INGEST_RUN, run_ingest_run)
register_task(INGEST_DOCUMENT, run_ingest_document)
register_task(TRANSCRIBE_SOURCE, run_transcribe_source)
