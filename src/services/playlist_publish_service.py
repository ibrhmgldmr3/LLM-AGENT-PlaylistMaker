from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from src.config import AppConfig
from src.models import PlaylistResult


@dataclass
class PublishResult:
    url: str
    added: int = 0
    warnings: list[str] = field(default_factory=list)


YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube"]


def build_authorization_url(config: AppConfig, redirect_uri: str, state: str) -> tuple[str, str]:
    """Google onay URL'ini ve PKCE dogrulayicisini uretir.

    Web akisinin ilk adimi. `run_local_server` sunucuda tarayici acmaya
    calisiyordu; bir web uygulamasinda bu kavramsal olarak imkansiz.

    `code_verifier` DE dondurulur: kutuphane PKCE'yi varsayilan olarak aciyor
    ve URL'e `code_challenge` koyuyor, ama dogrulayici yalnizca bu `Flow`
    nesnesinde yasiyor. Callback'te yeni bir `Flow` kuruldugunda kayboluyor ve
    Google `invalid_grant: Missing code verifier` donuyor. Cagiran bunu `state`
    ile birlikte saklamali.
    """
    flow = _build_web_flow(config, redirect_uri)
    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        # Yenileme jetonunu garanti altina al: Google bunu yalnizca ilk onayda
        # gonderiyor, `prompt=consent` her seferinde gondermesini saglar.
        prompt="consent",
        state=state,
    )
    return authorization_url, flow.code_verifier


def exchange_code_for_token(
    config: AppConfig, redirect_uri: str, code: str, code_verifier: str | None = None
) -> str:
    """Yetkilendirme kodunu jetona cevirir; saklanacak JSON'u dondurur.

    `code_verifier`, yetkilendirmeyi baslatan istekten tasinmali; PKCE dogrulamasi
    bunsuz tamamlanmaz.
    """
    flow = _build_web_flow(config, redirect_uri)
    if code_verifier:
        flow.code_verifier = code_verifier
    flow.fetch_token(code=code)
    return flow.credentials.to_json()


def _build_web_flow(config: AppConfig, redirect_uri: str):
    from google_auth_oauthlib.flow import Flow

    # `autogenerate_code_verifier` ACIKCA geciliyor. `Flow.__init__` bunu True
    # varsayiyor ama fabrika metotlari kwargs'tan pop ederken kendi varsayilanini
    # dayatiyor: google-auth-oauthlib 1.2.0'da bu `None` (yani PKCE dogrulayicisi
    # URETILMIYOR), 1.4.0'da `True`. Belirtmezsek kurulu surume gore sessizce
    # degisiyor ve eski surumde Google `invalid_grant: Missing code verifier`
    # donuyor. Ikisinde de dogru olan tek davranis: acikca istemek.
    if config.youtube_oauth_client_secret_file:
        flow = Flow.from_client_secrets_file(
            config.youtube_oauth_client_secret_file,
            scopes=YOUTUBE_SCOPES,
            autogenerate_code_verifier=True,
        )
    else:
        flow = Flow.from_client_config(
            _web_client_config(config),
            scopes=YOUTUBE_SCOPES,
            autogenerate_code_verifier=True,
        )
    flow.redirect_uri = redirect_uri
    return flow


