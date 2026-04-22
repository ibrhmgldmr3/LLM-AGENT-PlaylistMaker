from src.models import FilterOptions, VideoCandidate
from src.services.metadata_ranker import rank_candidates


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
