# Make A Playlist

Turns one learning goal into an ordered YouTube playlist.

Give it a topic. It asks Gemini to break the topic into distinct subtopics, searches
YouTube for each, ranks candidates on metadata, optionally enriches the shortlist with
transcripts, and assigns one video per subtopic — then exports the result as JSON and
Markdown, and can publish it as a real YouTube playlist.

A FastAPI backend with a React frontend.

```bash
# development — two processes, hot reload on both
python -m uvicorn api.main:app --reload --port 8000  # API → /docs for OpenAPI
cd web && npm install && npm run dev        # React UI → localhost:5173

# production — one process serves both
cd web && npm run build
python -m uvicorn api.main:app --port 8000           # → localhost:8000
```

In development Vite proxies `/api` to port 8000, so start the API first. In production
the API serves the built frontend from `web/dist`, so a single process is enough.

> Use `python -m uvicorn`, not the bare `uvicorn` command: on Windows the `uvicorn.exe`
> on `PATH` may belong to a different Python installation than the one your dependencies
> are installed in.

---

## Requirements

| | |
|---|---|
| Python | **3.13** — the version CI tests and development targets. 3.11/3.12 should work but are not verified. |
| **Gemini API key** | required, **with available quota/credits** |
| YouTube Data API key | optional — primary search path; without it, `yt-dlp` is used |
| YouTube OAuth client | optional — only for publishing playlists (the API key is *not* used for this) |
| A JS runtime (`node`) | needed for audio download; see below |
| `ffmpeg` | needed only when ASR is enabled |
| `whisper.cpp` binary + model | optional alternative ASR backend |

**About the JS runtime:** yt-dlp needs one to solve YouTube's `nsig` challenge. Without
it, audio download fails with *"Requested format is not available"*. This requires
`yt-dlp >= 2025.11`, where the `js_runtimes` option landed — older versions silently
ignore the setting. The API reports this through `GET /api/config` and the UI surfaces it.

`yt-dlp` tracks YouTube's changes closely. If discovery or audio download starts failing,
upgrade it first:

```bash
pip install -U yt-dlp
```

## Install

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set at least `GEMINI_API_KEY`.

> **Editing `.env` on Windows:** use an editor or `Add-Content .env "KEY=value" -Encoding utf8`.
> Do **not** append with `>>` or `Out-File` — Windows PowerShell writes UTF-16 there, and a
> UTF-16 line inside a UTF-8 `.env` makes `python-dotenv` fail with
> `ValueError: embedded null character`. The app detects this and tells you which lines are
> damaged, but the file still has to be repaired.

---

## How a run works

The pipeline is split into phases so independent, network-bound work runs concurrently
while order-dependent work stays sequential and deterministic.

| # | Phase | Concurrency |
|---|---|---|
| 1 | **Topic decomposition** — one Gemini call, capped at `MAX_SUBTOPICS` | single call |
| 2 | **Candidate discovery** — YouTube Data API, `yt-dlp` fallback | parallel (`MAX_SEARCH_WORKERS`) |
| 3 | **Metadata ranking** — every subtopic scored against the pooled candidates | in-process |
| 4 | **Transcript enrichment** — deduplicated per video across subtopics | parallel (`MAX_TRANSCRIPT_WORKERS`) |
| 5 | **Assignment** — one video per subtopic, maximising total fit | sequential |
| 6 | **Export** — JSON + Markdown, optional YouTube playlist | sequential |

Parallelising phases 2 and 4 measured a **~3.5x end-to-end speedup** on a 4-subtopic run
(12.8s → 3.7s) producing byte-identical output.

### Discovery

The candidate pool is deliberately built wider than any single search:

- **The LLM writes the search queries, not the code.** One call returns
  `{title, query, query_en}` per subtopic, so the extra queries cost no extra LLM calls.
  Mechanically concatenating topic and subtopic produced unnatural, repetitive strings
  (*"Makine öğrenmesi ile zaman serisi tahmini XGBoost ile zaman serisi tahmini"*); the
  model instead emits what people actually search for (*"XGBoost LightGBM zaman serisi
  tahmini python"*). Queries follow the requested language, so choosing `tr` does not
  silently return an all-English playlist.
- **Optional bilingual search.** When the interface language is not English, a second
  English query per subtopic can be run and the pools merged. On a Turkish machine-learning
  topic this grew the pool from 52 to 95 videos and lifted the weakest slot; it costs one
  extra `search.list` call per subtopic. Turkish videos still outrank English ones when
  both fit, so it only changes slots where local content is thin.
- **Candidates are pooled across subtopics.** Every subtopic ranks against the union of
  all searches, not just its own results — on a 5-subtopic run that is ~55 unique videos
  instead of ~12, at zero extra quota. A subtopic whose own search fails is still served
  from the pool, and the run says so.
- **Starved queries are widened.** A search returning nothing is retried with the bare
  subtopic title before giving up.
- **Subtopics merge on distinctive tokens**, not raw string similarity. *"XGBoost ile zaman
  serisi tahmini"* and *"LSTM ile zaman serisi tahmini"* are 0.885 similar as strings and
  were being collapsed into one — silently dropping an entire method from the playlist.

### Ranking

Relevance is measured against the **subtopic and the topic separately**, never as one
merged query. Merged, the shared topic tokens dominate and every subtopic produces nearly
the same ordering — the ARIMA slot and the LSTM slot would pick the same video.

| Signal | Weight |
|---|---|
| Title × subtopic | 3.2 |
| Title × topic | 0.8 |
| Description × subtopic | 1.6 |
| Description × topic | 0.4 |
| Channel authority | 0 … 2.2 |
| Duration fit | −3.0 … 2.0 |
| Language match | −0.2 … 1.5 |
| Freshness | −0.25 … 1.5 |
| Difficulty fit | −0.1 … 1.0 |
| Engagement | 0 … 1.0 |
| Live-stream penalty | −2.0 |
| Transcript bonus | −0.1 … 1.0 |

The topic weight is deliberately low: discovery already constrains the pool to the topic,
so weighting it heavily mostly rewards echoing the topic's phrasing — which structurally
penalised English candidates in Turkish runs, on top of the language score.

Four more things make the score discriminative:

- **Pool-based IDF.** The candidate pool is itself the corpus. `xgboost` appears in one
  title, `model` in a dozen, so the rare term carries the weight. Without it a subtopic
  could be won by a video matching only its generic words: *"Ağaç Tabanlı Modeller ve
  XGBoost"* went to an LSTM text-generation video on the strength of `tabanlı` and
  `modeller` alone, with `xgboost` unmatched.
- **Stem-aware matching.** Turkish is agglutinative, so `tahmin`/`tahmini` and
  `model`/`modelleri` must match; English `filter`/`filters` too. Matching is by common
  prefix, guarded so `veri` does not match `verimlilikten` and version numbers stay
  distinct (`python2` ≠ `python3`, `gpt4` ≠ `gpt5`).
- **Pedagogical and structural stopwords.** `temelleri`, `giriş`, `basics`, `explained`,
  `tabanlı`, `based` appear in subtopic titles but carry no domain meaning — and being
  rare, IDF would otherwise score them *high*. A cognitive-science lecture once won a
  time-series subtopic purely on the word "temelleri". Domain words that are merely common
  (`model`, `yöntem`) are deliberately left in; IDF handles those.
- **Real channel authority.** Subscriber count on a log scale, fetched with
  `channels.list` (1 quota unit per search, against 100 for the search itself). The
  previous keyword heuristic scored a channel named "Random Tutorial Guy" above
  MIT OpenCourseWare. Engagement is measured as **views per day**, so a two-week-old video
  is not punished against a five-year-old one.

**Assignment** maximises fit across the whole playlist rather than filling subtopics in
order — greedy assignment let an early subtopic take a video a later one needed far more.
A small `CHANNEL_REPEAT_PENALTY` breaks near-ties toward a different channel without
overriding a clearly better video.

A subtopic whose terms appear in no pool title has no signal to rank on; the assignment
fills it with whatever maximises the total. Those picks score low and the UI labels them a
**weak match** instead of presenting them as good ones.

### ASR is off by default

Downloading audio and running Whisper costs roughly **90–120 seconds per video** on CPU.
The first two transcript providers already cover the large majority of videos, and a
transcript moves the score by at most +1.0. Enable it from the sidebar toggle or with
`ENABLE_ASR_FALLBACK=true`; `MAX_ASR_VIDEOS_PER_RUN` caps the cost per run.

The downloader produces 16 kHz mono WAV — exactly what `whisper.cpp` requires, and one
less encode step than MP3. The Whisper model is cached process-wide (a cold load measured
5.5s; cached loads are instant).

---

## Resilience

**Provider health.** A single video failing — subtitles disabled, private, removed —
never penalises a provider. Only infrastructure-level failures count toward
`PROVIDER_FAILURE_THRESHOLD` consecutive errors, after which that provider is skipped for
`PROVIDER_COOLDOWN_SEC`. A success resets the counter.

**Rate limits are modelled separately.** YouTube throttles unauthenticated transcript
requests per IP, and retrying deepens the block. `HTTP 429`, `RequestBlocked` and
`IpBlocked` therefore have their own error class: they are **never retried**, they cool
the provider down **immediately** without waiting for the failure threshold, and the
cooldown uses `RATE_LIMIT_COOLDOWN_SEC` or the server's `Retry-After` header. Previously a
single rate-limit event turned into roughly `workers × attempts × providers` ≈ 24 requests
against a server already asking for less traffic; it is now bounded by the worker count.

If you hit this often, authenticate with browser cookies via `YTDLP_COOKIES_FROM_BROWSER=chrome`.
The playlist still builds without transcripts — they are enrichment, and ranking falls back
to metadata.

**Model availability.** Google closes older Gemini models to new projects
(`gemini-2.5-flash` returns *"no longer available to new users"*). The provider walks a
chain — configured model → `gemini-3.7-flash` → `gemini-flash-latest` → `gemini-2.5-flash` —
and retries without `thinking_config` for models that reject it.

**Secrets** are redacted from logs and user-facing warnings: the YouTube API key travels as
a query parameter, so exception text can contain it.

---

## Configuration

Every setting below is read from `.env`. Unrecognised keys in `.env` produce a visible
warning rather than being silently ignored.

### Required

| Variable | Default |
|---|---|
| `GEMINI_API_KEY` | — |

### Models and discovery

| Variable | Default | Notes |
|---|---|---|
| `GEMINI_MODEL` | `gemini-3.7-flash` | falls back through a chain if unavailable |
| `GEMINI_THINKING_BUDGET` | `-1` | `-1` = don't send the parameter; some models reject it |
| `YOUTUBE_DATA_API_KEY` | — | without it, `yt-dlp` handles discovery |
| `MAX_SUBTOPICS` | `6` | hard cap; each subtopic costs a search |
| `SEARCH_CANDIDATES_PER_SUBTOPIC` | `12` | 10–50 |
| `METADATA_TOP_K` | `4` | shortlist size per subtopic |
| `INCLUDE_ENGLISH_BY_DEFAULT` | `true` | default state of the bilingual toggle |
| `CHANNEL_REPEAT_PENALTY` | `0.6` | diversity nudge, breaks near-ties only |

### Transcripts and ASR

| Variable | Default |
|---|---|
| `TRANSCRIPT_ENRICHMENT_TOP_K` | `2` |
| `ENABLE_ASR_FALLBACK` | `false` |
| `MAX_ASR_VIDEOS_PER_RUN` | `2` |
| `ASR_BACKEND` | `auto` — `auto` \| `faster-whisper` \| `whisper.cpp` |
| `FASTER_WHISPER_MODEL_SIZE` | `base` |
| `FASTER_WHISPER_DEVICE` | `cpu` |
| `FASTER_WHISPER_COMPUTE_TYPE` | `int8` |
| `FASTER_WHISPER_BEAM_SIZE` | `1` |
| `WHISPER_CPP_CLI_PATH` | — |
| `WHISPER_CPP_MODEL_PATH` | — |
| `WHISPER_CPP_TIMEOUT_SEC` | `1800` |
| `FFMPEG_PATH` | — |

### yt-dlp

| Variable | Default | Notes |
|---|---|---|
| `YTDLP_JS_RUNTIME` | — | when empty, `node`/`deno`/`bun`/`quickjs` are auto-detected on `PATH` |
| `YTDLP_COOKIES_FROM_BROWSER` | — | e.g. `chrome`, `firefox:default` — loosens YouTube rate limits |
| `YTDLP_COOKIES_FILE` | — | Netscape-format cookie file, alternative to the above |
| `YTDLP_PROXY` | — | |

### Playlist publishing (OAuth only)

| Variable | Default | Notes |
|---|---|---|
| `YOUTUBE_OAUTH_CLIENT_SECRET_FILE` | — | |
| `YOUTUBE_OAUTH_CLIENT_ID` | — | |
| `YOUTUBE_OAUTH_CLIENT_SECRET` | — | |
| `YOUTUBE_OAUTH_TOKEN_FILE` | `data/cache/youtube_oauth_token.json` | legacy file store; the API uses the `oauth_token` table |
| `YOUTUBE_OAUTH_ALLOW_LOCAL_SERVER` | `true` | legacy desktop flow only; the API uses a redirect flow |
| `YOUTUBE_PLAYLIST_PRIVACY_STATUS` | `private` | `private` \| `unlisted` \| `public` |

The API uses a proper OAuth redirect flow instead of opening a browser on the server, and
stores tokens per user in the `oauth_token` table rather than a file. For that path the
Google Cloud client must be of type **Web application**, with
`http://localhost:8000/api/auth/youtube/callback` registered as an authorized redirect URI.

> Tokens are encrypted at rest when `SECRET_ENCRYPTION_KEY` is set (generate one with
> `python -m src.storage.crypto`). Without a key they are stored in plaintext. Adding a key
> later is safe — existing plaintext rows keep working and are encrypted on next write.

### Concurrency, retries, caching

| Variable | Default |
|---|---|
| `MAX_SEARCH_WORKERS` | `4` |
| `MAX_TRANSCRIPT_WORKERS` | `4` |
| `REQUEST_TIMEOUT_SEC` | `30` |
| `RETRY_MAX_ATTEMPTS` | `3` |
| `RETRY_BASE_DELAY_SEC` | `1.0` |
| `SEARCH_CACHE_TTL_SEC` | `21600` (6 h) |
| `TRANSCRIPT_CACHE_TTL_SEC` | `2592000` (30 d) |
| `FAILURE_CACHE_TTL_SEC` | `900` |
| `PROVIDER_COOLDOWN_SEC` | `900` |
| `RATE_LIMIT_COOLDOWN_SEC` | `1800` |
| `PROVIDER_FAILURE_THRESHOLD` | `3` |
| `DATA_DIR` | `data` |
| `SQLITE_PATH` | `data/cache/app.db` |
| `SECRET_ENCRYPTION_KEY` | — encrypts stored OAuth tokens; generate with `python -m src.storage.crypto` |
| `ALLOW_UNSAFE_OPENMP_WORKAROUND` | `true` (Windows/Conda OpenMP clash) |

---

## Output

Per run, under `data/runs/<run_id>/`:

- `result.json` — the full result: subtopics, shortlists, per-signal scores, warnings
- `study_plan.md` — a readable study plan
- `run_<timestamp>.log` — the run log, with secrets redacted

SQLite state lives at `data/cache/app.db`:

| Table | Purpose |
|---|---|
| `search_cache` | search results per provider/query/filters |
| `transcript_cache` | transcripts and per-video failures |
| `provider_health` | cooldowns and consecutive-failure counters |
| `run`, `run_subtopic`, `run_video` | run history, scoped by `user_id` |
| `oauth_token` | YouTube OAuth tokens, per user, encrypted when a key is set |

Expired rows are purged at the end of each run. The cache key carries a schema version, so
changing the candidate model invalidates stale entries automatically instead of serving
records that are missing new fields.

---

## Project layout

```
api/                        FastAPI layer — the application entry point
  main.py                   app, CORS, provider-error → HTTP mapping
  deps.py                   current user (stub), config composition, job runner
  schemas.py                request/response contracts
  sse.py                    Server-Sent Events for live progress
  routers/                  runs, config
web/                        React + Vite + TypeScript frontend
  src/api/                  typed client
  src/hooks/useRunStream.ts EventSource wrapper for live progress
  src/features/run/         form, progress, results
  src/features/history/     past runs
  src/styles.css            palette carried over from src/ui/theme.py
src/
  config/settings.py        env → validated AppConfig
  models/domain.py          pydantic domain models
  providers/                external systems, one module each
    errors.py               temporary / rate-limited / permanent / video-level
    llm_provider.py         Gemini, with a model fallback chain
    youtube_data_api_provider.py
    ytdlp_provider.py       search, subtitles, audio download
    youtube_transcript_api_provider.py
    faster_whisper_provider.py / whisper_cpp_provider.py
  services/                 orchestration and business logic
    playlist_service.py     the phased pipeline
    youtube_search_service.py
    metadata_ranker.py      scoring
    recommendation_service.py  global assignment
    topic_service.py / transcript_service.py / playlist_publish_service.py
  storage/sqlite_store.py   cache, provider health, run history, OAuth tokens
  jobs/                     JobRunner abstraction (in-process today)
  utils/                    text, retry, logging, yt-dlp options
tests/                      218 tests
```

## Tests

```bash
pytest -q
```

224 tests, no network access, under 10 seconds. Each significant bug fixed in this codebase
has a regression test named after the behaviour it locks in. The suite runs without a `.env`
and without any API key — CI has neither.

### CI

`.github/workflows/ci.yml` runs four jobs on push and pull request:

| Job | What it protects |
|---|---|
| `backend` | Python 3.13: import check, `pyflakes`, full test suite |
| `minimum-deps` | Installs the **lower bound** of every range in `requirements.txt` and runs the same checks |
| `frontend` | `web/`: TypeScript typecheck and production build |
| `secrets` | Scans tracked files *and history* for API-key patterns; fails if `.env` is tracked |

> Editing the workflow: expressions are only valid in fields that allow the context they use.
> `${{ env.* }}` works in a step's `with:` but **not** in `jobs.<id>.name` — and an illegal
> context there is rejected at validation time, so the run appears with *zero jobs* rather than
> a failing step. `yaml.safe_load` will not catch it; the syntax is fine, the context is not.

The import check runs before the tests on purpose: some breakage happens at import time, and
only a standalone import step reports it as itself rather than as 200 collection errors.

`minimum-deps` exists because development always runs at the *top* of a declared range while
`requirements.txt` promises the *bottom*. Two real bugs came from that gap: FastAPI 0.116
rejected `-> None` on a 204 route while the installed 0.118 did not, and
`google-auth-oauthlib` 1.2.0 silently skipped PKCE verifier generation while the installed
1.4.0 did it by default. Both were invisible until the lower bounds were actually installed.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `ValueError: embedded null character` | a UTF-16 line in `.env`, usually from PowerShell `>>`. Rewrite the file as UTF-8 |
| `429 Too Many Requests`, transcripts empty | YouTube IP rate limit. Wait it out, or set `YTDLP_COOKIES_FROM_BROWSER` |
| `no longer available to new users` | the configured Gemini model is closed to your project; the fallback chain handles it, or set `GEMINI_MODEL` |
| `Requested format is not available` | yt-dlp too old or no JS runtime — `pip install -U yt-dlp`, install `node` |
| Playlist has few recommendations | subtopics could not be matched. Narrow the topic, or enable English content |
| `RESOURCE_EXHAUSTED` from Gemini | the API key has no quota or credits left |

