from __future__ import annotations

from src.config import AppConfig
from src.models import FilterOptions, VideoCandidate
from src.providers.youtube_data_api_provider import ProviderPermanentError, ProviderTemporaryError, YouTubeDataAPIProvider
from src.providers.ytdlp_provider import YtDlpProvider
from src.storage import SQLiteStore
from src.utils.retry_utils import retry_with_backoff


def search_candidates(
    config: AppConfig,
    store: SQLiteStore,
    query: str,
    filters: FilterOptions,
    logger=None,
) -> list[VideoCandidate]:
    providers = [YouTubeDataAPIProvider(config), YtDlpProvider(config)]
    provider_errors: list[str] = []
    for provider in providers:
        if hasattr(provider, "is_configured") and not provider.is_configured():
            if logger:
                logger.info("Skipping %s because it is not configured", provider.name)
            continue

        cooldown_until = store.get_provider_cooldown(provider.name)
        if cooldown_until:
            if logger:
                logger.warning("Skipping %s due to cooldown until %s", provider.name, cooldown_until)
            continue

        cached = store.get_search_cache(provider.name, query, filters.model_dump())
        if cached is not None:
            if logger:
                logger.info("Search cache hit for %s via %s", query, provider.name)
            return cached

        try:
            candidates = retry_with_backoff(
                lambda: provider.search(query, filters, config.search_candidates_per_subtopic),
                attempts=config.retry_max_attempts,
                base_delay=config.retry_base_delay_sec,
                logger=logger,
                on_exception=(ProviderTemporaryError, RuntimeError),
            )
        except ProviderPermanentError as exc:
            provider_errors.append(str(exc))
            continue
        except Exception as exc:
            provider_errors.append(str(exc))
            store.mark_provider_cooldown(provider.name, str(exc), config.provider_cooldown_sec)
            continue

        store.clear_provider_cooldown(provider.name)
        deduped: dict[str, VideoCandidate] = {}
        for candidate in candidates:
            if candidate.video_id not in deduped:
                deduped[candidate.video_id] = candidate
                store.upsert_video_metadata(candidate, config.metadata_cache_ttl_sec)
        final_candidates = list(deduped.values())
        store.put_search_cache(provider.name, query, filters.model_dump(), final_candidates, config.search_cache_ttl_sec)
        return final_candidates

    if logger and provider_errors:
        logger.warning("All search providers failed for query %s: %s", query, provider_errors)
    return []
