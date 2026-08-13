from __future__ import annotations

from difflib import SequenceMatcher

from src.models import Subtopic
from src.providers.errors import ProviderTemporaryError
from src.providers.llm_provider import DEFAULT_SUBTOPIC_COUNT, LLMProvider
from src.utils.retry_utils import retry_with_backoff
from src.utils.text_utils import normalize_text, query_tokens, slugify_text


# Ham metin benzerligi esigi. Yalnizca AYIRT EDICI token kalmadiginda kullanilir.
SIMILARITY_MERGE_THRESHOLD = 0.87
# Ayirt edici token kumeleri icin Jaccard esigi.
TOKEN_MERGE_THRESHOLD = 0.8


def generate_subtopics(
    provider: LLMProvider,
    topic: str,
    language: str,
    max_items: int = DEFAULT_SUBTOPIC_COUNT,
    logger=None,
) -> list[Subtopic]:
    """Konuyu tekillestirilmis alt konulara boler.

    `max_items` bir UST SINIR olarak uygulanir. Onceki surum modelin dondurdugu
    listeyi hic kirpmiyordu; 20 alt konu donen bir yanit 20 arama cagrisi ve
    YouTube gunluk kotasinin buyuk bolumunun tukenmesi anlamina geliyordu.
    """
    attempts = getattr(getattr(provider, "config", None), "retry_max_attempts", 3)
    base_delay = getattr(getattr(provider, "config", None), "retry_base_delay_sec", 1.0)
    raw_items = retry_with_backoff(
        lambda: provider.generate_subtopics(topic, language, max_items),
        attempts=attempts,
        base_delay=base_delay,
        logger=logger,
        on_exception=(ProviderTemporaryError,),
    )

    # Konu tokenlari "arka plan": alt konulari birbirinden AYIRAN sey degiller.
    background = query_tokens(topic)

    results: list[Subtopic] = []
    for item in raw_items or []:
        title, search_query, search_query_en = _unpack(item)
        title = normalize_text(title)
        if not title:
            continue
        normalized = slugify_text(title)
        if not normalized:
            continue

        existing = _find_duplicate(results, title, normalized, background)
        if existing is not None:
            if title not in existing.source_titles:
                existing.source_titles.append(title)
            continue

        results.append(
            Subtopic(
                title=title,
                normalized_title=normalized,
                source_titles=[title],
                search_query=normalize_text(search_query) or None,
                search_query_en=normalize_text(search_query_en) or None,
            )
        )
        if len(results) >= max_items:
            break

    if logger:
        logger.info("Generated %s normalized subtopics (cap %s)", len(results), max_items)
    return results


def _unpack(item) -> tuple[str, str, str]:
    """Hem duz metin hem {"title", "query", "query_en"} bicimini kabul eder."""
    if isinstance(item, dict):
        title = item.get("title") or item.get("subtopic") or item.get("name") or ""
        query = item.get("query") or item.get("search_query") or ""
        query_en = item.get("query_en") or item.get("english_query") or ""
        return str(title), str(query), str(query_en)
    return str(item), "", ""


def _find_duplicate(
    results: list[Subtopic], title: str, normalized: str, background: set[str]
) -> Subtopic | None:
    """Ayni alt konunun tekrari mi?

    ONEMLI: karsilastirma AYIRT EDICI tokenlar uzerinden yapilir. Ham metin
    benzerligi, ortak kalibi olan basliklarda yaniltici oluyordu:
    "XGBoost ile zaman serisi tahmini" ile "LSTM ile zaman serisi tahmini"
    0.885 benzerlik aliyor ve birlestiriliyordu; oysa bunlar tamamen farkli
    yontemler ve playlist sessizce kisaliyordu.
    """
    candidate_tokens = query_tokens(title) - background

    for existing in results:
        if existing.normalized_title == normalized:
            return existing

        existing_tokens = query_tokens(existing.title) - background
        if candidate_tokens and existing_tokens:
            intersection = candidate_tokens & existing_tokens
            union = candidate_tokens | existing_tokens
            if len(intersection) / len(union) >= TOKEN_MERGE_THRESHOLD:
                return existing
            # Ayirt edici tokenlar farkliysa metin benzerligine BAKMA.
            continue

        # Ayirt edici token kalmadiysa (her ikisi de sadece konu kelimelerinden
        # olusuyorsa) ham metin benzerligine geri don.
        if _similar(existing.title, title) >= SIMILARITY_MERGE_THRESHOLD:
            return existing
    return None


def _similar(left: str, right: str) -> float:
    return SequenceMatcher(a=left.lower(), b=right.lower()).ratio()