def _web_client_config(config: AppConfig) -> dict:
    if not (config.youtube_oauth_client_id and config.youtube_oauth_client_secret):
        raise RuntimeError(
            "YOUTUBE_OAUTH_CLIENT_ID ve YOUTUBE_OAUTH_CLIENT_SECRET tanımlı olmalı"
        )
    return {
        "web": {
            "client_id": config.youtube_oauth_client_id,
            "client_secret": config.youtube_oauth_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def create_youtube_playlist(
    config: AppConfig,
    result: PlaylistResult,
    logger=None,
    token_json: str | None = None,
    on_token_refresh: Callable[[str], None] | None = None,
) -> PublishResult:
    """Playlist'i YouTube'a yayinlar.

    `token_json` verilirse (API yolu) yalnizca o jeton kullanilir; tarayici
    acilmaz. Verilmezse eski dosya tabanli akisa donulur (Streamlit yolu).
    """
    # NOT: YOUTUBE_DATA_API_KEY burada ARANMIYOR. Playlist olusturma OAuth
    # kimlik bilgileriyle yapilir; API anahtari bu akista kullanilmaz.
    # Eskiden anahtar zorunlu tutuluyor ve gecerli yapilandirmalar reddediliyordu.
    if not (
        config.youtube_oauth_client_secret_file
        or (config.youtube_oauth_client_id and config.youtube_oauth_client_secret)
    ):
        raise RuntimeError(
            "Set YOUTUBE_OAUTH_CLIENT_SECRET_FILE or both YOUTUBE_OAUTH_CLIENT_ID and "
            "YOUTUBE_OAUTH_CLIENT_SECRET for official playlist creation"
        )

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError as exc:
        raise RuntimeError(
            "Google OAuth bağımlılıkları kurulu değil. `pip install -r requirements.txt` çalıştırın."
        ) from exc

    if token_json:
        credentials = _credentials_from_token(
            token_json, Credentials, Request, on_token_refresh, logger
        )
    else:
        credentials = _load_credentials(config, YOUTUBE_SCOPES, Credentials, Request, logger=logger)

    service = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    playlist = (
        service.playlists()
        .insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": f"Learning Playlist: {result.topic}"[:150],
                    "description": "Generated by Make A Playlist",
                },
                "status": {"privacyStatus": config.youtube_playlist_privacy_status},
            },
        )
        .execute()
    )
    playlist_id = playlist["id"]
    url = f"https://www.youtube.com/playlist?list={playlist_id}"

    warnings: list[str] = []
    added = 0
    for recommendation in result.recommendations:
        try:
            service.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {
                            "kind": "youtube#video",
                            "videoId": recommendation.video.video_id,
                        },
                    }
                },
            ).execute()
            added += 1
        except HttpError as exc:
            # Tek bir videonun eklenememesi tum playlist'i cope atmamali.
            # Eskiden ilk hata yukari firliyor, kullanicinin hesabinda yarim kalan
            # bir playlist ve hicbir URL bildirimi olmadan kaliyordu.
            warnings.append(
                f"'{recommendation.video.title}' playlist'e eklenemedi: {getattr(exc, 'reason', None) or exc}"
            )
            if logger:
                logger.warning("playlistItems.insert failed for %s: %s", recommendation.video.video_id, exc)

    if logger:
        logger.info("Created YouTube playlist %s with %s/%s items", url, added, len(result.recommendations))
    return PublishResult(url=url, added=added, warnings=warnings)


def _credentials_from_token(
    token_json: str, Credentials, Request, on_token_refresh, logger=None
):
    """Saklanan jetondan kimlik kurar; gerekirse yeniler. TARAYICI ACMAZ."""
    import json

    credentials = Credentials.from_authorized_user_info(json.loads(token_json), YOUTUBE_SCOPES)
    if credentials.valid:
        return credentials

    if not (credentials.expired and credentials.refresh_token):
        raise RuntimeError(
            "YouTube yetkilendirmesi geçersiz. Ayarlardan hesabı yeniden bağlayın."
        )
    credentials.refresh(Request())
    if on_token_refresh:
        # Yenilenmis jetonu geri yaz; aksi halde her yayinlamada yenileme gerekir.
        on_token_refresh(credentials.to_json())
    if logger:
        logger.info("YouTube OAuth token refreshed")
    return credentials


def _load_credentials(config: AppConfig, scopes: list[str], Credentials, Request, logger=None):
    token_path = Path(config.youtube_oauth_token_file)
    token_path.parent.mkdir(parents=True, exist_ok=True)

    credentials = None
    if token_path.exists():
        try:
            credentials = Credentials.from_authorized_user_file(str(token_path), scopes=scopes)
        except Exception as exc:
            if logger:
                logger.warning("Stored OAuth token could not be read: %s", exc)
            credentials = None

    if credentials and credentials.valid:
        return credentials

    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            token_path.write_text(credentials.to_json(), encoding="utf-8")
            return credentials
        except Exception as exc:
            # Yenileme basarisizsa tam yetkilendirmeye geri don (eskiden burada takiliyordu).
            if logger:
                logger.warning("OAuth token refresh failed, re-authorising: %s", exc)

    if not config.youtube_oauth_allow_local_server:
        raise RuntimeError(
            "Geçerli bir OAuth token yok ve YOUTUBE_OAUTH_ALLOW_LOCAL_SERVER=false. "
            f"Yetkilendirmeyi yerel makinede yapıp token dosyasını `{token_path}` konumuna kopyalayın."
        )

    flow = _build_installed_app_flow(config, scopes)
    try:
        # UYARI: bu cagri bir tarayici acar ve SUNUCU tarafinda calisir.
        # Uzak/headless dagitimda YOUTUBE_OAUTH_ALLOW_LOCAL_SERVER=false yapin.
        credentials = flow.run_local_server(port=0, open_browser=True, timeout_seconds=300)
    except TypeError:
        # Eski google-auth-oauthlib surumleri `timeout_seconds` kabul etmiyor.
        credentials = flow.run_local_server(port=0)
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    return credentials


def _build_installed_app_flow(config: AppConfig, scopes: list[str]):
    from google_auth_oauthlib.flow import InstalledAppFlow

    if config.youtube_oauth_client_secret_file:
        return InstalledAppFlow.from_client_secrets_file(config.youtube_oauth_client_secret_file, scopes=scopes)

    client_config = {
        "installed": {
            "client_id": config.youtube_oauth_client_id,
            "client_secret": config.youtube_oauth_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    return InstalledAppFlow.from_client_config(client_config, scopes=scopes)
