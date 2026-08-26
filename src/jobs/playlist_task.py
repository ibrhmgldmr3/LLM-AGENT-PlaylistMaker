"""Playlist uretme isi: kuyruktan gelen VERIYI calisan koda cevirir.

`api/` ALTINDA DEGIL, bilerek: dagitik kurulumda bu kodu calistiran taraf
worker ve worker'in HTTP katmanini import etmesi gerekmiyor. Bugun ayni
surecte calisiyor, yarin ayri; sinir bugunden dogru yerde duruyor.

Yapilandirma PAYLOAD'DAN GELMIYOR, ortamdan kuruluyor. Iki sebep:

1. `AppConfig` API anahtarlarini tasiyor; onlari kuyruga yazmak sirlari
   broker'a (ve oradan diskine, yedeklerine) yaymak olurdu.
2. Kullaniciya ozgu bir sir zaten YOK -- `get_user_credentials` bos bir
   `UserCredentials()` donduruyor ve butun anahtarlar `.env`'den geliyor.
   Yani ortamdan yeniden kurmak, bugunku davranisin BIREBIR aynisi.

Payload yalnizca gercekten iste ozgu olani tasiyor: konu, filtreler ve
istegin sunucu varsayilanlarini ezen secenekleri.
"""

from __future__ import annotations

from typing import Any, Callable

from src.config import AppConfig
from src.jobs.runtime import runtime_config, runtime_store
from src.jobs.tasks import TaskContext, register_task
from src.models import PlaylistRequest, ProgressEvent
from src.services.playlist_service import build_playlist

BUILD_PLAYLIST = "build_playlist"


def build_payload(
    topic: str, filters: dict[str, Any], create_youtube_playlist: bool, options: dict[str, Any]
) -> dict[str, Any]:
    """Kuyruga gidecek govdeyi kurar. Cagiran taraf anahtar adlarini bilmesin."""
    return {
        "topic": topic,
        "filters": filters,
        "create_youtube_playlist": create_youtube_playlist,
        "options": options,
    }


def run_build_playlist(
    context: TaskContext, emit: Callable[[ProgressEvent], None]
) -> Any:
    """Playlist uretir; hata halinde kota rezervasyonunu BIRAKIR.

    Rezervasyonu birakma isi API rotasindan BURAYA tasindi ve tasinmasi
    zorunluydu: isi calistiran taraf artik ayri bir surec olabilir ve rotada
    kalsaydi web tarafi isi kuyruga atip donerdi -- worker patladiginda
    rezervasyon hic serbest kalmazdi. Ustelik surec yeniden baslatmasi bile
    cozmezdi, cunku `mark_interrupted_runs` yalnizca WEB acilisinda calisiyor.

    `BaseException`: iptal (`JobCancelled`) de bu yoldan geciyor ve iptal
    edilen bir calistirma da kotayi tutmamali.
    """
    config = runtime_config(context.payload.get("options"))
    request = PlaylistRequest(
        topic=context.payload["topic"],
        filters=context.payload.get("filters") or {},
        create_youtube_playlist=bool(context.payload.get("create_youtube_playlist")),
    )
    try:
        return build_playlist(
            config,
            request,
            progress_callback=emit,
            run_id=context.job_id,
            user_id=context.user_id,
        )
    except BaseException:
        _release_reservation(config, context.job_id)
        raise


def _release_reservation(config: AppConfig, run_id: str) -> None:
    """Temizlik, isin GERCEK hatasini golgelememeli."""
    import logging

    try:
        runtime_store(config).release_run_reservation(run_id)
    except Exception:
        logging.getLogger(__name__).warning(
            "Kota rezervasyonu bırakılamadı: %s", run_id, exc_info=True
        )


register_task(BUILD_PLAYLIST, run_build_playlist)
