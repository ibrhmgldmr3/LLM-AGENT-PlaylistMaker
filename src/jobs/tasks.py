"""Is turlerinin kayit defteri.

`JobRunner.submit` uzun sure bir CLOSURE aliyordu. Surec-ici calisirken bu
sorunsuzdu, ama bir closure surec sinirini gecemez: paylasimli bir kuyruga
yazilacak sey KOD degil VERI olmali. Bu modul o ayrimi kuruyor -- cagiran
"su isi su verilerle yap" diyor, isi yapan tarafi hic tanimadan.

Kayit defteri MODUL DUZEYINDE: dagitik kurulumda web ve worker AYNI kod
tabanini calistiriyor, dolayisiyla ikisi de ayni adlari cozebiliyor. Web
tarafi adi dogrulamak icin (bilinmeyen is hemen reddedilsin), worker tarafi
gercekten calistirmak icin kullaniyor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from src.models import ProgressEvent


@dataclass(frozen=True)
class TaskContext:
    """Isin kendisi hakkinda bilmesi gerekenler.

    `payload` JSON'a cevrilebilir OLMALI: paylasimli kuyruga giden sey bu.
    Yapilandirma (API anahtarlari dahil) BILEREK burada DEGIL -- isi yapan
    taraf onu kendi ortamindan kuruyor. Anahtarlari kuyruga yazmak, onlari
    broker'a ve oradan da broker'in disk/yedeklerine yaymak olurdu.
    """

    job_id: str
    user_id: str
    payload: dict[str, Any]


# Bir is: baglami ve ilerleme bildiricisini alir, sonucu doner.
TaskFn = Callable[[TaskContext, Callable[[ProgressEvent], None]], Any]

_TASKS: dict[str, TaskFn] = {}


class UnknownTask(KeyError):
    """Kayitli olmayan bir is adi. Cagiranin hatasi, calisma zamani arizasi degil."""


def register_task(name: str, fn: TaskFn) -> None:
    """Bir is turunu kaydeder.

    Ayni ad iki kez kaydedilirse HATA veriyor: sessizce ezmek, iki farkli
    modulun ayni adi kullandigi bir durumda hangisinin calistigini yalnizca
    import sirasina bagli hale getirirdi.
    """
    existing = _TASKS.get(name)
    if existing is not None and existing is not fn:
        raise ValueError(f"`{name}` isi zaten kayitli")
    _TASKS[name] = fn


def resolve_task(name: str) -> TaskFn:
    try:
        return _TASKS[name]
    except KeyError as exc:
        raise UnknownTask(name) from exc


def unregister_task(name: str) -> None:
    """Tek bir kaydi siler. Yoksa sessizce gecer.

    Testler icin: ad-hoc bir is kaydeden test, defteri KENDI ardindan
    toplayabilmeli. `clear_tasks` bunun icin fazla genis -- gercek kayitlari
    (`build_playlist`) da silerdi ve testler arasi sira bagimliligi yaratirdi.
    """
    _TASKS.pop(name, None)


def registered_tasks() -> tuple[str, ...]:
    return tuple(sorted(_TASKS))


def clear_tasks() -> None:
    """Defteri bosaltir. Testler icin; uretimde kayitlar import aninda olusur."""
    _TASKS.clear()
