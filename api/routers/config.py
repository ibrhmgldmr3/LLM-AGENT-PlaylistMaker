"""Yetenek kesfi: arayuz hangi kontrolleri acabilir?"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import build_run_config, get_default_run_options, get_server_config, get_user_credentials
from api.schemas import CapabilitiesResponse
from src.config import RunOptions, ServerConfig, UserCredentials

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("", response_model=CapabilitiesResponse)
def get_capabilities(
    credentials: UserCredentials = Depends(get_user_credentials),
    defaults: RunOptions = Depends(get_default_run_options),
    server: ServerConfig = Depends(get_server_config),
) -> CapabilitiesResponse:
    """Hangi ozelliklerin kullanilabilir oldugunu bildirir.

    SIR DONDURMEZ: yalnizca "kurulu mu" bilgisi ve calistirma varsayilanlari.
    Anahtar degerleri hicbir kosulda bu ucun cevabina girmez.
    """
    config = build_run_config(credentials, defaults, server)
    capabilities = config.public_capabilities()
    return CapabilitiesResponse(**capabilities, defaults=defaults.model_dump())
