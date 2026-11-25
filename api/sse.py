"""Server-Sent Events yardimcilari.

WebSocket yerine SSE: akis TEK YONLU (sunucu -> istemci) ve mevcut
`progress_callback(ProgressEvent)` desenine birebir oturuyor. SSE ayrica
proxy'lerden daha sorunsuz gecer ve tarayici tarafinda otomatik yeniden baglanir.

Her olay bir `id:` tasir (olay gunlugundeki indeks). Baglanti koptugunda tarayici
`Last-Event-ID` basligiyla geri doner ve kaldigi yerden devam eder — kacirilan
ilerleme kaybolmaz.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import anyio
from starlette.concurrency import run_in_threadpool

from api.schemas import RunSnapshotBody
from src.jobs import JobRunner

# Proxy'ler ve yuk dengeleyiciler sessiz baglantiyi kapatir. Bu araliklarla
# yorum satiri gondererek baglantiyi canli tutuyoruz.
HEARTBEAT_SECONDS = 15.0

# Yeni olaya bakma araligi. Bekleme thread TUTMUYOR (bkz. `run_event_stream`);
# bu deger yalnizca ilerleme cubugunun en fazla ne kadar gecikecegi.
POLL_SECONDS = 0.5

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    # nginx'in yanit tamponlamasini kapat; aksi halde olaylar toplu iletilir.
    "X-Accel-Buffering": "no",
}


def format_event(event: str, data: dict, event_id: int | None = None) -> str:
    """Tek bir SSE mesaji. `data` her zaman JSON."""
    payload = json.dumps(data, ensure_ascii=False)
    prefix = f"id: {event_id}\n" if event_id is not None else ""
    return f"{prefix}event: {event}\ndata: {payload}\n\n"


def heartbeat() -> str:
    """Yorum satiri: istemci yok sayar, ara katmanlar baglantiyi acik tutar."""
    return ": keep-alive\n\n"


def parse_last_event_id(raw: str | None) -> int:
    """`Last-Event-ID` basligini imlece cevirir; gecersizse bastan basla."""
    if not raw:
        return 0
    try:
        return max(0, int(raw) + 1)
    except (TypeError, ValueError):
        return 0


async def run_event_stream(
    runner: JobRunner, run_id: str, cursor: int = 0, user_id: str | None = None
) -> AsyncIterator[str]:
    """Bir isin ilerlemesini SSE olarak akitir.

    ASENKRON uretici, bilerek. Eskiden senkrondu ve `wait_for_events` icinde
    15 sn'ye kadar bloklaniyordu; Starlette senkron ureticiyi, butun `def`
    rotalarina hizmet eden 40 thread'lik havuzda dondurur. Yani acik her akis
    bir thread TUTUYORDU: canli islerde 40 akis (tek bir betik ya da HTTP/2
    uzerinde bir avuc sekme) API'nin geri kalanini is bitene kadar
    kuyruga sokuyordu. Simdi runner'a yalnizca kisa, bloklamayan cagrilar
    gidiyor (`events_since`, `is_stream_closed`) ve aradaki bekleme thread
    tutmayan `anyio.sleep`. Istemci koptugunda akis da o bekleme noktasinda
    hemen iptal ediliyor; eskiden thread 15 sn'lik beklemeyi bitirmek
    zorundaydi.

    `user_id` verildiginde BASKASININ isi "bilinmeyen" gibi davraniliyor: ayri
    bir "yetkisiz" yaniti, var olmayan bir kimlikle var olan bir kimligi ayirt
    edilebilir kilar ve calistirma kimliklerini sizdirir.

    Burada 404 DONULMUYOR, akis icinde `error` olayi gonderilip kapatiliyor:
    `EventSource` HTTP hata kodunu govdesiz bir hata sayip SONSUZA KADAR yeniden
    baglanmaya calisir. Olay olarak gonderildiginde istemci durumu okuyup
    duruyor.
    """
    # Runner cagrilari yine thread'de: Redis'te ag gidis-donusu var ve olay
    # dongusunu bloklamamali. Ama her biri milisaniyelik; thread'i hemen
    # birakiyorlar.
    handle = await run_in_threadpool(runner.get, run_id)
    if handle is None or (user_id is not None and handle.user_id != user_id):
        yield format_event("error", {"run_id": run_id, "detail": "Bilinmeyen çalıştırma"})
        return

    quiet = 0.0
    while True:
        # Once birikmis olaylari bosalt: yeniden baglanan istemci
        # `Last-Event-ID`den itibaren kacirdiklarini burada alir.
        events = await run_in_threadpool(runner.events_since, run_id, cursor)
        for index, event in events:
            cursor = index + 1
            yield format_event("progress", event.model_dump(), event_id=index)
        if events:
            quiet = 0.0

        if await run_in_threadpool(runner.is_stream_closed, run_id):
            break

        await anyio.sleep(POLL_SECONDS)
        quiet += POLL_SECONDS
        # Yeni olay yoksa baglantiyi canli tut.
        if quiet >= HEARTBEAT_SECONDS:
            yield heartbeat()
            quiet = 0.0

    final = await run_in_threadpool(runner.get, run_id)
    if final is not None:
        # Sozlugu OLDUGU GIBI yollamiyoruz: `snapshot()` is katmaninin ic
        # adlarini tasiyor (`job_id`) ve istemcinin isine yaramayan `user_id`yi
        # iceriyor. Model, tel uzerindeki sozlesmeyi API'nin geri kalaniyla
        # ayni adlandirmaya (`run_id`) sabitliyor.
        snapshot = final.snapshot()
        yield format_event(
            "done",
            RunSnapshotBody(
                run_id=snapshot["job_id"],
                state=snapshot["state"],
                created_at=snapshot["created_at"],
                progress=snapshot["progress"],
                stage=snapshot["stage"],
                message=snapshot["message"],
                error=snapshot["error"],
            ).model_dump(),
        )
