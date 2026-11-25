"""Acik bir ilerleme akisi thread TUTMAMALI (bkz. `api/sse.py`)."""

from __future__ import annotations

from types import SimpleNamespace

import anyio

from api.sse import run_event_stream


class _QuietJob:
    """Calisan ama henuz olay uretmeyen bir is."""

    def get(self, run_id):
        return SimpleNamespace(user_id="ali")

    def events_since(self, run_id, cursor):
        return []

    def is_stream_closed(self, run_id):
        return False


def test_an_open_stream_does_not_pin_a_thread():
    """Akis senkronken `wait_for_events` icinde 15 sn bloklaniyor ve butun
    `def` rotalarinin paylastigi havuzdan bir thread tutuyordu: 40 acik akis
    API'nin geri kalanini durduruyordu. Havuz TEK thread'e indiriliyor; akis
    acikken baska bir is o thread'i alabilmeli."""

    async def scenario():
        anyio.to_thread.current_default_thread_limiter().total_tokens = 1

        async def listen():
            async for _ in run_event_stream(_QuietJob(), "r1", user_id="ali"):
                pass

        async with anyio.create_task_group() as group:
            group.start_soon(listen)
            await anyio.sleep(0.2)  # akis artik yeni olay bekliyor
            with anyio.fail_after(2):
                assert await anyio.to_thread.run_sync(lambda: "serbest") == "serbest"
            group.cancel_scope.cancel()

    anyio.run(scenario)
