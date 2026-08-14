"""YouTube OAuth — web redirect akisi.

`run_local_server` sunucuda tarayici acmaya calisiyordu; bir web uygulamasinda
bu imkansiz. Standart uc adimli akis:

    1. GET  /api/auth/youtube/start    -> Google onay URL'i
    2. (kullanici Google'da onaylar)
    3. GET  /api/auth/youtube/callback -> kod -> jeton -> kullaniciya bagli saklanir
"""

from __future__ import annotations

import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi import Response
from fastapi.responses import RedirectResponse

from api.deps import build_run_config, get_current_user, get_default_run_options, get_server_config, get_store, get_user_credentials
from src.config import RunOptions, ServerConfig, UserCredentials
from src.services.playlist_publish_service import build_authorization_url, exchange_code_for_token
from src.storage import SQLiteStore
from src.utils.logging_utils import redact_secrets

router = APIRouter(prefix="/api/auth/youtube", tags=["auth"])

PROVIDER = "youtube"
STATE_TTL_SECONDS = 600

# CSRF `state` degerleri. Surec-ici: tek instance icin yeterli, cok kullanicili
# dagitimda paylasimli bir depoya (Redis) tasinmali.
_pending_states: dict[str, tuple[str, float]] = {}


def _remember_state(user_id: str) -> str:
    _expire_states()
    state = secrets.token_urlsafe(24)
    _pending_states[state] = (user_id, time.monotonic() + STATE_TTL_SECONDS)
    return state


def _consume_state(state: str) -> str | None:
    _expire_states()
    entry = _pending_states.pop(state, None)
    return entry[0] if entry else None


def _expire_states() -> None:
    now = time.monotonic()
    for key in [key for key, (_user, expiry) in _pending_states.items() if expiry < now]:
        _pending_states.pop(key, None)


def _redirect_uri(request: Request) -> str:
    """Callback ucunun mutlak adresi; Google Console'a da bu yazilmali."""
    return str(request.url_for("youtube_callback"))


@router.get("/status")
def get_status(
    user_id: str = Depends(get_current_user),
    store: SQLiteStore = Depends(get_store),
    credentials: UserCredentials = Depends(get_user_credentials),
) -> dict:
    """Hesap bagli mi, ve baglanti kurulabilir mi?"""
    configured = bool(
        credentials.youtube_oauth_client_secret_file
        or (credentials.youtube_oauth_client_id and credentials.youtube_oauth_client_secret)
    )
    return {
        "connected": store.get_oauth_token(user_id, PROVIDER) is not None,
        "configured": configured,
    }


@router.get("/start")
def start_authorization(
    request: Request,
    user_id: str = Depends(get_current_user),
    credentials: UserCredentials = Depends(get_user_credentials),
    defaults: RunOptions = Depends(get_default_run_options),
    server: ServerConfig = Depends(get_server_config),
) -> dict:
    """Google onay URL'ini uretir. Tarayici bu adrese YONLENDIRILIR."""
    config = build_run_config(credentials, defaults, server)
    state = _remember_state(user_id)
    try:
        url = build_authorization_url(config, _redirect_uri(request), state)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, redact_secrets(str(exc))) from exc
    return {"authorization_url": url, "state": state}


@router.get("/callback", name="youtube_callback")
def youtube_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    store: SQLiteStore = Depends(get_store),
    credentials: UserCredentials = Depends(get_user_credentials),
    defaults: RunOptions = Depends(get_default_run_options),
    server: ServerConfig = Depends(get_server_config),
) -> RedirectResponse:
    """Google'in geri dondugu uc. Kodu jetona cevirip kullaniciya baglar."""
    if error:
        return RedirectResponse(f"/?youtube_auth=error&reason={error}")
    if not code or not state:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Eksik `code` veya `state`")

    # `state` dogrulamasi CSRF korumasi: bu akisi biz baslatmis olmaliyiz.
    user_id = _consume_state(state)
    if user_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Geçersiz veya süresi dolmuş `state`")

    config = build_run_config(credentials, defaults, server)
    try:
        token_json = exchange_code_for_token(config, _redirect_uri(request), code)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, redact_secrets(f"Jeton alınamadı: {exc}")
        ) from exc

    store.save_oauth_token(user_id, PROVIDER, token_json)
    return RedirectResponse("/?youtube_auth=ok")


# `response_class=Response` ACIK olarak veriliyor: FastAPI 0.116'da `-> None`
# donus anotasyonu yanit modeli sayiliyor ve 204 govde kabul etmedigi icin
# uygulama IMPORT ZAMANINDA patliyor. 0.118'de bu davranis degismis; iki surumde
# de calissin diye yanit sinifi elle belirtiliyor.
@router.delete("", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def disconnect(
    user_id: str = Depends(get_current_user),
    store: SQLiteStore = Depends(get_store),
) -> Response:
    if not store.delete_oauth_token(user_id, PROVIDER):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bağlı hesap yok")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
