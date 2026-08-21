from __future__ import annotations

from src.config import AppConfig
from src.models import FilterOptions, VideoCandidate
from src.providers.errors import (
    ProviderPermanentError,
    ProviderRateLimitedError,
    ProviderTemporaryError,
)
from src.providers.youtube_data_api_provider import YouTubeDataAPIProvider
from src.providers.ytdlp_provider import YtDlpProvider
from src.storage import DEFAULT_USER_ID, SQLiteStore
from src.utils.logging_utils import redact_secrets
from src.utils.retry_utils import retry_with_backoff


def search_candidates(
    config: AppConfig,
    store: SQLiteStore,
    query: str,
    filters: FilterOptions,
    logger=None,
    notes: list[str] | None = None,
    user_id: str = DEFAULT_USER_ID,
) -> list[VideoCandidate]:
    """Aday videolari sirayla saglayicilardan toplar.

    Bir saglayici SIFIR sonuc dondururse bu artik "basari" sayilmaz; bir sonraki
    saglayiciya gecilir. Onceki surum bos listeyi dondurup 6 saat onbellekliyor ve
    yt-dlp yedegini tamamen devre disi birakiyordu.
    """
    # Kota sayaci BURADA baglaniyor: `store` ve `user_id` yalnizca bu katmanda
    # birlikte var. Onbellek isabetlerinde saglayici hic cagrilmadigi icin
    # sayac da hic artmiyor -- olcum bu sayede gercek tuketimi gosteriyor.
    def _record(endpoint: str, units: int) -> None:
        store.record_api_usage(user_id, YouTubeDataAPIProvider.name, endpoint, units)

    providers = [
        YouTubeDataAPIProvider(config, usage_recorder=_record),
        YtDlpProvider(config),
    ]
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
            provider_errors.append(f"{provider.name}: cooldown until {cooldown_until}")
            continue

        cached = store.get_search_cache(provider.name, query, filters.model_dump())
        if cached:
            if logger:
                logger.info("Search cache hit for %s via %s", query, provider.name)
            return cached
        # Bos onbellek kaydi (cached == []) bir sonraki saglayiciyi engellememeli,
        # ama ayni saglayiciyi tekrar cagirmayi da onlemeli.
        if cached is not None:
            if logger:
                logger.info("Empty cached result for %s via %s; trying next provider", query, provider.name)
            provider_errors.append(f"{provider.name}: no results (cached)")
            continue

        try:
            candidates = retry_with_backoff(
                lambda p=provider: p.search(query, filters, config.search_candidates_per_subtopic),
                attempts=config.retry_max_attempts,
                base_delay=config.retry_base_delay_sec,
                logger=logger,
                on_exception=(ProviderTemporaryError,),
            )
        except ProviderRateLimitedError as exc:
            # Hiz siniri: tekrar denemeden dinlendir, sonraki saglayiciya gec.
            message = redact_secrets(str(exc))
            cooldown = exc.retry_after or config.rate_limit_cooldown_sec
            store.mark_provider_cooldown(provider.name, message, cooldown)
            provider_errors.append(f"{provider.name}: rate limited ({cooldown}s cooldown)")
            if logger:
                logger.warning("Rate limited on %s; cooling down for %ss", provider.name, cooldown)
            continue
        except ProviderPermanentError as exc:
            message = redact_secrets(str(exc))
            provider_errors.append(f"{provider.name}: {message}")
            if logger:
                logger.error("Permanent failure on %s: %s", provider.name, message)
            continue
        except Exception as exc:
            message = redact_secrets(str(exc))
            provider_errors.append(f"{provider.name}: {message}")
            failure_count, cooled_down = store.record_provider_failure(
                provider.name,
                message,
                config.provider_cooldown_sec,
                threshold=config.provider_failure_threshold,
            )
            if logger:
                logger.warning(
                    "Search failure %s on %s (cooldown=%s): %s",
                    failure_count,
                    provider.name,
                    cooled_down,
                    message,
                )
            continue

        store.clear_provider_cooldown(provider.name)

        deduped: dict[str, VideoCandidate] = {}
        for candidate in candidates:
            deduped.setdefault(candidate.video_id, candidate)
        final_candidates = list(deduped.values())

        if not final_candidates:
            # Bos sonucu kisa sureli onbellekle (uzun TTL yanlis olurdu) ve devam et.
            store.put_search_cache(
                provider.name, query, filters.model_dump(), [], config.failure_cache_ttl_sec
            )
            provider_errors.append(f"{provider.name}: no results")
            if logger:
                logger.info("%s returned no results for %s; falling back", provider.name, query)
            continue

        store.put_search_cache(
            provider.name, query, filters.model_dump(), final_candidates, config.search_cache_ttl_sec
        )
        return final_candidates

    if provider_errors:
        if logger:
            logger.warning("All search providers failed for query %s: %s", query, provider_errors)
        if notes is not None:
            notes.extend(provider_errors)
    return []
