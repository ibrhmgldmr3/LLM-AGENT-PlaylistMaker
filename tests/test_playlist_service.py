from pathlib import Path

from src.config import AppConfig
from src.models import FilterOptions, MetadataScore, PlaylistRequest, Subtopic, TranscriptResult, VideoCandidate
from src.services import playlist_service


class DummyLLM:
    def generate_subtopics(self, topic: str, language: str):
        return ["Foundations", "Advanced Practice"]


def test_playlist_prevents_duplicate_processing_and_exports(monkeypatch, tmp_path):
    config = AppConfig(
        gemini_api_key="test",
        gemini_model="gemini-test",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "cache" / "app.db"),
    )
    config.ensure_directories()

    candidates = [
        VideoCandidate(
            video_id="video-1",
            url="https://www.youtube.com/watch?v=video-1",
            title="Topic Foundations",
            description="Foundations and setup",
            channel="Official Academy",
            duration_sec=1500,
            view_count=500_000,
            language="en",
        ),
        VideoCandidate(
            video_id="video-2",
            url="https://www.youtube.com/watch?v=video-2",
            title="Topic Advanced Practice",
            description="Advanced drills and exercises",
            channel="Official Academy",
            duration_sec=1800,
            view_count=350_000,
            language="en",
        ),
    ]

    transcript_calls = {"video-1": 0, "video-2": 0}

    monkeypatch.setattr(playlist_service, "GeminiLLMProvider", lambda config: DummyLLM())
    monkeypatch.setattr(playlist_service, "search_candidates", lambda *args, **kwargs: candidates)

    def fake_rank_candidates(candidates, topic, subtopic, filters):
        score_a = MetadataScore(
            total=8.0,
            title_relevance=3.0,
            description_relevance=1.5,
            channel_quality=1.5,
            duration_fit=1.0,
            difficulty_fit=0.5,
            language_match=1.0,
            freshness=0.5,
            engagement=0.5,
            rationale=["strong metadata match"],
        )
        score_b = MetadataScore(
            total=7.0,
            title_relevance=2.0,
            description_relevance=1.2,
            channel_quality=1.5,
            duration_fit=1.0,
            difficulty_fit=0.25,
            language_match=1.0,
            freshness=0.2,
            engagement=0.1,
            rationale=["good metadata match"],
        )
        if "Advanced" in subtopic:
            return [(candidates[0], score_a), (candidates[1], score_b)]
        return [(candidates[0], score_a), (candidates[1], score_b)]

    monkeypatch.setattr(playlist_service, "rank_candidates", fake_rank_candidates)

    def fake_get_transcript(config, store, candidate, run_dir, state, logger=None):
        transcript_calls[candidate.video_id] += 1
        return TranscriptResult(
            video_id=candidate.video_id,
            status="available",
            source="youtube_transcript_api",
            text=f"{candidate.title} transcript",
        )

    monkeypatch.setattr(playlist_service, "get_transcript", fake_get_transcript)

    request = PlaylistRequest(topic="Test Topic", filters=FilterOptions(language="en"))
    result = playlist_service.build_playlist(config, request)

    assert [item.video.video_id for item in result.recommendations] == ["video-1", "video-2"]
    assert transcript_calls["video-1"] == 1
    assert transcript_calls["video-2"] == 1
    assert result.exports is not None
    assert Path(result.exports.json_path).exists()
    assert Path(result.exports.markdown_path).exists()
