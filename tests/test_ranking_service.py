from src.models import FilterOptions, MetadataScore, TranscriptResult, VideoCandidate
from src.providers.youtube_data_api_provider import _parse_iso_duration_seconds
from src.services.metadata_ranker import rank_candidates, to_confidence
from src.services.recommendation_service import select_recommendation


def test_metadata_first_ranking_prefers_stronger_metadata():
    filters = FilterOptions(language="en", max_duration_minutes=60, freshness_preference="balanced")
    candidates = [
        VideoCandidate(
            video_id="strong",
            url="https://example.com/strong",
            title="Python Data Classes Full Tutorial",
            description="Official tutorial with practical examples for Python data classes.",
            channel="Official Python Academy",
            duration_sec=1800,
            view_count=250000,
            publish_date="2024-01-10T00:00:00Z",
            language="en",
        ),
        VideoCandidate(
            video_id="weak",
            url="https://example.com/weak",
            title="Random vlog",
            description="A day in my life.",
            channel="Personal Channel",
            duration_sec=90,
            view_count=120,
            publish_date="2017-01-10T00:00:00Z",
            language="en",
        ),
    ]

    ranked = rank_candidates(candidates, "Python", "Data Classes", filters)

    assert ranked[0][0].video_id == "strong"
    assert ranked[0][1].total > ranked[1][1].total


def test_videos_over_the_duration_limit_are_pushed_to_the_end():
    """Regresyon: 'Max duration' sadece yumusak bir ipucuydu, sinir asilabiliyordu."""
    filters = FilterOptions(language="en", max_duration_minutes=30)
    candidates = [
        VideoCandidate(
            video_id="too-long",
            url="https://example.com/long",
            title="Kalman Filter Complete Masterclass",
            description="Kalman filter deep coverage",
            channel="Official Academy",
            duration_sec=4 * 3600,
            view_count=900_000,
            language="en",
        ),
        VideoCandidate(
            video_id="fits",
            url="https://example.com/fits",
            title="Kalman Filter Basics",
            description="Kalman filter short intro",
            duration_sec=900,
            language="en",
        ),
    ]

    ranked = rank_candidates(candidates, "Kalman", "Filter", filters)

    assert ranked[0][0].video_id == "fits"
    assert "istenen azami süreyi aşıyor" in ranked[1][1].rationale


def test_confidence_is_normalised_to_zero_ten():
    """Regresyon: ham toplam 10'a kirpiliyor, iyi adaylarin hepsi tavana yapisiyordu."""
    assert to_confidence(20.0) == 10.0
    assert to_confidence(-5.0) == 0.0
    low = to_confidence(2.0)
    high = to_confidence(8.0)
    assert 0.0 < low < high < 10.0


def _score(total):
    return MetadataScore(
        total=total,
        title_relevance=1.0,
        description_relevance=0.5,
        channel_quality=0.5,
        duration_fit=1.0,
        difficulty_fit=0.25,
        language_match=1.0,
        freshness=0.5,
        engagement=0.25,
        rationale=[],
    )


def test_transcript_bonus_can_change_the_selected_video():
    """Regresyon: transkript bonusu hesaplaniyordu ama secimi hic etkilemiyordu."""
    first = VideoCandidate(video_id="metadata-leader", url="https://a", title="A")
    second = VideoCandidate(video_id="transcript-leader", url="https://b", title="B")
    ranked = [(first, _score(5.0)), (second, _score(4.5))]
    transcripts = {
        # Metadata lideri: transkript yok -> -0.1 ceza -> 4.9
        "metadata-leader": TranscriptResult(video_id="metadata-leader", status="unavailable", source="none"),
        # Rakip: transkript konuyu birebir kapsiyor -> +1.0 -> 5.5
        "transcript-leader": TranscriptResult(
            video_id="transcript-leader",
            status="available",
            source="youtube_transcript_api",
            text="kalman filter state estimation covariance update",
        ),
    }

    recommendation = select_recommendation("kalman filter", ranked, transcripts, set(), 1)

    assert recommendation is not None
    assert recommendation.video.video_id == "transcript-leader"


def test_selection_skips_already_used_videos():
    first = VideoCandidate(video_id="used", url="https://a", title="A")
    second = VideoCandidate(video_id="fresh", url="https://b", title="B")
    ranked = [(first, _score(9.0)), (second, _score(1.0))]

    recommendation = select_recommendation("topic", ranked, {}, {"used"}, 1)

    assert recommendation is not None
    assert recommendation.video.video_id == "fresh"


def test_iso_duration_handles_days_and_weeks():
    """Regresyon: `P1DT2H` None donuyor ve 26 saatlik video 'suresi bilinmiyor' sayiliyordu."""
    assert _parse_iso_duration_seconds("PT1H2M30S") == 3750
    assert _parse_iso_duration_seconds("PT45M") == 2700
    assert _parse_iso_duration_seconds("P1DT2H") == 93600
    assert _parse_iso_duration_seconds("P1W") == 604800
    assert _parse_iso_duration_seconds("PT0S") == 0
    assert _parse_iso_duration_seconds("garbage") is None
    assert _parse_iso_duration_seconds(None) is None
