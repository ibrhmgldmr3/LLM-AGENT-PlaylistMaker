"""Parca vektorleri: uretim, saklama ve benzerlik aramasi.

VEKTOR VERITABANI YOK. Bir ogrenme alani birkac bin parca tasiyor; 2.500 parca
x 768 boyut = ~7 MB float32 ve kosinus hesabi numpy ile milisaniyelerle
olculuyor. Chroma/FAISS/pgvector eklemek, bugun olmayan bir olcek sorununu
cozmek olurdu -- ayni gerekce `playlist_service`in RAG kullanmama kararinda da
yaziliydi ve orada bugun de gecerli.

Vektorler `chunk_embedding` tablosunda float32 BLOB olarak duruyor, MODEL ADIYLA
BIRLIKTE: farkli modellerin vektorleri arasinda kosinus benzerligi anlamsizdir.
Model degistiginde eski satirlar "gomulmemis" sayilip tembel bicimde yeniden
uretiliyor (bkz. `SQLiteStore.chunks_missing_embeddings`).
"""

from __future__ import annotations

import logging
import struct

import numpy as np

from src.config import AppConfig
from src.storage import DEFAULT_USER_ID, SQLiteStore

_log = logging.getLogger(__name__)


def pack_vector(values) -> bytes:
    """float32, little-endian. Platformdan bagimsiz olmasi icin acik format."""
    values = list(values)
    return struct.pack(f"<{len(values)}f", *values)


def unpack_vector(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype="<f4")


def _normalise(matrix: np.ndarray) -> np.ndarray:
    """Satirlari birim uzunluga getirir; sifir vektorleri oldugu gibi birakir.

    Normalize edilmis vektorlerde kosinus benzerligi basit bir ic carpima
    donusuyor. Sifir vektor bolmesi `nan` uretir ve `nan` her karsilastirmada
    False doner -- yani esik kontrolu SESSIZCE her zaman basarisiz olurdu.
    """
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def embed_missing(
    config: AppConfig,
    store: SQLiteStore,
    llm,
    space_id: str,
    user_id: str = DEFAULT_USER_ID,
    progress=None,
) -> int:
    """Bu alanda eksik olan vektorleri uretir; uretilen sayiyi doner.

    Toplu (batch) cagiriliyor: parca basina bir HTTP istegi, 200 parcalik bir
    videoda 200 gidis-donus demek olurdu.

    Bir grup basarisiz olursa istisna YUKSELIYOR ve is katmani bunu kaynagin
    `status="failed"` durumuna cevirebiliyor. Sessizce atlamak, alanin yarisi
    aranabilir yarisi aranamaz halde kalmasi ve kullanicinin bunu ancak
    "bulamadim" yanitlarindan sezmesi demek olurdu.
    """
    model = config.embedding_model()
    pending = store.chunks_missing_embeddings(space_id, model)
    if not pending:
        return 0

    batch_size = max(1, config.embedding_batch_size)
    written = 0
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        vectors = llm.embed([text for _chunk_id, text in batch])
        store.put_embeddings(
            [
                (chunk_id, model, len(vector), pack_vector(vector))
                for (chunk_id, _text), vector in zip(batch, vectors)
            ]
        )
        _record_usage(store, config, user_id, calls=1)
        written += len(batch)
        if progress:
            progress(written, len(pending))
    return written


def embed_question(config: AppConfig, store: SQLiteStore, llm, question: str, user_id: str):
    """Soruyu vektore cevirir. Tek elemanli bir `embed` cagrisi."""
    vectors = llm.embed([question])
    _record_usage(store, config, user_id, calls=1)
    return np.asarray(vectors[0], dtype="<f4") if vectors else None


def similarity_search(
    store: SQLiteStore,
    config: AppConfig,
    space_id: str,
    question_vector,
    limit: int = 20,
) -> list[tuple[int, float]]:
    """Kosinus benzerligine gore `(chunk_id, skor)`, EN IYIDEN kotuye.

    Skor `[-1, 1]` araliginda ve `rag_min_similarity` esigi DOGRUDAN bununla
    karsilastiriliyor -- yani deger kalibre edilebilir olmali, bu yuzden ham
    kosinus birakiliyor (yeniden olcekleme esigi okunamaz hale getirirdi).
    """
    if question_vector is None:
        return []
    rows = store.load_embeddings(space_id, config.embedding_model())
    if not rows:
        return []

    chunk_ids = [chunk_id for chunk_id, _blob in rows]
    matrix = np.vstack([unpack_vector(blob) for _chunk_id, blob in rows])
    query = np.asarray(question_vector, dtype="<f4").reshape(1, -1)

    if matrix.shape[1] != query.shape[1]:
        # Boyut uyusmazligi = alanda BASKA bir modelin vektorleri var. Model
        # adiyla suzuyoruz, dolayisiyla buraya normalde dusulmez; yine de
        # patlamak yerine leksik yola birakiyoruz.
        _log.warning(
            "Vektor boyutu uyusmuyor (indeks=%s, sorgu=%s); anlamsal arama atlandi",
            matrix.shape[1],
            query.shape[1],
        )
        return []

    scores = (_normalise(matrix) @ _normalise(query).T).ravel()
    order = np.argsort(-scores)[: max(1, limit)]
    return [(chunk_ids[index], float(scores[index])) for index in order]


def _record_usage(store: SQLiteStore, config: AppConfig, user_id: str, calls: int) -> None:
    """Embedding cagrisini kullanim raporuna yazar.

    `units=0` BILEREK. `api_usage.units` sutunu YouTube kota butcesinin
    kaynagi: `create_run_within_daily_limit` gun icindeki TUM satirlarin
    `units` toplamini okuyup `MAX_UNITS_PER_DAY` ile karsilastiriyor ve
    saglayiciya gore ayirmiyor. Buraya sifirdan buyuk bir deger yazmak,
    embedding cagrilarini YouTube kotasindan dusurur ve kullanicilarin
    calistirma hakkini alakasiz bir sebeple bitirirdi.

    Cagri SAYISI yine de tutuluyor: `GET /api/admin/usage` "bugun kac embedding
    cagrisi yapildi" sorusunu yanitlayabilmeli -- bir olcum aracinin en kotu
    arizasi sessizce hicbir sey kaydetmemesidir.
    """
    provider = f"{config.effective_embedding_provider()}_embedding"
    try:
        for _ in range(calls):
            store.record_api_usage(user_id, provider, "embeddings", units=0)
    except Exception:  # pragma: no cover - olcum isi dusurmemeli
        _log.warning("Embedding kullanimi kaydedilemedi", exc_info=True)
