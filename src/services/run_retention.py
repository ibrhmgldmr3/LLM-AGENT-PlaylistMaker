"""Calistirma verisinin YASAM DONGUSU: silme ve temizlik.

Ayri bir modul olmasinin sebebi: bu is iki katmani birden gerektiriyor.
`SQLiteStore` disk hakkinda hicbir sey bilmiyor (ve bilmemeli), `AppConfig`
ise veritabanini tanimiyor. Ikisini birlestiren yer burasi.

Bir calistirma IKI yerde yasiyor:
  - veritabani: `run`, `run_subtopic`, `run_video` satirlari
  - disk: `data/runs/<run_id>/` -- calistirma logu, `result.json`, `study_plan.md`

Eskiden silme yalnizca BIRINCISINI kaldiriyordu. Kullanici gecmisten "Sil"e
basiyor, kayit listeden kayboluyor, ama urettigi calisma plani ve tum sonuc
JSON'u diskte duruyordu. Tek kullanicili kurulumda yalnizca disk sisirmesiydi;
herkese acik bir serviste "sildim" diyen bir dugmenin silmemesi baska bir sey.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from src.config import AppConfig
from src.storage import DEFAULT_USER_ID, SQLiteStore

_log = logging.getLogger(__name__)


def delete_run(
    config: AppConfig, store: SQLiteStore, run_id: str, user_id: str | None = DEFAULT_USER_ID
) -> bool:
    """Calistirmayi veritabanindan VE diskten siler.

    Sira onemli: once veritabani. Satir silinemezse (yok ya da baskasinin)
    diske hic dokunulmuyor -- yetki kontrolu veritabani sorgusunun icinde.
    """
    if not store.delete_run(run_id, user_id=user_id):
        return False
    _remove_run_dir(config, run_id)
    return True


def purge_orphan_run_dirs(config: AppConfig, store: SQLiteStore) -> int:
    """Veritabaninda karsiligi olmayan calistirma dizinlerini siler.

    Gecmiste birikmis olanlari toplar: silme uzun sure yalnizca veritabanina
    dokundugu icin diskte karsiliksiz dizinler kaldi. Ayrica hicbir zaman
    tamamlanmayan calistirmalarin artiklarini da temizler.
    """
    runs_dir = config.runs_dir
    if not runs_dir.exists():
        return 0

    bilinen = store.list_run_ids()
    silinen = 0
    for child in runs_dir.iterdir():
        if not child.is_dir() or child.name in bilinen:
            continue
        if _rmtree(child):
            silinen += 1
    return silinen


def _remove_run_dir(config: AppConfig, run_id: str) -> None:
    # `run_id` disaridan gelen bir dize; dogrudan yol olarak kullanmadan once
    # ADININ bir dizin adi oldugundan emin ol. Bugun her zaman `uuid4().hex`
    # ama bu fonksiyon o garantiyi kendisi tasimali -- yol birlestirmede
    # ".." ya da ayirac iceren bir deger runs_dir'in disina cikabilirdi.
    if not run_id or not run_id.isalnum():
        _log.warning("Beklenmeyen calistirma kimligi, dizin silinmedi: %r", run_id)
        return
    _rmtree(config.runs_dir / run_id)


def _rmtree(path: Path) -> bool:
    """Dizini siler. Silinemezse ISI DUSURMEZ, uyarir.

    Windows'ta acik bir dosya tutamagi (ornegin hala kapatilmamis bir
    calistirma logu) silmeyi engelleyebiliyor. Bu durumda kullaniciya hata
    dondurmek yanlis olurdu: veritabani kaydi zaten silindi, kayit gecmisten
    kalkti; artan dosya bir sonraki `purge_orphan_run_dirs` turunda toplanir.
    """
    try:
        shutil.rmtree(path)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        _log.warning("Calistirma dizini silinemedi (%s): %s", path, exc)
        return False
