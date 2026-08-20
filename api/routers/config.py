"""Yetenek kesfi: arayuz hangi kontrolleri acabilir?"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import (
    build_run_config,
    get_current_user_optional,
    get_default_run_options,
    get_server_config,
    get_store,
    get_user_credentials,
)
from api.schemas import CapabilitiesResponse
from src.config import RunOptions, ServerConfig, UserCredentials
from src.storage import SQLiteStore

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("", response_model=CapabilitiesResponse)
def get_capabilities(
    user_id: str | None = Depends(get_current_user_optional),
    credentials: UserCredentials = Depends(get_user_credentials),
    defaults: RunOptions = Depends(get_default_run_options),
    server: ServerConfig = Depends(get_server_config),
    store: SQLiteStore = Depends(get_store),
) -> CapabilitiesResponse:
    """Hangi ozelliklerin kullanilabilir oldugunu bildirir.

    SIR DONDURMEZ: yalnizca "kurulu mu" bilgisi ve calistirma varsayilanlari.
    Anahtar degerleri hicbir kosulda bu ucun cevabina girmez.
    """
    config = build_run_config(credentials, defaults, server)
    capabilities = config.public_capabilities()

    # Kalan hak yalnizca sinir VARSA anlamli; `None` "sinir yok" demek ve
    # arayuz o durumda hicbir sey gostermiyor.
    # `user_id is None` = oturumsuz istek. Bu uc BILEREK 401 dondurmuyor
    # (bkz. `get_current_user_optional`): kullaniciya ozgu alan bos kaliyor,
    # yapilandirma bilgisi yine de veriliyor.
    remaining = None
    if user_id is not None and server.max_runs_per_user_per_day:
        used = store.count_runs_since(user_id)
        remaining = max(0, server.max_runs_per_user_per_day - used)

    return CapabilitiesResponse(
        **capabilities,
        runs_remaining_today=remaining,
        defaults=defaults.model_dump(),
    )
