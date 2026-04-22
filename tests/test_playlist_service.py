from src.services import playlist_service
from src.config import AppConfig
from src.models import VideoCandidate, Transcript, VideoScore


def test_playlist_dedup(monkeypatch, tmp_path):
    config = AppConfig(
        GEMINI_API_KEY="x",
        GEMINI_MODEL="test",
        YOUTUBE_SEARCH_PER_TOPIC=8,
        TRANSCRIPT_SCORE_TOP_K=3,
        REQUEST_TIMEOUT_SEC=10,
        RETRY_MAX_ATTEMPTS=1,
        RETRY_BASE_DELAY_SEC=0.1,
        DATA_DIR=str(tmp_path),
    )

    monkeypatch.setattr(playlist_service, "generate_subtopics", lambda *args, **kwargs: ["Sub A", "Sub B"])

    candidates = [
        VideoCandidate(video_id="vid1", url="https://www.youtube.com/watch?v=vid1", title="Video 1", metadata_score=1.0),
        VideoCandidate(video_id="vid2", url="https://www.youtube.com/watch?v=vid2", title="Video 2", metadata_score=0.5),
    ]

    monkeypatch.setattr(playlist_service, "search_candidates", lambda *args, **kwargs: candidates)
    monkeypatch.setattr(playlist_service, "metadata_prefilter", lambda cands, *args, **kwargs: cands)
    monkeypatch.setattr(playlist_service, "get_transcript", lambda *args, **kwargs: Transcript(video_id="vid1", language="en", source="x", text="t", fetched_at="now"))

    def fake_score(*args, **kwargs):
        return VideoScore(
            kapsam_uyumu=8,
            bilgi_derinligi=7,
            anlatim_tarzi=7,
            hedef_kitle=6,
            yapisal_tutarlilik=7,
            genel_puan=9,
            yorum="ok",
        )

    monkeypatch.setattr(playlist_service, "score_transcript", fake_score)

    result = playlist_service.build_playlist(config, "Topic")
    assert len(result.selected_videos) == 1
    assert result.selected_videos[0].video_id == "vid1"
