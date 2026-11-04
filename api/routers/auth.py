"""YouTube OAuth — web redirect akisi.

`run_local_server` sunucuda tarayici acmaya calisiyordu; bir web uygulamasinda
bu imkansiz. Standart uc adimli akis:

    1. GET  /api/auth/youtube/start    -> Google onay URL'i + baglama cerezi
    2. (kullanici Google'da onaylar)
    3. GET  /api/auth/youtube/callback -> cerez == state mi -> kod -> jeton
                                          -> kullaniciya bagli saklanir
"""

from __future__ import annotations

import secrets
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi import Response
from fastapi.responses import RedirectResponse

from api.deps import SESSION_COOKIE, get_current_user, get_server_config, get_store
from src.config import ServerConfig
from src.services.playlist_publish_service import (
    LOGIN_SCOPES,
    build_authorization_url,
    exchange_code_for_token,
)
from src.storage import DEFAULT_USER_ID, SQLiteStore
from src.utils.logging_utils import redact_secrets


def _request_is_https(request: Request) -> bool:
    """Istek KULLANICIYA kadar HTTPS mi.

    `request.url.scheme` yalnizca uygulamaya gelen BACAGI gosteriyor. Tipik
    kurulumda (nginx/Caddy/Traefik + uvicorn) TLS ters proxy'de sonlaniyor ve
    uygulamaya duz `http` geliyor; bu durumda `secure` bayragi yanlislikla
    dusuyor ve oturum cerezi sifresiz baglantida da gonderilebilir hale
    geliyordu. Proxy'nin bildirdigi orijinal semayi once ona soruyoruz.

    Basligin ilk degeri aliniyor: zincirli proxy'lerde `X-Forwarded-Proto`
    virgulle ayrilmis liste olabiliyor ve ISTEMCIYE en yakin olan bastaki.
    """
    forwarded = request.headers.get("x-forwarded-proto")
    if forwarded:
        return forwarded.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


router = APIRouter(prefix="/api/auth/youtube", tags=["auth"])

PROVIDER = "youtube"
STATE_TTL_SECONDS = 600

# Bekleyen state sayisinin ust siniri. Bu uc OTURUM GEREKTIRMIYOR (gerektiremez,
# bkz. `start_authorization`), yani tek sinir TTL olsaydi 10 dakikalik pencere
# icinde sozlugu istedigi kadar buyutebilen kimliksiz bir yol kalirdi.
MAX_PENDING_STATES = 10_000

# `state`i akisi BASLATAN tarayiciya baglayan cerez.
#
# Depodaki kayit tek basina yetmiyordu: `state` tek kullanimlik ve suresi
# dolan bir sirdi ama HANGI tarayiciya ait oldugunu bilmiyordu. Saldirgan
# akisi kendi tarayicisinda baslatip Google'dan donen `code` + `state`
# baglantisini kurbana yolladiginda callback'i acan KURBAN, saldirganin
# hesabiyla oturum aciyordu (login CSRF). Kurbanin bundan sonra yukledigi
# dokumanlar ve yayinladigi listeler saldirganin hesabina gidiyordu.
#
# Cerez yalnizca bu akisin uclarina gidiyor (`path`) ve JavaScript'ten
# okunamiyor. `SameSite=Lax` Google'dan donen ust duzey GET yonlendirmesinde
# gonderiliyor; capraz siteli alt istekler onu tasimiyor.
#
# Bilinen sinir: ayni tarayicida iki akis ust uste baslatilirsa (iki sekme)
# YALNIZCA sonuncusu tamamlanabilir; ilki "yeniden baslatin" der. Tek akis
# icin kurulmus bir giris ekraninda bu kabul edilebilir bir bedel.
STATE_COOKIE = "map_oauth_state"
STATE_COOKIE_PATH = "/api/auth/youtube"

# Bekleyen yetkilendirmeler ARTIK PAYLASIMLI DEPODA (bkz.
# `SQLiteStore.put_oauth_state` / `consume_oauth_state`).
#
# Onceden surec icinde bir sozlukteydi ve bu, web tarafini cogaltmanin
# onundeki somut engellerden biriydi: kullanici A replikasinda akisi
# baslatiyor, Google B replikasina donuyor ve state bulunamadigi icin giris
# basarisiz oluyordu. Tek kullanimlik tuketme, TTL ve kapasite tavani ayni
# semantikle depoya tasindi; tek fark durumun nerede yasadigi.
#
# `code_verifier` PKCE icin ZORUNLU olarak saklaniyor: yalnizca URL'i ureten
# `Flow` nesnesinde yasadigi icin callback'e baska turlu tasinamiyor. Depoda
# `oauth_token` ile ayni sekilde SIFRELENIYOR.


