"""Vektoru eksik kalan parcalari tamamlar.

NEDEN GEREKLI: `rag_service._embed_space` iceri alma isini gomme arizasi
yuzunden DUSURMUYOR -- parcalar yazilmis ve leksik arama onlarla calisiyor.
Ama eksik vektorlerin "bir sonraki iceri almada tamamlanacagi" varsayimi,
kullanici o alana yeni bir kaynak EKLEMEDIGI surece hic gerceklesmiyor.

Canli veritabaninda olculdu: 130 parcalik bir alanda yalnizca 64'u -- tam
olarak BIR grup (`EMBEDDING_BATCH_SIZE=64`) -- gomulmustu. Ilk grup yazilmis,
ikincisi patlamis, istisna yutulmus ve alan aylarca yari gomulu kalmisti. Alanin
6 kaynagindan 3'unun HIC vektoru yoktu; o kaynaklar yalnizca kelime eslesmesiyle
bulunabiliyordu ve "bu konunun ana fikri ne" gibi ortak kelimesi olmayan sorular
(leksik kapsam 0.00) hicbir zaman yanit alamiyordu.

Kullanim:

    python scripts/backfill_embeddings.py              # eksikleri listeler
    python scripts/backfill_embeddings.py --apply      # uretir

Once LISTELIYOR: gomme cagrisi para harciyor ve kac parca uretilecegini gormeden
baslatmak dogru degil. `--apply` verilmedikce hicbir sey yazilmiyor.

Ariza tekrarlarsa (hiz siniri, gecici saglayici sorunu) betik guvenle YENIDEN
kosturulabilir: `chunks_missing_embeddings` zaten yalnizca eksikleri getiriyor,
yazilanlar tekrar uretilmiyor.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from src.providers import create_rag_llm_provider  # noqa: E402
from src.services import embedding_service  # noqa: E402
from src.storage import SQLiteStore  # noqa: E402


def _spaces_with_gaps(store: SQLiteStore, model: str) -> list[tuple[str, str, int, int]]:
    """`(space_id, ad, toplam_parca, eksik)` -- yalnizca eksigi olan alanlar."""
    rows: list[tuple[str, str, int, int]] = []
    with store.connect() as conn:
        spaces = conn.execute("SELECT space_id, name FROM space ORDER BY name").fetchall()
    for space in spaces:
        space_id = space["space_id"]
        total = store.count_chunks(space_id)
        missing = len(store.chunks_missing_embeddings(space_id, model))
        if missing:
            rows.append((space_id, space["name"], total, missing))
    return rows


def main() -> int:
    apply = "--apply" in sys.argv

    config = load_config()
    store = SQLiteStore(config.sqlite_path)
    model = config.embedding_model()

    gaps = _spaces_with_gaps(store, model)
    print(f"Model: {model}")
    if not gaps:
        print("Eksik vektor yok.")
        return 0

    for space_id, name, total, missing in gaps:
        print(f"  {missing:>4}/{total:<4} eksik  {name}  ({space_id})")

    if not apply:
        print("\nUretmek icin: python scripts/backfill_embeddings.py --apply")
        return 0

    llm = create_rag_llm_provider(config)
    failed = False
    for space_id, name, _total, missing in gaps:
        print(f"\n{name}: {missing} parça gömülüyor...")
        try:
            written = embedding_service.embed_missing(
                config,
                store,
                llm,
                space_id,
                progress=lambda done, total: print(f"  {done}/{total}", end="\r"),
            )
        except Exception as exc:
            # Sonraki alana GECILIYOR: bir alanin arizasi digerlerini
            # engellememeli ve yazilan gruplar zaten korunuyor.
            failed = True
            print(f"  HATA: {exc}")
            continue
        remaining = len(store.chunks_missing_embeddings(space_id, model))
        print(f"  {written} üretildi, {remaining} eksik kaldı")
        failed = failed or bool(remaining)

    if failed:
        print("\nEksik kalanlar var; betik yeniden koşulabilir (yazılanlar korunur).")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
