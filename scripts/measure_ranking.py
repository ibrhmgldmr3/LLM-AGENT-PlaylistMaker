"""Kayitli kosular uzerinden siralama kalitesini olcer.

`data/runs/*/result.json` dosyalarini okuyup secilen videolarin ALT BASLIK
ALAKASINI raporlar, ardindan `POPULARITY_GATE_FULL` esigini tarar.

Neden var: siralamaya yapilan bir degisikligin iyilestirme oldugunu iddia
edebilmek icin once olcmek gerekiyor. `metadata_ranker.POPULARITY_GATE_FULL`
sabitinin degeri bu betigin ciktisiyla secildi ve koddaki yorumdaki tablo
buradan uretiliyor.

    python scripts/measure_ranking.py

Sinirlar, pesinen: kayitli kisa listeler alt baslik basina yalnizca ilk birkac
adayi tasiyor ve o liste ZATEN eski puanlamayla suzulmus. Dolayisiyla bu olcum
"mevcut kisa liste icinde daha iyi seciliyor mu" sorusunu yanitliyor, "havuzun
tamaminda en iyi video bulunuyor mu" sorusunu degil. IDF agirliklari da orijinal
havuzun tamami yerine bu birlesik listeden hesaplaniyor.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.models import FilterOptions, VideoCandidate  # noqa: E402
from src.services import metadata_ranker  # noqa: E402
from src.services.metadata_ranker import rank_candidates  # noqa: E402

# Ayirt edici tek bir kelimenin eslesmesi bile bunun uzerine cikiyor; altinda
# kalan secimler pratikte "leksik sinyal yok" demek.
NO_SIGNAL = 0.5


def load_runs():
    runs_dir = ROOT / "data" / "runs"
    if not runs_dir.exists():
        return
    for run_dir in sorted(runs_dir.iterdir()):
        path = run_dir / "result.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        pool: dict[str, VideoCandidate] = {}
        for sub in data.get("subtopics", []):
            for raw in sub.get("shortlisted_candidates", []):
                candidate = VideoCandidate(**raw)
                pool[candidate.video_id] = candidate
        if pool and data.get("recommendations"):
            yield data, list(pool.values())


def pick_with_gate(gate: float | None):
    """Verilen esikle her alt baslik icin kazanani ve alakasini dondurur."""
    original = metadata_ranker.POPULARITY_GATE_FULL
    metadata_ranker.POPULARITY_GATE_FULL = gate
    try:
        picks = []
        for data, pool in load_runs():
            filters = FilterOptions(**data["filters"])
            for sub in data.get("subtopics", []):
                ranked = rank_candidates(pool, data["topic"], sub["subtopic"]["title"], filters)
                if ranked:
                    winner, score = ranked[0]
                    picks.append((sub["subtopic"]["title"], winner.title, score.title_relevance))
        return picks
    finally:
        metadata_ranker.POPULARITY_GATE_FULL = original


def main() -> int:
    picks = pick_with_gate(metadata_ranker.POPULARITY_GATE_FULL)
    if not picks:
        print("data/runs altinda okunabilir kosu yok.")
        return 1

    relevance = [p[2] for p in picks]
    starved = [p for p in picks if p[2] < NO_SIGNAL]

    print(f"=== {len(picks)} secim, mevcut esik: {metadata_ranker.POPULARITY_GATE_FULL} ===")
    print(f"  alt baslik alakasi ortanca : {statistics.median(relevance):.2f}")
    print(f"  leksik sinyali olmayan     : {len(starved)}/{len(picks)}"
          f" (%{100 * len(starved) / len(picks):.0f})")

    if starved:
        print("\n  sinyalsiz secimler:")
        for subtopic, video, score in sorted(starved, key=lambda p: p[2])[:8]:
            print(f"    alaka={score:4.2f}  {subtopic[:38]:38} -> {video[:44]}")

    print("\n=== esik taramasi ===")
    for gate in (None, 0.75, 1.0, 1.5, 2.0, 3.0):
        swept = pick_with_gate(gate)
        rel = [p[2] for p in swept]
        low = sum(1 for p in swept if p[2] < NO_SIGNAL)
        label = "kapali" if gate is None else f"{gate}"
        print(f"  {label:>6}: ortanca alaka={statistics.median(rel):.2f}  sinyalsiz={low}/{len(swept)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
