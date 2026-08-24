"""Ogrenme alaninin DISK tarafi: yuklenen dosyalarin yeri ve silinmesi.

Ayri bir modul olmasinin gerekcesi `run_retention.py` ust aciklamasindakiyle
ayni: bu is iki katmani birden gerektiriyor. `SQLiteStore` disk hakkinda
hicbir sey bilmiyor (ve bilmemeli), `AppConfig` veritabanini tanimiyor.

Bir alan IKI yerde yasiyor:
  - veritabani: `space`, `space_source`, `chunk`, `chunk_fts`, `chunk_embedding`
  - disk: `data/uploads/<kullanici ozeti>/<space_id>/` -- yuklenen dosyalar

Silme ikisini birden kaldirmali. `run_retention` uzun sure yalnizca birincisini
kaldiriyordu ve "sildim" diyen bir dugmenin silmemesi, herkese acik bir
serviste kabul edilebilir bir sey degil.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path

from src.config import AppConfig
from src.storage import SQLiteStore

_log = logging.getLogger(__name__)


def user_bucket(user_id: str) -> str:
    """Kullanici kimligini DIZIN ADINA cevirir.

    Kimligi dogrudan yol olarak kullanmak IKI sebeple yanlis:

    1. `multi_user` modda kimlik `google:1234567` bicimde ve `:` Windows'ta
       gecersiz bir dosya adi karakteri -- yol olusturma sessizce patlardi.
    2. Kimlik disaridan gelen bir dize; ozetlemek yol gecisi (`..`) olasiligini
       tamamen ortadan kaldiriyor, kacis kurallariyla ugrasmadan.

    Ozet ayrica diskte e-posta/kimlik gormeyi de engelliyor.
    """
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]


def space_upload_dir(config: AppConfig, user_id: str, space_id: str) -> Path:
    """Bu alanin yuklenen dosyalarinin bulundugu dizin.

    `space_id` her zaman `uuid4().hex` (yalnizca alfanumerik). Yine de burada
    DOGRULANIYOR: fonksiyon bu garantiyi kendisi tasimali -- ayni gerekce
    `run_retention._remove_run_dir` icinde de yazili.
    """
    if not space_id or not space_id.isalnum():
        raise ValueError(f"Gecersiz alan kimligi: {space_id!r}")
    return Path(config.data_dir) / "uploads" / user_bucket(user_id) / space_id


def delete_space(config: AppConfig, store: SQLiteStore, space_id: str, user_id: str) -> bool:
    """Alani veritabanindan VE diskten siler.

    Sira onemli: once veritabani. Satir silinemezse (yok ya da baskasinin)
    diske hic dokunulmuyor -- yetki kontrolu veritabani sorgusunun icinde.
    """
    if not store.delete_space(space_id, user_id):
        return False
    _rmtree(space_upload_dir(config, user_id, space_id))
    return True


def delete_source_file(config: AppConfig, user_id: str, space_id: str, ref_id: str) -> None:
    """Tek bir yuklenmis dosyayi siler.

    `ref_id` saklanan dosya adi ve SUNUCU tarafindan uretiliyor
    (`uuid4().hex + uzanti`). Yine de yalnizca ADIN kendisi kullaniliyor
    (`Path(...).name`): kayitta bir sekilde yol ayraci bulunsaydi bile dizinin
    disina cikamaz.
    """
    if not ref_id:
        return
    target = space_upload_dir(config, user_id, space_id) / Path(ref_id).name
    try:
        if target.is_file():
            target.unlink()
    except OSError:
        _log.warning("Yuklenen dosya silinemedi: %s", target, exc_info=True)


def _rmtree(path: Path) -> None:
    """Dizini siler. Silinemezse ISI DUSURMEZ, uyarir.

    Windows'ta acik bir dosya tutamagi silmeyi engelleyebiliyor. Kullaniciya
    hata dondurmek yanlis olurdu: veritabani kaydi zaten silindi ve alan
    listeden kalkti. Artik dosya bir sonraki bakim adiminda toplanir.
    """
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
    except OSError:
        _log.warning("Alan dizini silinemedi: %s", path, exc_info=True)
