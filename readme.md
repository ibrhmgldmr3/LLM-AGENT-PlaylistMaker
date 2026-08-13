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
- Gemini API key **with available quota/credits**
- `ffmpeg` on `PATH` (or `FFMPEG_PATH`) if ASR audio extraction is used
- A JS runtime on `PATH` (`node` is fine) — yt-dlp needs it to solve YouTube's
  `nsig` challenge; without it audio download fails with "Requested format is not
  available". Requires `yt-dlp >= 2025.11`, which is where the `js_runtimes` option
  landed. The app warns in the sidebar if the installed version is too old.
- Optional YouTube Data API key for primary search
- Optional YouTube OAuth client credentials for playlist publishing (the API key is
  *not* used for this)
- Optional `whisper.cpp` binary + model if using the fallback ASR adapter. It reads
  16 kHz mono WAV only; the downloader produces exactly that.

`yt-dlp` tracks YouTube changes closely — if discovery or audio download starts
failing, upgrade it first:

```bash
pip install -U yt-dlp
```

## Install

```bash
pip install -r requirements.txt
```

Create `.env` from `.env.example`.

## Git Hygiene

- `.env` stays local and is ignored by git
- runtime output under `data/` stays local and is ignored by git
- local `whisper.cpp/` builds, binaries, and models stay local and are ignored by git
- if you need to share config, update `.env.example` instead of committing `.env`

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

The run is split into phases so that independent, network-bound work happens concurrently
while order-dependent work stays sequential:

1. **Topic decomposition** with Gemini (one call, capped at `MAX_SUBTOPICS`)
2. **Candidate discovery — parallel** across subtopics (`MAX_SEARCH_WORKERS`),
   via YouTube Data API with `yt-dlp` fallback. An empty result falls through to the
   next provider rather than ending the search.
3. **Metadata-first ranking** across title, description, channel, duration, language,
   freshness, and engagement. Videos over `Max duration` are pushed behind all others.
4. **Transcript enrichment — parallel** (`MAX_TRANSCRIPT_WORKERS`), deduplicated per
   video across subtopics:
   - `youtube-transcript-api`
   - `yt-dlp` subtitle extraction
   - ASR fallback (`faster-whisper` primary, `whisper.cpp` optional) —
     **off by default**, see below
5. **Selection — sequential**, so subtopic order and cross-subtopic deduplication are
   deterministic. The shortlist is re-ranked by `metadata score + transcript bonus`,
   so transcripts actually influence which video is picked.
6. Optional official YouTube playlist publishing (OAuth only; no API key needed)

Parallelising steps 2 and 4 measured a ~3.5x end-to-end speedup on a 4-subtopic run
(12.8s → 3.7s) with byte-identical output.

### ASR is off by default

Downloading audio and running Whisper costs roughly 90–120 seconds per video on CPU,
while the first two transcript providers already cover the large majority of videos and
a transcript contributes at most +1.0 to a ~19-point ranking scale. Enable it from the
sidebar toggle or with `ENABLE_ASR_FALLBACK=true`; `MAX_ASR_VIDEOS_PER_RUN` caps the
cost per run.

### How discovery works

The candidate pool is built to be wider than any single search:

- **The LLM writes the search query**, not the code. One call returns
  `{title, query}` per subtopic, so no extra cost. Mechanically concatenating
  topic and subtopic produced unnatural, repetitive queries
  ("Makine öğrenmesi ile zaman serisi tahmini XGBoost ile zaman serisi tahmini");
  the model instead emits what people actually search for
  ("XGBoost LightGBM zaman serisi tahmini python"). Queries follow the requested
  language, so choosing `tr` does not silently return an all-English playlist.
- **Candidates are pooled across subtopics.** Every subtopic ranks against the
  union of all searches, not just its own results — on a 5-subtopic run that is
  a pool of ~55 unique videos instead of ~12, at zero extra quota. A subtopic
  whose own search fails is still served from the pool (and the run says so).
- **Starved queries are widened.** A search returning nothing is retried with the
  bare subtopic title before giving up.
- **Subtopics merge on distinctive tokens**, not raw string similarity. "XGBoost ile
  zaman serisi tahmini" and "LSTM ile zaman serisi tahmini" are 0.885 similar as
  strings and were being collapsed into one — silently dropping a whole method from
  the playlist. Comparison now ignores tokens shared with the topic.

### How ranking works

