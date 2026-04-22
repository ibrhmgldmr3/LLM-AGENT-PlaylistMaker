# Make A Playlist

This project builds a learning playlist from YouTube videos using LLMs and transcripts.

## Requirements

- Python 3.10+
- ffmpeg available on PATH (or set `FFMPEG_PATH`)
- whisper.cpp CLI + model (for fallback transcription)
- Google Gemini API key

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Create `.env` from `.env.example` and set:
- `GEMINI_API_KEY`
- `GEMINI_MODEL`
- `WHISPER_CPP_CLI_PATH`
- `WHISPER_CPP_MODEL_PATH`

Optional anti-rate-limit settings for YouTube:
- `YTDLP_RATE_LIMIT_COOLDOWN_SEC` (wait after 429)
- `YTDLP_COOKIES_FROM_BROWSER` (e.g. `chrome`, `firefox:default`)
- `YTDLP_COOKIES_FILE` (exported Netscape cookie file)
- `YTDLP_PROXY` (proxy URL)

## Run

```bash
streamlit run app.py
```

## Data Output

Each run is stored under `data/runs/<run_id>/result.json`.
Transcripts are cached under `data/cache/transcripts/<video_id>.json`.