def _redirect_uri(request: Request, server: ServerConfig) -> str:
    """Callback ucunun mutlak adresi; Google Console'a da bu yazilmali.

    `/start` ve `/callback` AYNI degeri uretmeli: Google kod degisiminde
    `redirect_uri`nin yetkilendirmedekiyle birebir ayni olmasini istiyor.

    `PUBLIC_BASE_URL` tanimliysa TEK YETKILI kaynak o. Tanimsizsa adres
    istekten kuruluyor ama SEMA `_request_is_https` ile -- oturum cerezinin
    `secure` kararini veren AYNI kural. Eskiden `request.url_for` oldugu gibi
    kullaniliyordu: TLS ters proxy'de sonlandiginda uygulamaya `http` geliyor
    (uvicorn `X-Forwarded-Proto`ya varsayilan olarak yalnizca 127.0.0.1'den
    guveniyor), Google'a `http://...` gidiyor ve kayitli `https://...` ile
    eslesmiyordu. Cerez basligi okurken adres okumuyordu; iki karar
    ayrismisti. `Host`un da yanlis olabildigi kurulumlar (varsayilan nginx)
    icin cozum `PUBLIC_BASE_URL`.
    """
    url = request.url_for("youtube_callback")
    if server.public_base_url:
        return f"{server.public_base_url}{url.path}"
    return str(url.replace(scheme="https" if _request_is_https(request) else "http"))


def _oauth_client_configured(server: ServerConfig) -> bool:
    """Kurulumda OAuth ISTEMCISI tanimli mi.

    Kullanicinin anahtarlarindan bagimsiz: istemci uygulamaya ait, bu yuzden
    `ServerConfig`ten okunuyor.
    """
    return bool(
        server.youtube_oauth_client_secret_file
        or (server.youtube_oauth_client_id and server.youtube_oauth_client_secret)
    )


@router.get("/status")
def get_status(
    user_id: str = Depends(get_current_user),
    store: SQLiteStore = Depends(get_store),
    server: ServerConfig = Depends(get_server_config),
) -> dict:
    """Hesap bagli mi, ve baglanti kurulabilir mi?"""
    return {
        "connected": store.get_oauth_token(user_id, PROVIDER) is not None,
        "configured": _oauth_client_configured(server),
    }


@router.get("/start")
def start_authorization(
    request: Request,
    response: Response,
    server: ServerConfig = Depends(get_server_config),
    store: SQLiteStore = Depends(get_store),
) -> dict:
    """Google onay URL'ini uretir. Tarayici bu adrese YONLENDIRILIR.

    OTURUM GEREKTIRMIYOR -- gerektirseydi giris yapmak icin once giris yapmis
    olmak gerekirdi. Kullanicinin API anahtarlarina da bakmiyor: OAuth istemcisi
    kuruluma ait ve akisin bu adimi kimlikten bagimsiz.
    """
    # State'i URL uretildikten SONRA kaydediyoruz: `code_verifier` ancak o zaman
    # olusuyor ve callback'te ayni degerin geri verilmesi gerekiyor.
    placeholder = secrets.token_urlsafe(24)
    try:
        url, code_verifier = build_authorization_url(
            server, _redirect_uri(request, server), placeholder, scopes=LOGIN_SCOPES
        )
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, redact_secrets(str(exc))) from exc

    # Kullanici kimligi burada BILINMIYOR ve bilinmesi de gerekmiyor; callback
    # onu ID token'dan cikaracak. Suresi dolmuslarin temizligi ve kapasite
    # tavani depoda uygulaniyor.
    store.put_oauth_state(
        placeholder,
        None,
        code_verifier,
        ttl_sec=STATE_TTL_SECONDS,
        max_pending=MAX_PENDING_STATES,
    )
    # Callback'te bu tarayicinin akisi baslattigi bununla kanitlaniyor; bkz.
    # `STATE_COOKIE`. Omru state'in TTL'i ile ayni.
    response.set_cookie(
        STATE_COOKIE,
        placeholder,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=_request_is_https(request),
        path=STATE_COOKIE_PATH,
    )
    return {"authorization_url": url, "state": placeholder}