Relevance is measured against the **subtopic and the topic separately**, not against
a single merged query. Merging them let the shared topic tokens dominate, so every
subtopic produced nearly the same ordering — the ARIMA subtopic and the LSTM subtopic
would pick the same video.

Four things make the score discriminative:

- **Subtopic outweighs topic** (`2.6` vs `1.4` on the title) — the subtopic is what
  distinguishes one slot in the playlist from another.
- **Pool-based IDF** — the candidate pool is itself the corpus. `xgboost` appears in one
  title, `model` in a dozen, so the rare term carries the weight. Without this, a subtopic
  could be won by a video that matched only its generic words: "Ağaç Tabanlı Modeller ve
  XGBoost" was going to an LSTM text-generation video on the strength of `tabanlı` and
  `modeller` alone, with `xgboost` unmatched. When no pool is available the ranker falls
  back to down-weighting tokens shared with the topic.
- **A low topic weight** (`0.8` against the subtopic's `3.2`). Discovery already
  constrains the pool to the topic, so a high topic weight mostly rewards echoing the
  topic phrasing — which structurally penalised English candidates in Turkish runs,
  on top of the language score.
- **Stem-aware matching** — Turkish is agglutinative, so `tahmin`/`tahmini` and
  `model`/`modelleri` must match; English `filter`/`filters` too. Matching is by common
  prefix with guards against false friends (`veri` does not match `verimlilikten`).
- **Pedagogical and structural words are stopwords** — `temelleri`, `giriş`, `basics`,
  `explained`, `tabanlı`, `based`. They appear in subtopic titles but carry no domain
  meaning, and being rare they would otherwise score *high* under IDF. A cognitive-science
  lecture won a time-series subtopic purely on the word "temelleri". Domain words that are
  merely common (`model`, `yöntem`) are deliberately left in — IDF handles those.

A subtopic whose terms appear in no pool title has no signal to rank on, and the
assignment fills it with whatever maximises the playlist total. Those picks score low and
the UI labels them a weak match rather than presenting them as good ones.

Channel authority uses the real **subscriber count** (`channels.list`, 1 quota unit per
search) on a log scale, falling back to name heuristics only when that data is missing
(the `yt-dlp` path). Engagement is **views per day**, so a two-week-old video is not
punished against a five-year-old one, and view count is no longer double-counted.

Videos are then assigned to subtopics by **maximising total fit across the whole
playlist**, not greedily per subtopic. Greedy assignment let an early subtopic take a
video that a later subtopic needed far more. A small `CHANNEL_REPEAT_PENALTY` breaks
near-ties in favour of a different channel without overriding a clearly better video.

### Rate limits and IP blocks

YouTube throttles unauthenticated transcript requests per IP. When it does, retrying is
actively harmful — it deepens the block. So `HTTP 429`, `RequestBlocked` and `IpBlocked`
are modelled as their own error class, separate from ordinary transient failures:

- they are **never retried**,
- they cool the provider down **immediately**, without waiting for
  `PROVIDER_FAILURE_THRESHOLD`,
- the cooldown uses `RATE_LIMIT_COOLDOWN_SEC` (30 min default, longer than the ordinary
  one) or the server's `Retry-After` header when it sends one.

Previously a single rate-limit event turned into roughly `workers × attempts × providers`
= ~24 requests against a server already asking for less traffic; it is now bounded by the
worker count, after which every remaining video skips the provider outright.

If you hit this regularly, authenticate the requests with browser cookies by adding this
line to `.env`:

```
YTDLP_COOKIES_FROM_BROWSER=chrome
```

Edit `.env` in a text editor, or on Windows PowerShell use `Add-Content` with an explicit
encoding. Do **not** append with `>>` or `Out-File`: Windows PowerShell writes UTF-16
there, and a UTF-16 line inside a UTF-8 `.env` makes `python-dotenv` fail with
`ValueError: embedded null character`. The app now detects this and says so, but the file
still has to be repaired.

```powershell
Add-Content .env "YTDLP_COOKIES_FROM_BROWSER=chrome" -Encoding utf8
```

The playlist still builds without transcripts — they are enrichment, and the ranking
falls back to metadata.

### Provider health

A single video failing (subtitles disabled, private, removed) never penalises a
provider. Only infrastructure-level failures count towards
`PROVIDER_FAILURE_THRESHOLD` consecutive errors, after which that provider is skipped
for `PROVIDER_COOLDOWN_SEC`.

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
