"""Sunucu sahibi icin kullanim raporu.

Neden var: anahtarlar PAYLASIMLI oldugu icin YouTube kotasini ve LLM faturasini
sunucu sahibi odiyor, ama bugune kadar "bugun ne kadar harcandi, kim harcadi"
sorusunun hicbir cevabi yoktu. `MAX_RUNS_PER_USER_PER_DAY` bir sinir koyuyor
ama sinirin DOGRU yerde olup olmadigini anlamak icin olcum gerekiyor.

Rapor tahmin URETMIYOR: sayilar gercek API cagrilarindan geliyor (bkz.
`SQLiteStore.record_api_usage`). Arama sonuclari onbelleklendigi icin
"calistirma x 1.200" tahmini gercek tuketimin cok uzerinde cikardi.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import get_server_config, get_store, require_admin
from src.config import ServerConfig
from src.providers.youtube_data_api_provider import DEFAULT_DAILY_QUOTA_UNITS
from src.storage import SQLiteStore
from src.storage.sqlite_store import quota_day

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/usage")
def usage(
    _admin: str = Depends(require_admin),
    server: ServerConfig = Depends(get_server_config),
    store: SQLiteStore = Depends(get_store),
) -> dict:
    """Bugunku kota tuketimi, calistirma dagilimi ve devre disi saglayicilar."""
    day = quota_day()
    rows = store.get_api_usage(day)

    spent = sum(row["units"] for row in rows)
    limit = server.youtube_daily_quota_units or DEFAULT_DAILY_QUOTA_UNITS
    # Servisin kendine koydugu tavan; projenin kotasindan DUSUK olabilir.
    budget = server.daily_unit_budget()

    by_endpoint: dict[str, dict[str, int]] = {}
    by_user: dict[str, int] = {}
    for row in rows:
        bucket = by_endpoint.setdefault(row["endpoint"], {"calls": 0, "units": 0})
        bucket["calls"] += row["calls"]
        bucket["units"] += row["units"]
        by_user[row["user_id"]] = by_user.get(row["user_id"], 0) + row["units"]

    return {
        # Gun siniri Pasifik saatine gore: Google kotayi orada sifirliyor.
        "quota_day": day,
        "quota": {
            "spent_units": spent,
            "limit_units": limit,
            "remaining_units": max(0, limit - spent),
            # Kalan kotayla kac arama daha yapilabilecegi, `search.list`in 100
            # birimi uzerinden. Kullaniciya "kac calistirma kaldi" demiyoruz:
            # bir calistirmanin kac arama yapacagi alt konu sayisina ve
            # onbellek isabetine bagli, yani ONCEDEN bilinmiyor.
            "remaining_searches": max(0, limit - spent) // 100,
            # Kabul kontrolunun kullandigi tavan (bkz. MAX_UNITS_PER_DAY).
            "budget_units": budget,
            "budget_remaining_units": max(0, budget - spent),
        },
        "by_endpoint": by_endpoint,
        "by_user_units": by_user,
        "runs_last_24h": store.count_runs_by_user_since(),
        "max_runs_per_user_per_day": server.max_runs_per_user_per_day,
        # ANLIK durum: su an hangi saglayici dinleniyor.
        "active_cooldowns": store.list_active_cooldowns(),
        # GUNUN TOPLAMI: kac kez hiz sinirina takildik, kac kez sogumaya girdik.
        # Sogumalar sure dolunca iz birakmadan kayboldugu icin anlik durum bu
        # soruyu yanitlayamiyor. Es zamanlilik ayarlari (kac calistirma x kac
        # isci) bu sayilara bakilarak degistirilmeli -- sinir sunucunun IP'sine
        # bagli ve tahminle kurcalanacak bir sey degil.
        "provider_events": store.get_provider_events(day),
    }
