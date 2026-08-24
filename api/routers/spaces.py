"""Ogrenme alani rotalari: alan yonet, kaynak ekle, soru sor.

YENI ALTYAPI YOK. Uzun suren iceri alma isleri mevcut `InProcessJobRunner` ve
`api/sse.py` uzerinden yurutuluyor -- olay gunlugu, `Last-Event-ID` ile devam
etme ve coklu abone destegi Faz 2b'de zaten cozulmustu.

SAHIPLIK: baskasinin alani **404** doner, 403 degil. Ayri bir "yetkisiz"
yaniti, var olmayan bir kimlikle BASKASINA ait olani ayirt edilebilir kilardi;
ayni gerekce `runs.py:_owned_handle` icinde de yazili.

SSE `done` olayindaki `run_id` alani burada ICERI ALMA ISININ kimligini
tasiyor. Alan adi `runs` sozlesmesinden geliyor ve BILEREK degistirilmedi:
istemci tarafinda tek bir ilerleme akisi mantigi iki ozelligi birden
kapsayabiliyor.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse

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
from api.schemas import (
    AddRunSourceRequest,
    AskRequest,
    CreateSpaceRequest,
    IngestAccepted,
    SpaceDetail,
    SpaceListResponse,
    SpaceSummary,
)
from src.config import AppConfig, RunOptions, ServerConfig, UserCredentials
from src.jobs import JobRunner, new_job_id
from src.models import ProgressEvent, RagAnswer, SpaceSource
from src.providers import create_rag_llm_provider
from src.services import rag_service, space_retention
from src.services.document_parser import SUPPORTED_EXTENSIONS
from src.storage import SQLiteStore
from src.utils.logging_utils import redact_secrets

router = APIRouter(prefix="/api/spaces", tags=["spaces"])

# Yukleme okunurken kullanilan blok boyutu. Dosya BELLEGE TOPLANMIYOR:
# `max_upload_bytes` 20 MB olsa da es zamanli birkac yukleme sunucuyu
# sisirebilir ve `UploadFile.read()` tek seferde hepsini bellege alirdi.
_UPLOAD_CHUNK = 1024 * 1024


def _require_rag(config: AppConfig) -> None:
    """RAG kapaliyken uclar ACIKCA reddediyor.

    Sessizce bos sonuc donmek daha kotu olurdu: kullanici ozelligin BOZUK
    oldugunu dusunurdu, oysa yalnizca yapilandirilmamis.
    """
    if not config.enable_rag:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Öğrenme alanı kapalı. Sunucuda ENABLE_RAG=true yapın.",
        )
    if not config.llm_api_key():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "LLM sağlayıcı anahtarı yapılandırılmamış (GEMINI_API_KEY veya TOGETHER_API_KEY)",
        )


def _config(
    credentials: UserCredentials, defaults: RunOptions, server: ServerConfig
) -> AppConfig:
    config = build_run_config(credentials, defaults, server)
    _require_rag(config)
    return config


def _owned_space(store: SQLiteStore, space_id: str, user_id: str) -> dict:
    space = store.get_space(space_id, user_id)
    if space is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bilinmeyen öğrenme alanı")
    return space


def _detail(store: SQLiteStore, space: dict) -> SpaceDetail:
    sources = [SpaceSource.model_validate(row) for row in store.list_sources(space["space_id"])]
    return SpaceDetail(
        space_id=space["space_id"],
        name=space["name"],
        created_at=space["created_at"],
        updated_at=space["updated_at"],
        source_count=len(sources),
        chunk_count=store.count_chunks(space["space_id"]),
        sources=sources,
    )


# ------------------------------------------------------------------- alanlar


@router.post("", response_model=SpaceSummary, status_code=status.HTTP_201_CREATED)
def create_space(
    payload: CreateSpaceRequest,
    user_id: str = Depends(get_current_user),
    credentials: UserCredentials = Depends(get_user_credentials),
    defaults: RunOptions = Depends(get_default_run_options),
    server: ServerConfig = Depends(get_server_config),
    store: SQLiteStore = Depends(get_store),
) -> SpaceSummary:
    config = _config(credentials, defaults, server)

    # Tavan var cunku maliyet (embedding + depolama) SUNUCU SAHIBINDE --
    # anahtarlar paylasimli (bkz. docs/react-migration-plan.md §2).
    if store.count_spaces(user_id) >= config.max_spaces_per_user:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"En fazla {config.max_spaces_per_user} öğrenme alanı oluşturabilirsiniz.",
        )

    space_id = uuid4().hex
    store.create_space(space_id, user_id, payload.name.strip())
    space = store.get_space(space_id, user_id)
    return SpaceSummary(
        space_id=space_id,
        name=space["name"],
        created_at=space["created_at"],
        updated_at=space["updated_at"],
    )


@router.get("", response_model=SpaceListResponse)
def list_spaces(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user_id: str = Depends(get_current_user),
    store: SQLiteStore = Depends(get_store),
) -> SpaceListResponse:
    rows = store.list_spaces(user_id, limit=limit, offset=offset)
    return SpaceListResponse(
        items=[SpaceSummary(**row) for row in rows],
        total=store.count_spaces(user_id),
        limit=limit,
        offset=offset,
    )


@router.get("/{space_id}", response_model=SpaceDetail)
def get_space(
    space_id: str,
    user_id: str = Depends(get_current_user),
    store: SQLiteStore = Depends(get_store),
) -> SpaceDetail:
    return _detail(store, _owned_space(store, space_id, user_id))


# `response_class=Response` icin bkz. `api/routers/auth.py` — FastAPI 0.116'da
# `-> None` + 204 birlesimi uygulamayi import zamaninda kiriyor.
@router.delete("/{space_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_space(
    space_id: str,
    user_id: str = Depends(get_current_user),
    credentials: UserCredentials = Depends(get_user_credentials),
    defaults: RunOptions = Depends(get_default_run_options),
    server: ServerConfig = Depends(get_server_config),
    store: SQLiteStore = Depends(get_store),
) -> Response:
    """Alani veritabanindan ve DISKTEN siler."""
    config = build_run_config(credentials, defaults, server)
    if not space_retention.delete_space(config, store, space_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bilinmeyen öğrenme alanı")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ kaynaklar


@router.post(
    "/{space_id}/sources/run",
    response_model=IngestAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def add_run_source(
    space_id: str,
    payload: AddRunSourceRequest,
    user_id: str = Depends(get_current_user),
    credentials: UserCredentials = Depends(get_user_credentials),
    defaults: RunOptions = Depends(get_default_run_options),
    server: ServerConfig = Depends(get_server_config),
    store: SQLiteStore = Depends(get_store),
    runner: JobRunner = Depends(get_job_runner),
) -> IngestAccepted:
    """Tamamlanmis bir calistirmanin videolarini alana ekler.

    HEMEN doner: transkript cekme + parcalama + gomme dakikalar surebiliyor
    (ASR acikken daha da fazla) ve bir HTTP istegi bunu bekleyemez.
    """
    config = _config(credentials, defaults, server)
    _owned_space(store, space_id, user_id)

    summary = store.get_run_summary(payload.run_id)
    if summary is None or summary["user_id"] != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bilinmeyen çalıştırma")
    if not summary["is_complete"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Çalıştırma henüz tamamlanmadı")

    llm = create_rag_llm_provider(config)
    job_id = new_job_id()

    def work(emit):
        def progress(done: int, total: int, label: str) -> None:
            emit(
                ProgressEvent(
                    stage="space_ingest",
                    message=f"İşleniyor: {label}",
                    progress=(done / total) if total else 0.0,
                    current=done,
                    total=total,
                )
            )

        emit(ProgressEvent(stage="space_ingest", message="Kaynaklar ekleniyor", progress=0.0))
        report = rag_service.ingest_run(
            config, store, llm, space_id, payload.run_id, user_id, progress=progress
        )
        emit(ProgressEvent(stage="done", message=_report_message(report), progress=1.0))
        return report

    runner.submit(job_id, user_id, work)
    return IngestAccepted(
        job_id=job_id,
        space_id=space_id,
        events_url=f"/api/spaces/{space_id}/jobs/{job_id}/events",
    )


@router.post(
    "/{space_id}/sources/document",
    response_model=IngestAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def add_document_source(
    space_id: str,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    user_id: str = Depends(get_current_user),
    credentials: UserCredentials = Depends(get_user_credentials),
    defaults: RunOptions = Depends(get_default_run_options),
    server: ServerConfig = Depends(get_server_config),
    store: SQLiteStore = Depends(get_store),
    runner: JobRunner = Depends(get_job_runner),
) -> IngestAccepted:
    """Bir dokuman yukler ve indekslemeyi kuyruga alir.

    Dosya SENKRON yaziliyor, indeksleme arka planda: yazma hizlidir ve
    basarisiz olursa kullanici bunu ANINDA ogrenmeli (boyut/tur reddi bir
    arka plan isinin icinde kaybolmamali).
    """
    config = _config(credentials, defaults, server)
    _owned_space(store, space_id, user_id)

    documents = [s for s in store.list_sources(space_id) if s["kind"] == "document"]
    if len(documents) >= config.max_documents_per_space:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Bir alana en fazla {config.max_documents_per_space} doküman yükleyebilirsiniz.",
        )

    # Kullanicinin verdigi ad yalnizca GORUNEN BASLIK. Saklanan ad sunucu
    # tarafindan uretiliyor, boylece yol gecisi ya da gecersiz karakter
    # olasiligi tamamen ortadan kalkiyor.
    display_name = (title or file.filename or "belge").strip()[:200]
    suffix = Path(display_name).suffix.lower() or Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Desteklenmeyen dosya türü: {suffix or '(uzantısız)'}. "
            f"Desteklenenler: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )

    stored_name = f"{uuid4().hex}{suffix}"
    target_dir = space_retention.space_upload_dir(config, user_id, space_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / stored_name

    written = 0
    try:
        with open(target, "wb") as handle:
            while True:
                block = await file.read(_UPLOAD_CHUNK)
                if not block:
                    break
                written += len(block)
                if written > config.max_upload_bytes:
                    # Sinir OKUMA SIRASINDA uygulaniyor, `Content-Length`e
                    # bakilarak degil: o baslik istemcinin BEYANI ve yanlis
                    # olabilir. Yarim dosya hemen siliniyor.
                    # Sabit yerine sayi: starlette surumleri arasinda ad
                    # degisti (REQUEST_ENTITY_TOO_LARGE -> CONTENT_TOO_LARGE).
                    # Ayni gerekce `runs.py`de 422 icin de yazili.
                    raise HTTPException(
                        413,
                        f"Dosya çok büyük (en fazla {config.max_upload_bytes // (1024 * 1024)} MB).",
                    )
                handle.write(block)
    except Exception:
        target.unlink(missing_ok=True)
        raise

    if written == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Dosya boş.")

    source_id = f"doc:{stored_name}"
    store.add_source(
        space_id,
        source_id,
        kind="document",
        ref_id=stored_name,
        title=display_name,
        status="pending",
        byte_size=written,
    )

    llm = create_rag_llm_provider(config)
    job_id = new_job_id()

    def work(emit):
        def progress(done: int, total: int, label: str) -> None:
            emit(
                ProgressEvent(
                    stage="space_ingest",
                    message=f"İşleniyor: {label}",
                    progress=(done / total) if total else 0.0,
                    current=done,
                    total=total,
                )
            )

        report = rag_service.ingest_document(
            config, store, llm, space_id, source_id, target, display_name, user_id,
            progress=progress,
        )
        emit(ProgressEvent(stage="done", message=_report_message(report), progress=1.0))
        return report

    runner.submit(job_id, user_id, work)
    return IngestAccepted(
        job_id=job_id,
        space_id=space_id,
        events_url=f"/api/spaces/{space_id}/jobs/{job_id}/events",
    )


@router.delete(
    "/{space_id}/sources/{source_id:path}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_source(
    space_id: str,
    source_id: str,
    user_id: str = Depends(get_current_user),
    credentials: UserCredentials = Depends(get_user_credentials),
    defaults: RunOptions = Depends(get_default_run_options),
    server: ServerConfig = Depends(get_server_config),
    store: SQLiteStore = Depends(get_store),
) -> Response:
    """Kaynagi, parcalarini ve (dokumansa) diskteki dosyasini siler.

    `{source_id:path}` cunku kimlik `doc:0a1b.pdf` bicimde ve iki nokta
    tasiyor; varsayilan yol donusturucu bunu sorunsuz gecirse de bicimi
    acikca belirtmek ileride ayrac degisirse kirilmayi onluyor.
    """
    config = build_run_config(credentials, defaults, server)
    _owned_space(store, space_id, user_id)

    source = store.get_source(space_id, source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bilinmeyen kaynak")

    store.delete_source(space_id, source_id)
    if source["kind"] == "document":
        space_retention.delete_source_file(config, user_id, space_id, source["ref_id"])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------- akis


@router.get("/{space_id}/jobs/{job_id}/events")
def stream_ingest_events(
    request: Request,
    space_id: str,
    job_id: str,
    user_id: str = Depends(get_current_user),
    runner: JobRunner = Depends(get_job_runner),
) -> StreamingResponse:
    """Iceri alma ilerlemesini SSE olarak akitir. `runs` ile AYNI mekanizma."""
    cursor = sse.parse_last_event_id(request.headers.get("Last-Event-ID"))
    return StreamingResponse(
        sse.run_event_stream(runner, job_id, cursor, user_id=user_id),
        media_type="text/event-stream",
        headers=sse.SSE_HEADERS,
    )


# ---------------------------------------------------------------------- soru


@router.post("/{space_id}/ask", response_model=RagAnswer)
def ask(
    space_id: str,
    payload: AskRequest,
    user_id: str = Depends(get_current_user),
    credentials: UserCredentials = Depends(get_user_credentials),
    defaults: RunOptions = Depends(get_default_run_options),
    server: ServerConfig = Depends(get_server_config),
    store: SQLiteStore = Depends(get_store),
) -> RagAnswer:
    """Alandaki kaynaklara dayanarak soruyu yanitlar.

    SENKRON: tek bir erisim + tek bir LLM cagrisi, olculen sure birkac saniye.
    Arka plana atmak, istemciye ikinci bir akis mekanizmasi tasittirirdi ve
    kazanci yoktu.

    `answered=False` bir HATA DEGIL, 200 ile donuyor. Ozelligin asil vaadi bu
    ve onu HTTP hatasi yapmak, arayuzun onu kirmizi bir kutuda gostermesine ve
    kullanicinin sistemi bozuk sanmasina yol acardi.
    """
    config = _config(credentials, defaults, server)
    _owned_space(store, space_id, user_id)

    llm = create_rag_llm_provider(config)
    try:
        return rag_service.answer_question(
            config, store, llm, space_id, payload.question, payload.language, user_id
        )
    except HTTPException:
        raise
    except Exception as exc:
        # Saglayici hatalari `api/main.py`deki isleyicilere gidiyor; buraya
        # dusen her sey beklenmedik. Mesaj MASKELENIYOR: anahtarlar istisna
        # metnine sizabiliyor.
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, redact_secrets(f"Yanıt üretilemedi: {exc}")
        ) from exc


def _report_message(report) -> str:
    """Iceri alma ozetini kullanicinin okuyabilecegi tek satira cevirir.

    Kapsam DISI kalanlar acikca sayiliyor: kullanici neyin aranamayacagini
    bilmeli, yoksa o konuda "bulamadim" yanitini alip sistemi bozuk sanir.
    """
    parts = [f"{report.indexed} kaynak indekslendi"]
    if report.skipped_no_text:
        parts.append(f"{len(report.skipped_no_text)} kaynakta metin yok")
    if report.failed:
        parts.append(f"{len(report.failed)} kaynak başarısız")
    return ", ".join(parts)
