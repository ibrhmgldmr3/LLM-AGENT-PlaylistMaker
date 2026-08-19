"""Kullanicinin kendi API anahtarlari (BYOK).

Neden kullanici basina: YouTube Data API kotasi PROJE basina gunde 10.000 birim
ve bir calistirma ~1.200 birim tuketiyor. Paylasimli anahtarla ikinci kullanici
gunu bitirirdi.

Bu uclar anahtar DEGERLERINI hicbir zaman dondurmuyor; yalnizca "girilmis mi"
bilgisini veriyor. Bir kez yazilan deger geri okunamaz -- kullanici unuttuysa
yenisini girer. Boylece bir XSS ya da yanlis loglama anahtari disari tasiyamaz.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from api.deps import get_current_user, get_server_config, get_store
from src.config import ServerConfig
from src.storage import SQLiteStore

router = APIRouter(prefix="/api/credentials", tags=["credentials"])

# BYOK ile girilebilecek alanlar. Beyaz liste bilerek: rastgele bir ad
# yazilarak yapilandirmanin baska bir alanina deger sokulmasi engelleniyor.
# `required` alanlarin HICBIRI artik True degil: kullanici Gemini VEYA Together
# kullanabiliyor, ikisi birden zorunlu degil. "En az birinin girilmis olmasi"
# kosulu `api/deps.py` icinde, calistirma baslatilirken kontrol ediliyor.
EDITABLE = {
    "gemini_api_key": {"required": False, "label": "Gemini API anahtarı"},
    "together_api_key": {"required": False, "label": "Together.ai API anahtarı"},
    "youtube_data_api_key": {"required": False, "label": "YouTube Data API anahtarı"},
}


class CredentialValue(BaseModel):
    value: str = Field(min_length=1, max_length=500)


class CredentialItem(BaseModel):
    name: str
    label: str
    required: bool
    configured: bool


class CredentialsResponse(BaseModel):
    # `single_user` kurulumda anahtarlar `.env`'den geliyor ve buradan
    # duzenlenemiyor; arayuz buna gore salt-okunur gorunuyor.
    editable: bool
    items: list[CredentialItem]


def _guard_editable(server: ServerConfig) -> None:
    if server.auth_mode != "multi_user":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Tek kullanıcılı kurulumda anahtarlar `.env` dosyasından okunur",
        )


@router.get("", response_model=CredentialsResponse)
def list_credentials(
    user_id: str = Depends(get_current_user),
    server: ServerConfig = Depends(get_server_config),
    store: SQLiteStore = Depends(get_store),
) -> CredentialsResponse:
    editable = server.auth_mode == "multi_user"
    stored = set(store.get_user_credentials(user_id)) if editable else set()
    return CredentialsResponse(
        editable=editable,
        items=[
            CredentialItem(
                name=name,
                label=str(meta["label"]),
                required=bool(meta["required"]),
                configured=(name in stored),
            )
            for name, meta in EDITABLE.items()
        ],
    )


@router.put("/{name}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def save_credential(
    name: str,
    payload: CredentialValue,
    user_id: str = Depends(get_current_user),
    server: ServerConfig = Depends(get_server_config),
    store: SQLiteStore = Depends(get_store),
) -> Response:
    _guard_editable(server)
    if name not in EDITABLE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bilinmeyen anahtar")
    store.save_user_credential(user_id, name, payload.value.strip())
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_credential(
    name: str,
    user_id: str = Depends(get_current_user),
    server: ServerConfig = Depends(get_server_config),
    store: SQLiteStore = Depends(get_store),
) -> Response:
    _guard_editable(server)
    if name not in EDITABLE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bilinmeyen anahtar")
    if not store.delete_user_credential(user_id, name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Kayıtlı anahtar yok")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
