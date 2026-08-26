"""Testlerin ortak yardimcilari."""

from __future__ import annotations

import uuid

import pytest

from src.jobs import register_task, unregister_task


@pytest.fixture
def task():
    """Ad-hoc bir isi kaydeder ve `submit` icin ADINI doner.

    `JobRunner.submit` artik closure degil is ADI aliyor: paylasimli bir
    kuyruga yazilacak sey kod degil veri olmali (bkz. `src/jobs/tasks.py`).
    Runner MEKANIGINI (iptal, olay gunlugu, es zamanlilik) sinayan testlerin
    yine de keyfi fonksiyonlara ihtiyaci var; bu fixture o ihtiyaci sozlesmeyi
    delmeden karsiliyor.

    Kayitlar test bitince TEK TEK siliniyor: `clear_tasks` gercek kayitlari da
    (`build_playlist`) silip testler arasi sira bagimliligi yaratirdi.

    Test fonksiyonlari `(emit)` imzasini kullanmaya devam ediyor; baglami
    ilgilendirmeyen testlere `TaskContext` dayatmanin bir faydasi yok.
    """
    registered: list[str] = []

    def register(fn):
        name = f"_test_task_{uuid.uuid4().hex[:12]}"
        register_task(name, lambda context, emit: fn(emit))
        registered.append(name)
        return name

    yield register

    for name in registered:
        unregister_task(name)
