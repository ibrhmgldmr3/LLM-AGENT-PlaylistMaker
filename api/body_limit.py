"""Istek govdesine ust sinir -- govde AYRISTIRILMADAN once.

Neden middleware: FastAPI multipart govdeyi rota govdesi ve bagimliliklar
(oturum kontrolu dahil) calismadan ONCE tamamen okuyup gecici dosyaya
yaziyor. Yukleme ucundaki `max_upload_bytes` kontrolu ancak o dosyadan
kopyalarken devreye giriyordu; yani oturumu olmayan biri 50 GB'lik bir
"yukleme" ile diski doldurabiliyor, 401'i ancak sonra aliyordu.

Burada iki kontrol var:
- `Content-Length` sinirin ustundeyse uygulama HIC cagrilmadan 413.
- Baslik yoksa ya da yalan soyluyorsa (chunked), gelen baytlar sayiliyor ve
  sinir asildigi anda okuma 413 ile kesiliyor.
"""

from __future__ import annotations

import json

from fastapi import HTTPException

# JSON govdeleri (konu, soru, ayar) birkac KB. 1 MB genis bir pay.
DEFAULT_BODY_LIMIT = 1024 * 1024
# Multipart zarfi (sinirlar, basliklar, `title` alani) dosyanin USTUNE biniyor.
MULTIPART_OVERHEAD = 1024 * 1024
UPLOAD_PATH_PREFIX = "/api/spaces/"
UPLOAD_PATH_SUFFIX = "/sources/document"


def _is_upload(path: str) -> bool:
    return path.startswith(UPLOAD_PATH_PREFIX) and path.endswith(UPLOAD_PATH_SUFFIX)


def _too_large() -> HTTPException:
    return HTTPException(413, "İstek gövdesi çok büyük.")


class BodySizeLimitMiddleware:
    """`upload_limit` yalnizca dokuman yukleme ucuna, `default_limit` digerlerine."""

    def __init__(self, app, upload_limit: int, default_limit: int = DEFAULT_BODY_LIMIT):
        self.app = app
        self.upload_limit = upload_limit + MULTIPART_OVERHEAD
        self.default_limit = default_limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self.upload_limit if _is_upload(scope["path"]) else self.default_limit
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:
            await _send_413(send)
            return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # FastAPI govdeyi ayristirirken HTTPException'i OLDUGU GIBI
                    # yukseltiyor; baska bir istisna 400'e donerdi.
                    raise _too_large()
            return message

        started = False

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except HTTPException as exc:
            # Govdeyi rota disinda okuyan bir yol istisnayi buraya kadar
            # tasiyabilir; yanit henuz baslamadiysa 413'u biz yaziyoruz.
            if exc.status_code != 413 or started:
                raise
            await _send_413(send)


async def _send_413(send) -> None:
    body = json.dumps({"detail": _too_large().detail}, ensure_ascii=False).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
