"""Calistirma rotalari: baslat, izle, sonuc al, gecmis."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse

from api import sse
from api.deps import (
    build_run_config,
    get_current_user,
    get_default_run_options,
    get_job_runner,
    get_server_config,
    get_store,
    get_user_credentials,
)
from api.routers.auth import PROVIDER as AUTH_PROVIDER
from api.schemas import (
    CreateRunRequest,
    PublishResponse,
    RunAccepted,
    RunListResponse,
    RunResultResponse,
    RunStatus,
    RunSummary,
)
from src.config import RunOptions, ServerConfig, UserCredentials
from src.jobs import JobRunner, JobState, new_job_id
from src.models import PlaylistRequest
from src.services.playlist_publish_service import create_youtube_playlist
from src.services.playlist_service import build_playlist
from src.storage import SQLiteStore
from src.utils.logging_utils import redact_secrets

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.post("", response_model=RunAccepted, status_code=status.HTTP_202_ACCEPTED)
def create_run(
    payload: CreateRunRequest,
    user_id: str = Depends(get_current_user),
    credentials: UserCredentials = Depends(get_user_credentials),
    defaults: RunOptions = Depends(get_default_run_options),
    server: ServerConfig = Depends(get_server_config),
    runner: JobRunner = Depends(get_job_runner),
) -> RunAccepted:
    """Calistirmayi kuyruga alir ve HEMEN doner.

    `build_playlist` 4-40 sn (ASR acikken dakikalar) suruyor; istegi bu sure
    boyunca acik tutmak yerine is numarasi donup ilerlemeyi SSE ile akitiyoruz.
    """
    if not payload.topic.strip():
        # Sabit yerine sayi: starlette surumleri arasinda ad degisti
        # (UNPROCESSABLE_ENTITY -> UNPROCESSABLE_CONTENT).
        raise HTTPException(422, "Konu boş olamaz")

    # Istek govdesindeki ezmeler sunucu varsayilanlarinin uzerine biner.
    overrides = payload.options.model_dump(exclude_none=True)
    options = defaults.model_copy(update=overrides) if overrides else defaults
    config = build_run_config(credentials, options, server)

    run_id = new_job_id()
    request = PlaylistRequest(
        topic=payload.topic.strip(),
        filters=payload.filters,
        create_youtube_playlist=payload.create_youtube_playlist,
    )

    def work(emit):
        return build_playlist(
            config, request, progress_callback=emit, run_id=run_id, user_id=user_id
        )

    handle = runner.submit(run_id, user_id, work)
    return RunAccepted(
        run_id=run_id,
        state=handle.state.value,
        events_url=f"/api/runs/{run_id}/events",
        result_url=f"/api/runs/{run_id}",
    )


@router.get("/{run_id}/events")
def stream_events(
    request: Request,
    run_id: str,
    runner: JobRunner = Depends(get_job_runner),
) -> StreamingResponse:
    """Ilerleme olaylarini SSE olarak akitir.

    Tarayici baglanti koptugunda `Last-Event-ID` basligiyla geri doner; akis o
    indeksten devam eder, kacirilan ilerleme kaybolmaz. Zaten bitmis bir is icin
    birikmis olaylar + son durum tek seferde yollanip kapanir.
    """
    cursor = sse.parse_last_event_id(request.headers.get("Last-Event-ID"))
    return StreamingResponse(
        sse.run_event_stream(runner, run_id, cursor),
        media_type="text/event-stream",
        headers=sse.SSE_HEADERS,
    )


@router.get("/{run_id}/status", response_model=RunStatus)
def get_status(run_id: str, runner: JobRunner = Depends(get_job_runner)) -> RunStatus:
    """SSE kullanmayan istemciler icin yoklama ucu."""
    handle = runner.get(run_id)
    if handle is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bilinmeyen çalıştırma")
    snapshot = handle.snapshot()
    return RunStatus(
        run_id=run_id,
        state=snapshot["state"],
        progress=snapshot["progress"],
        stage=snapshot["stage"],
        message=snapshot["message"],
        error=snapshot["error"],
    )


@router.get("/{run_id}", response_model=RunResultResponse)
def get_run(
    run_id: str,
    user_id: str = Depends(get_current_user),
    runner: JobRunner = Depends(get_job_runner),
    store: SQLiteStore = Depends(get_store),
) -> RunResultResponse:
    """Tamamlanmis sonucu dondurur.

    Once bellekteki is durumuna, sonra kalici gecmise bakar: surec yeniden
    baslamis olsa bile tamamlanmis calistirmalar okunabilir.
    """
    summary = store.get_run_summary(run_id)
    if summary is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bilinmeyen çalıştırma")
    if summary["user_id"] != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bilinmeyen çalıştırma")

    handle = runner.get(run_id)
    state = handle.state.value if handle else (JobState.DONE.value if summary["is_complete"] else JobState.PENDING.value)

    if handle is not None and handle.state is JobState.FAILED:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, handle.error or "Çalıştırma başarısız")

    return RunResultResponse(run_id=run_id, state=state, result=store.get_run(run_id))


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_or_delete_run(
    run_id: str,
    user_id: str = Depends(get_current_user),
    runner: JobRunner = Depends(get_job_runner),
    store: SQLiteStore = Depends(get_store),
) -> None:
    """Calisan isi iptal eder; bitmis calistirmayi gecmisten siler."""
    if runner.cancel(run_id):
        return
    if not store.delete_run(run_id, user_id=user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bilinmeyen çalıştırma")


@router.get("", response_model=RunListResponse)
def list_runs(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user_id: str = Depends(get_current_user),
    store: SQLiteStore = Depends(get_store),
) -> RunListResponse:
    rows = store.list_runs(user_id=user_id, limit=limit, offset=offset)
    return RunListResponse(
        items=[
            RunSummary(
                run_id=row["run_id"],
                topic=row["topic"],
                created_at=row["created_at"],
                is_complete=row["is_complete"],
                filters=row["filters"],
            )
            for row in rows
        ],
        total=store.count_runs(user_id=user_id),
        limit=limit,
        offset=offset,
    )


@router.post("/{run_id}/publish", response_model=PublishResponse)
def publish_run(
    run_id: str,
    user_id: str = Depends(get_current_user),
    credentials: UserCredentials = Depends(get_user_credentials),
    defaults: RunOptions = Depends(get_default_run_options),
    server: ServerConfig = Depends(get_server_config),
    store: SQLiteStore = Depends(get_store),
) -> PublishResponse:
    """Tamamlanmis bir calistirmayi YouTube playlist'i olarak yayinlar.

    Build'den AYRI: yayinlama basarisiz olsa da playlist uretimi kaybolmasin,
    ve kullanici yetkilendirmeyi sonradan yapip tekrar deneyebilsin.
    """
    summary = store.get_run_summary(run_id)
    if summary is None or summary["user_id"] != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bilinmeyen çalıştırma")

    result = store.get_run(run_id)
    if result is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Çalıştırma henüz tamamlanmadı")
    if not result.recommendations:
        raise HTTPException(status.HTTP_409_CONFLICT, "Yayınlanacak öneri yok")

    token_json = store.get_oauth_token(user_id, AUTH_PROVIDER)
    if token_json is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "YouTube hesabı bağlı değil. Önce yetkilendirin."
        )

    config = build_run_config(credentials, defaults, server)
    try:
        publish = create_youtube_playlist(
            config,
            result,
            token_json=token_json,
            on_token_refresh=lambda fresh: store.save_oauth_token(user_id, AUTH_PROVIDER, fresh),
        )
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, redact_secrets(f"Yayınlama başarısız: {exc}")
        ) from exc

    # Sonucu kalici hale getir ki gecmis de yayinlanmis URL'i gostersin.
    result.published_playlist_url = publish.url
    result.warnings.extend(publish.warnings)
    store.finalize_run(run_id, result)

    return PublishResponse(url=publish.url, added=publish.added, warnings=publish.warnings)


@router.get("/{run_id}/export/{artifact}")
def download_export(
    run_id: str,
    artifact: str,
    user_id: str = Depends(get_current_user),
    store: SQLiteStore = Depends(get_store),
) -> FileResponse:
    """Uretilen JSON / Markdown dosyasini indirir."""
    names = {"json": ("result.json", "application/json"), "markdown": ("study_plan.md", "text/markdown")}
    if artifact not in names:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bilinmeyen dosya türü")

    summary = store.get_run_summary(run_id)
    if summary is None or summary["user_id"] != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bilinmeyen çalıştırma")

    result = store.get_run(run_id)
    if result is None or result.exports is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Çalıştırma henüz tamamlanmadı")

    filename, media_type = names[artifact]
    path = Path(result.exports.json_path if artifact == "json" else result.exports.markdown_path)
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "Dosya sunucudan silinmiş")
    return FileResponse(path, media_type=media_type, filename=filename)
