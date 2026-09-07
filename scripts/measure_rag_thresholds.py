"""1. kapinin esiklerini GERCEK sorularla olcer ve tarar.

`docs/rag-plan.md` §8'in 1-3. maddeleri "once olc" diyor ve olcemedigi icin
bekliyordu; eksik olan sey buydu. Betik, etiketli bir soru kumesini gercek bir
ogrenme alanina karsi kosturup su sorulari yanitliyor:

  * Yanlis "bulamadim" orani ne? (cevabi OLAN soru kapida kesiliyor mu)
  * `RAG_MIN_SIMILARITY` bugunku embedding modeli icin dogru yerde mi?
  * Cevabi tasidigi bilinen kaynak, baglama GIRIYOR mu?

Kullanim:

    python scripts/measure_rag_thresholds.py golden.json

`golden.json` bicimi -- `answerable` ELDE etiketlenir, betik tahmin etmez:

    {
      "space_id": "sp_abc123",
      "questions": [
        {"question": "kovaryans nasil kuculur", "answerable": true},
        {"question": "kovaryans nasil kuculur", "answerable": true,
         "expected_source_id": "video:abc"},
        {"question": "bugun hava nasil", "answerable": false}
      ]
    }

MALIYET: soru basina BIR embedding cagrisi. LLM yanit cagrisi YAPILMIYOR --
olculen sey 1. kapi ve erisim, modelin yaniti degil. 50 soruluk bir kume birkac
kurus tutuyor ve tekrar tekrar kosturulabilir olmasi tam da bu yuzden onemli.

SINIR, pesinen: bu betik "kapi dogru karar veriyor mu" sorusunu yanitliyor,
"yanit dogru mu" sorusunu degil. Ikincisi 2. ve 3. kapinin isi ve elde
etiketlenmis yanit metni gerektirir -- ayri bir olcum.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import AppConfig, load_config  # noqa: E402
from src.providers import create_rag_llm_provider  # noqa: E402
from src.services import rag_service  # noqa: E402
from src.storage import SQLiteStore  # noqa: E402

# Taranacak benzerlik esikleri. Aralik `gemini-embedding-001` olcumunden geliyor
# (alakasiz sorular 0.50 tabaninda, ilgili sorular 0.80 uzeri) ama BASKA bir
# modelde kumeler baska yerde olabilir -- tarama zaten bunu gormek icin.
_SWEEP = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85)


def _measure(config: AppConfig, store: SQLiteStore, llm, space_id: str, question: str) -> dict:
    """Tek bir soru icin 1. kapinin girdilerini olcer. LLM'e SORMAZ."""
    lexical, semantic = rag_service._retrieve(
        config, store, llm, space_id, question, "local"
    )
    selected = rag_service._select_context(config, store, lexical, semantic)
    return {
        "similarity": semantic[0][1] if semantic else 0.0,
        "coverage": rag_service._best_lexical_coverage(store, lexical, question),
        "sources": {chunk["source_id"] for chunk in selected},
        "chunks": len(selected),
    }


def _report_distribution(rows: list[dict]) -> None:
    """Iki kumenin skorlarini yan yana koyar: esik ancak aralarinda olabilir."""
    for label, wanted in (("Cevabi OLAN", True), ("Cevabi OLMAYAN", False)):
        scores = sorted(row["similarity"] for row in rows if row["answerable"] is wanted)
        if not scores:
            continue
        print(f"  {label:16} n={len(scores):3}  "
              f"min={scores[0]:.3f}  medyan={scores[len(scores) // 2]:.3f}  max={scores[-1]:.3f}")


def _sweep_thresholds(rows: list[dict], min_coverage: float) -> None:
    """Her esikte kapinin ne kestigini gosterir.

    IKI hata birlikte raporlaniyor: yanlis "bulamadim" (cevabi olan soru
    kesildi) ve gereksiz cagri (cevabi olmayan soru modele gitti). Tek basina
    bakildiginda ilkini sifirlamak icin esigi dibe cekmek "iyilesme" gibi
    gorunur -- oysa kapinin varlik sebebi ikincisi.
    """
    print(f"\n{'esik':>6} | {'yanlis-bulamadim':>17} | {'gereksiz cagri':>15}")
    print("-" * 46)
    for threshold in _SWEEP:
        false_abstain = sum(
            1
            for row in rows
            if row["answerable"]
            and row["similarity"] < threshold
            and row["coverage"] < min_coverage
        )
        wasted = sum(
            1
            for row in rows
            if not row["answerable"]
            and (row["similarity"] >= threshold or row["coverage"] >= min_coverage)
        )
        answerable = sum(1 for row in rows if row["answerable"]) or 1
        unanswerable = sum(1 for row in rows if not row["answerable"]) or 1
        print(f"{threshold:>6.2f} | {false_abstain:>7} / {answerable:<7} | "
              f"{wasted:>6} / {unanswerable:<6}")


def _report_retrieval(rows: list[dict]) -> None:
    """Cevabi tasidigi BILINEN kaynak baglama girdi mi.

    Bu, esiklerden bagimsiz bir ariza: parca hic secilmiyorsa hicbir esik onu
    kurtarmaz ve sorun `RAG_TOP_K` / kaynak basina tavan / karakter butcesinde
    demektir (§8 madde 3).
    """
    labelled = [row for row in rows if row.get("expected_source_id")]
    if not labelled:
        return
    misses = [row for row in labelled if row["expected_source_id"] not in row["sources"]]
    print(f"\nBeklenen kaynak baglama girdi: {len(labelled) - len(misses)}/{len(labelled)}")
    for row in misses:
        print(f"  KACIRILDI  benzerlik={row['similarity']:.3f} "
              f"parca={row['chunks']}  {row['question'][:60]!r}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    golden = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    space_id = golden["space_id"]
    questions = golden["questions"]

    config = load_config()
    store = SQLiteStore(config.sqlite_path)
    llm = create_rag_llm_provider(config)

    if store.count_chunks(space_id) == 0:
        print(f"HATA: '{space_id}' alaninda aranabilir parca yok.")
        return 1

    rows: list[dict] = []
    for index, item in enumerate(questions, start=1):
        question = item["question"]
        print(f"[{index}/{len(questions)}] {question[:60]}", file=sys.stderr)
        rows.append(
            {
                **item,
                **_measure(config, store, llm, space_id, question),
            }
        )

    print(f"\nModel: {config.embedding_model()}")
    print(f"Alan : {space_id}  ({store.count_chunks(space_id)} parca)")
    print(f"\nBenzerlik dagilimi (esik ancak iki kumenin ARASINDA olabilir):")
    _report_distribution(rows)
    _sweep_thresholds(rows, config.rag_min_lexical_coverage)
    _report_retrieval(rows)
    print(f"\nBugunku ayar: RAG_MIN_SIMILARITY={config.rag_min_similarity} "
          f"RAG_MIN_LEXICAL_COVERAGE={config.rag_min_lexical_coverage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
