"""Govde siniri, oturum kontrolunden ve govde ayristirmadan ONCE uygulanmali.

FastAPI multipart govdeyi bagimliliklardan once diske yaziyor; sinir yoksa
oturumsuz biri diski doldurabiliyordu (bkz. `api/body_limit.py`).
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.testclient import TestClient

from api.body_limit import MULTIPART_OVERHEAD, BodySizeLimitMiddleware

UPLOAD_LIMIT = 1024


def _app(calls: list[str]) -> FastAPI:
    app = FastAPI()
    app.add_middleware(BodySizeLimitMiddleware, upload_limit=UPLOAD_LIMIT, default_limit=64)

    def require_login() -> None:
        calls.append("auth")
        raise HTTPException(401, "Oturum açmanız gerekiyor")

    @app.post("/api/spaces/{space_id}/sources/document")
    async def upload(space_id: str, file: UploadFile = File(...), _=Depends(require_login)):
        return {"ok": True}

    @app.post("/api/echo")
    async def echo(payload: dict) -> dict:
        return payload

    return app


def test_oversized_upload_is_rejected_before_auth_runs():
    calls: list[str] = []
    client = TestClient(_app(calls))

    too_big = b"x" * (UPLOAD_LIMIT + MULTIPART_OVERHEAD + 1)
    response = client.post(
        "/api/spaces/abc/sources/document", files={"file": ("a.txt", too_big, "text/plain")}
    )

    assert response.status_code == 413
    assert calls == []


def test_chunked_body_without_content_length_is_cut_off():
    """`Content-Length` istemcinin beyani; yoksa baytlar sayilmali."""
    calls: list[str] = []
    client = TestClient(_app(calls))

    def chunks():
        for _ in range(10):
            yield b"{" + b" " * 30

    response = client.post(
        "/api/echo", content=chunks(), headers={"content-type": "application/json"}
    )

    assert response.status_code == 413


def test_small_bodies_pass_through():
    calls: list[str] = []
    client = TestClient(_app(calls))

    assert client.post("/api/echo", json={"a": 1}).json() == {"a": 1}
    response = client.post(
        "/api/spaces/abc/sources/document", files={"file": ("a.txt", b"hi", "text/plain")}
    )
    assert response.status_code == 401
    assert calls == ["auth"]


def test_real_app_installs_the_limit():
    from api.main import app

    assert any(m.cls is BodySizeLimitMiddleware for m in app.user_middleware)
