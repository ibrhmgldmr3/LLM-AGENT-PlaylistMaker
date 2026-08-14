"""Faz 0: config'in sunucu / kullanici / calistirma olarak ayrilmasi.

Amac cok kullanicili moda hazirlik. Servis katmani bu ayrimi GORMEZ; tek bir
`AppConfig` almaya devam eder.
"""

import pytest

from src.config import AppConfig, RunOptions, ServerConfig, UserCredentials


def test_appconfig_still_exposes_every_field():
    """Servisler tek nesne aliyor; ayrim onlara sizmamali."""
    combined = set(ServerConfig.model_fields) | set(UserCredentials.model_fields) | set(RunOptions.model_fields)
    missing = combined - set(AppConfig.model_fields)
    assert not missing, f"AppConfig'te eksik alanlar: {missing}"


def test_groups_do_not_overlap():
    """Bir alan yalnizca tek bir gruba ait olmali."""
    server = set(ServerConfig.model_fields)
    credentials = set(UserCredentials.model_fields)
    options = set(RunOptions.model_fields)
    assert not server & credentials
    assert not server & options
    assert not credentials & options


def test_compose_merges_three_sources():
    config = AppConfig.compose(
        server=ServerConfig(max_search_workers=7, data_dir="/tmp/x"),
        credentials=UserCredentials(gemini_api_key="secret", youtube_data_api_key="yt"),
        options=RunOptions(max_subtopics=3, enable_asr_fallback=True),
    )

    assert config.max_search_workers == 7      # sunucu
    assert config.gemini_api_key == "secret"   # kullanici
    assert config.max_subtopics == 3           # calistirma
    assert config.enable_asr_fallback is True


def test_round_trip_split_and_compose():
    """Bolup tekrar birlestirmek ayni yapilandirmayi vermeli."""
    original = AppConfig(gemini_api_key="k", max_subtopics=5, max_search_workers=3)

    rebuilt = AppConfig.compose(
        server=original.server_config(),
        credentials=original.credentials(),
        options=original.run_options(),
    )

    assert rebuilt.model_dump() == original.model_dump()


def test_credentials_carry_the_secrets():
    """BYOK'a gecince bu grup kullanici basina saklanacak."""
    fields = set(UserCredentials.model_fields)
    assert "gemini_api_key" in fields
    assert "youtube_data_api_key" in fields
    assert "youtube_oauth_client_secret" in fields
    # Sunucu ayarlari buraya SIZMAMALI
    assert "max_search_workers" not in fields
    assert "sqlite_path" not in fields


def test_run_options_are_per_request():
    fields = set(RunOptions.model_fields)
    assert {"max_subtopics", "enable_asr_fallback", "metadata_top_k"} <= fields
    # Sir buraya sizmamali
    assert "gemini_api_key" not in fields


def test_validators_survive_the_split():
    """Dogrulayicilar alt modellere tasindi; kalitimla hala calismali."""
    with pytest.raises(ValueError):
        AppConfig(gemini_api_key="k", max_subtopics=99)
    with pytest.raises(ValueError):
        AppConfig(gemini_api_key="k", youtube_playlist_privacy_status="herkes")
    with pytest.raises(ValueError):
        AppConfig(gemini_api_key="k", asr_backend="bilinmeyen")
    with pytest.raises(ValueError):
        AppConfig(gemini_api_key="k", retry_base_delay_sec=0)


def test_validators_work_on_the_submodels_too():
    with pytest.raises(ValueError):
        RunOptions(metadata_top_k=99)
    with pytest.raises(ValueError):
        ServerConfig(max_search_workers=0)


def test_public_capabilities_leak_no_secrets():
    """`GET /api/config` bunu donecek; anahtar degeri asla disari cikmamali."""
    config = AppConfig(
        gemini_api_key="COK-GIZLI-ANAHTAR",
        youtube_data_api_key="AIzaGIZLI",
        youtube_oauth_client_id="id",
        youtube_oauth_client_secret="gizli",
    )

    capabilities = config.public_capabilities()
    serialised = repr(capabilities)

    assert capabilities["gemini_configured"] is True
    assert capabilities["youtube_search_configured"] is True
    assert capabilities["youtube_publish_configured"] is True
    assert "COK-GIZLI-ANAHTAR" not in serialised
    assert "AIzaGIZLI" not in serialised
    assert "gizli" not in serialised
    assert all(isinstance(value, bool) for value in capabilities.values())


def test_capabilities_report_missing_credentials():
    config = AppConfig(gemini_api_key="k")
    capabilities = config.public_capabilities()
    assert capabilities["youtube_search_configured"] is False
    assert capabilities["youtube_publish_configured"] is False