@router.get("/callback", name="youtube_callback")
def youtube_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    store: SQLiteStore = Depends(get_store),
    server: ServerConfig = Depends(get_server_config),
) -> RedirectResponse:
    """Google'in geri dondugu uc. Kodu jetona cevirir, kimligi kurar.

    Bu uc AYNI ANDA iki is yapiyor: YouTube yayin izni aliyor ve kullaniciyi
    tanitiyor. Ayirmak ikinci bir onay ekrani demekti; kullanici zaten yayin
    icin Google hesabi bagliyor.
    """
    if error:
        # KODLANIYOR: `error` Google'in verdigi ham sorgu parametresi ve
        # kodlanmadan yazildiginda icindeki `&` ya da `#` yonlendirme URL'ini
        # bolup arayuze uydurma parametre gecirebiliyordu.
        denied = RedirectResponse(f"/?youtube_auth=error&reason={quote(error, safe='')}")
        denied.delete_cookie(STATE_COOKIE, path=STATE_COOKIE_PATH)
        return denied
    if not code or not state:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Eksik `code` veya `state`")

    # Akisi BU tarayici mi baslatti? (login CSRF; bkz. `STATE_COOKIE`)
    #
    # State TUKETILMEDEN ve kod Google'a GONDERILMEDEN once: eslesmeyen bir
    # istek hicbir yan etki birakmamali. Bayt olarak karsilastiriliyor:
    # `compare_digest` ASCII disi `str` alinca hata firlatiyor ve `state`
    # disaridan gelen bir sorgu parametresi.
    bound = request.cookies.get(STATE_COOKIE)
    if not bound or not secrets.compare_digest(bound.encode(), state.encode()):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Giriş bu tarayıcıda başlatılmamış ya da süresi dolmuş; girişi yeniden başlatın.",
        )

    # `state` dogrulamasi CSRF korumasi: bu akisi biz baslatmis olmaliyiz.
    # TEK KULLANIMLIK tuketim ve TTL kontrolu depoda, tek islem icinde.
    pending = store.consume_oauth_state(state)
    if pending is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Geçersiz veya süresi dolmuş `state`")
    _unused, code_verifier = pending

    try:
        exchanged = exchange_code_for_token(
            server, _redirect_uri(request, server), code, code_verifier=code_verifier, scopes=LOGIN_SCOPES
        )
    except Exception as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, redact_secrets(f"Jeton alınamadı: {exc}")
        ) from exc

    response = RedirectResponse("/?youtube_auth=ok")
    # Akis bitti; baglama cerezinin artik bir isi yok.
    response.delete_cookie(STATE_COOKIE, path=STATE_COOKIE_PATH)

    if server.auth_mode == "single_user":
        store.save_oauth_token(DEFAULT_USER_ID, PROVIDER, exchanged.token_json)
        return response

    # Kalici kimlik `sub`: e-posta degisebilir, `sub` degismez.
    if not exchanged.google_sub:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Google kimliği alınamadı; `openid` izni verilmemiş olabilir",
        )
    user_id = f"google:{exchanged.google_sub}"
    store.save_oauth_token(user_id, PROVIDER, exchanged.token_json)

    session_token = secrets.token_urlsafe(32)
    store.create_session(session_token, user_id, exchanged.email, server.session_ttl_sec)
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=server.session_ttl_sec,
        httponly=True,  # JavaScript okuyamasin: XSS ile jeton calinmasini engeller
        samesite="lax",  # CSRF'e karsi; `lax` OAuth geri donusundeki yonlendirmeyi bozmuyor
        secure=_request_is_https(request),
        path="/",
    )
    return response


session_router = APIRouter(prefix="/api/auth", tags=["auth"])


@session_router.get("/me")
def whoami(
    request: Request,
    server: ServerConfig = Depends(get_server_config),
    store: SQLiteStore = Depends(get_store),
) -> dict:
    """Kim giris yapmis. Oturum YOKSA hata degil, `signed_in: false` doner.

    401 donmuyor cunku bu ucun isi tam da "oturum var mi" sorusunu yanitlamak;
    arayuz her acilista bunu cagirip giris ekrani gosterip gostermeyecegine
    karar veriyor.
    """
    if server.auth_mode == "single_user":
        return {"signed_in": True, "user_id": DEFAULT_USER_ID, "email": None, "auth_required": False}

    token = request.cookies.get(SESSION_COOKIE)
    session = store.get_session(token) if token else None
    if not session:
        return {"signed_in": False, "user_id": None, "email": None, "auth_required": True}
    return {
        "signed_in": True,
        "user_id": session["user_id"],
        "email": session["email"],
        "auth_required": True,
    }


@session_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def logout(
    request: Request,
    store: SQLiteStore = Depends(get_store),
) -> Response:
    """Oturumu SUNUCUDAN siler; cerezi silmek tek basina yeterli olmazdi.

    Cerez silinip kayit dursaydi, o jetonun bir kopyasini ele geciren biri
    oturumu kullanmaya devam ederdi. Sunucu tarafli oturumlarin varlik sebebi
    de bu: iptal edilebilir olmalari.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        store.delete_session(token)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


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
