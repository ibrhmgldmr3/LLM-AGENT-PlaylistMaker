# Make A Playlist

Production-leaning Streamlit app for generating topic-based YouTube learning playlists with:

- Gemini-driven topic decomposition
- YouTube Data API search as the primary path
- `yt-dlp` fallback for discovery and subtitle/audio access
- metadata-first ranking before any transcript work
- optional transcript enrichment with provider fallback
- SQLite-backed cache and run metadata
- JSON and Markdown run exports
- optional official YouTube playlist creation through OAuth

## Runtime

- Entry point: `app.py`
- Main app command:

```bash
streamlit run app.py
```

## Requirements

- Python 3.10+
- `ffmpeg` on `PATH` if ASR audio extraction is used
- Gemini API key
- Optional YouTube Data API key for primary search and playlist creation
- Optional `whisper.cpp` binary + model if using the fallback ASR adapter

## Install

```bash
pip install -r requirements.txt
```

Create `.env` from `.env.example`.


## Canonical Environment Variables

Required:

- `GEMINI_API_KEY`

Recommended:

- `GEMINI_MODEL`
- `YOUTUBE_DATA_API_KEY`

Optional playlist publishing:

- `YOUTUBE_OAUTH_CLIENT_SECRET_FILE`
- `YOUTUBE_OAUTH_TOKEN_FILE`

ASR:

- `ASR_BACKEND=auto|faster-whisper|whisper.cpp`
- `FASTER_WHISPER_MODEL_SIZE`
- `FASTER_WHISPER_DEVICE`
- `FASTER_WHISPER_COMPUTE_TYPE`
- `WHISPER_CPP_CLI_PATH`
- `WHISPER_CPP_MODEL_PATH`
- `FFMPEG_PATH`

Storage and pipeline:

- `DATA_DIR`
- `SQLITE_PATH`
- `SEARCH_CANDIDATES_PER_SUBTOPIC`
- `METADATA_TOP_K`
- `REQUEST_TIMEOUT_SEC`
- `RETRY_MAX_ATTEMPTS`
- `RETRY_BASE_DELAY_SEC`

## Pipeline

1. Topic decomposition with Gemini
2. Candidate discovery via YouTube Data API, with `yt-dlp` fallback
3. Metadata-first ranking across title, description, channel, duration, language, freshness, and engagement
4. Optional transcript enrichment:
   - `youtube-transcript-api`
   - `yt-dlp` subtitle extraction
   - ASR fallback (`faster-whisper` primary, `whisper.cpp` optional)
5. Final playlist assembly with confidence scores and selection reasons
6. Optional official YouTube playlist publishing

## Data Output

Run artifacts are stored under:

- `data/runs/<run_id>/result.json`
- `data/runs/<run_id>/study_plan.md`

SQLite cache and run metadata are stored at:

- `data/cache/app.db` by default

## Tests

```bash
pytest -q
```
