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

Or with Docker (builds the frontend and serves everything from one container):

```bash
docker build -t make-a-playlist .
docker run -p 8000:8000 --env-file .env -v playlist-data:/app/data make-a-playlist
```

In development Vite proxies `/api` to port 8000, so start the API first. In production
the API serves the built frontend from `web/dist`, so a single process is enough.

> **Run exactly one process — never `--workers`, never multiple replicas.**
> Job state lives in memory (`InProcessJobRunner` holds the handles, SSE event
> channels and futures). A request that lands on a second process does not know
> the run: the progress stream breaks and `/status` returns "unknown run" while
> the job is in fact running fine. Only finished results survive, because those
> are in SQLite. The app logs this at startup and reports an error if it sees
> `WEB_CONCURRENCY > 1`. Scaling out needs a shared job backend (the
> `JobRunner` protocol exists for exactly that swap).

Two settings matter before exposing this to anyone else:

| Setting | Why |
|---|---|
| `SECRET_ENCRYPTION_KEY` | Without it OAuth tokens are stored **in plain text**. They carry permission to create playlists on the user's YouTube account. Generate with `python -m src.storage.crypto`. |
| `CORS_ALLOW_ORIGINS` | Leave empty when the UI is served from the same process (the default, and the recommended setup). Only set it if the frontend lives on a separate domain. `*` is ignored — this API carries a session cookie. |

> Use `python -m uvicorn`, not the bare `uvicorn` command: on Windows the `uvicorn.exe`
> on `PATH` may belong to a different Python installation than the one your dependencies
> are installed in.

---

## Requirements

| | |
|---|---|
| Python | **3.13** — the version CI tests and development targets. 3.11/3.12 should work but are not verified. |
| **An LLM key** | Gemini **or** Together.ai — `LLM_PROVIDER` selects which, and only that one's key is read |
| YouTube Data API key | optional — primary search path; without it, `yt-dlp` is used. **Its daily quota is the binding constraint for a shared deployment** — see below |
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
  `{title, query, query_en, terms}` per subtopic, so the extra queries — and the equivalent
  terms used later in ranking — cost no extra LLM calls.
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

**Popularity is gated on relevance.** Channel authority, freshness and engagement only count
in full once title-and-description relevance reaches a threshold; at zero relevance they
contribute nothing. Without the gate the off-topic signals summed higher (8.2) than the
relevance signals (4.8), so a popular video could take a slot it did not match. Measured
across 13 stored runs (42 picks): one viral video had won *three different subtopics* at
once; gating changed 3 picks, all of them toward a better lexical match and none away from
one, and cut zero-signal picks from 6/42 to 4/42. Duration, language and difficulty are left
ungated — those are user constraints, not popularity.

```bash
python scripts/measure_ranking.py
```

That script produced the numbers above and the threshold sweep recorded next to
`POPULARITY_GATE_FULL`. It reads `data/runs/`, so it measures re-ranking *within each run's
stored shortlist* — not whether a better video existed in the wider pool.

**Equivalent terms bridge the language gap.** Lexical matching could not connect a concept's
Turkish and English names — `Kokusuz` *is* `Unscented`, but no amount of stemming will say
so. In one stored run the subtopic "Kokusuz Kalman Filtresi" matched nothing and its slot
went to a generic Kalman video, while the pool's *"Unscented Kalman Filter Design"* was
handed to a different subtopic entirely. The LLM now returns a `terms` array per subtopic —
the English equivalent, the acronym, common synonyms — alongside the title and queries it
already produced, so this costs no extra call. Relevance takes the **best** match across the
title and those terms rather than the sum: naming the same concept twice should not outscore
naming it once. Replayed on that run, the pick changed to the correct video and its relevance
went from 1.39 to 3.31.

A pick can still be weak when the pool genuinely holds nothing on the subtopic. The UI marks
those a **weak match** — and marks them on *relevance*, not on the confidence score alone.
Confidence is derived from the total, which includes duration, language and channel signals,
so a long, recent, popular video with zero topical overlap used to score 8.6 and be presented
without any warning. Measured across the stored runs, 3 of 6 zero-relevance picks were
unmarked under the old rule.

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
a query parameter, so exception text can contain it. That includes the one path a caught
exception can still reach a client through — a failed job's `error` field, which feeds both
`GET /api/runs/{id}` and the SSE `done` event.

---

## Running it for more than one person

`AUTH_MODE` decides the shape of the deployment:

| Mode | Who the request belongs to |
|---|---|
| `single_user` (default) | everyone is `local`; no session cookie, no sign-in |
| `multi_user` | a Google sign-in is required; the session cookie identifies the user |

`multi_user` exists to know *who* is asking — for the daily limits and for personal YouTube
publishing consent. It has nothing to do with keys; those are shared in both modes.

### The quota arithmetic, which decides everything else

One run costs roughly **1,224 quota units** — measured, not estimated: 6 subtopics × 2
queries (bilingual discovery) × 102 units per search (`search.list` 100 + `videos.list` 1 +
`channels.list` 1). A Google Cloud project gets **10,000 units per day** by default.

> **≈ 8 runs per day, for all users combined.**

That number, not CPU or memory, is what limits how many people this can serve. Two ceilings
guard it, and they answer different questions:

| Setting | Question it answers |
|---|---|
| `MAX_RUNS_PER_USER_PER_DAY` | "has this person had their share?" |
| `MAX_UNITS_PER_DAY` | "does the service have capacity left at all?" |

Both are enforced inside the same transaction that writes the run row. Splitting the check
from the write lets concurrent requests pass the same stale count — measured, the per-user
limit admitted 11 runs against a limit of 3, and the service budget admitted 2.1× its cap.

The budget check also **reserves** the estimated cost at admission time rather than only
reading what has been spent. Actual spend lands minutes later, while the run is still
working; a check that reads only spend lets every in-flight run pass against the same
figure. The reservation is released when the run finishes or is found interrupted.

The estimate is deliberately worst-case. Cached searches can make a run cost nothing, but
that is unknowable in advance, and under-estimating means a run dies mid-way on a `403`
with the LLM call already paid for.

**Raising the quota** is the real fix for a public deployment: request an increase in Google
Cloud Console (YouTube Data API → Quotas). Set `YOUTUBE_DAILY_QUOTA_UNITS` to the new figure
afterwards — no code changes, and both ceilings scale with it.

### Watching it

`GET /api/admin/usage` reports the day's real consumption, the per-endpoint breakdown,
per-user totals, runs in the last 24 hours, active provider cooldowns, and the day's
rate-limit events. Access is governed by `ADMIN_USER_IDS`; in `single_user` mode it is
always open, and in `multi_user` mode an **empty list closes it to everyone** — the report
carries user identities, so the failure mode of forgetting to configure it should be a
locked door rather than an open one.

The quota day follows **Pacific time**, because that is when Google resets it. `Retry-After`
on a 429 points at that reset, which from most of the world lands in the middle of the day
rather than at midnight.

---

## Configuration

Every setting below is read from `.env`. Unrecognised keys in `.env` produce a visible
warning rather than being silently ignored.

### Required

One LLM key. `LLM_PROVIDER` decides which one is read; the other may stay empty.

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `gemini` | `gemini` or `together` |
| `GEMINI_API_KEY` | — | required when `LLM_PROVIDER=gemini` |
| `TOGETHER_API_KEY` | — | required when `LLM_PROVIDER=together` |
| `TOGETHER_MODEL` | `meta-llama/Llama-3.3-70B-Instruct-Turbo` | verify it is available on your account |

Together.ai is reached over its **OpenAI-compatible** endpoint, so no extra SDK is
installed — the existing `requests` dependency carries it. Both providers share one
prompt and one field contract; only the schema *dialect* differs (Gemini wants
uppercase types, OpenAI-compatible endpoints want standard JSON Schema).

Keys are **server-side and shared**. Users never enter an API key — `LLM_PROVIDER` and the
keys below apply to every request regardless of who made it. An earlier design had each user
bring their own key; that was reversed because asking someone to create a Google Cloud
project before their first playlist is a wall, not an onboarding step. The cost moved to
whoever runs the server, which is why the daily ceilings below exist.

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

### Study notes

Off by default. When enabled, each selected video that has a transcript gets a short study
note written from that transcript — one extra LLM call per selected video, which is why it
is opt-in rather than automatic. The run form exposes it as a checkbox; the request body
can override the server default per run.

| Variable | Default | Notes |
|---|---|---|
| `ENABLE_STUDY_NOTES` | `false` | one additional LLM call per selected video |
| `STUDY_NOTE_TRANSCRIPT_CHAR_LIMIT` | `24000` | transcript is truncated to this before prompting |

### Learning spaces (RAG)

Off by default. A **learning space** is a persistent pool: the videos from one or more
completed runs plus documents you upload (PDF, DOCX, TXT, MD). You ask it questions and
get an answer built **only** from those sources, with citations that link to the exact
second of a video or the page of a document.

The point of the feature is not the answering — it is the **refusing**. If the pool does
not cover the question, it says so instead of inventing something. Three independent
gates enforce that:

1. **Retrieval threshold** — if the best candidate has no lexical match *and* scores
   below `RAG_MIN_SIMILARITY`, the model is **never called**. Free and deterministic.
2. **Output schema** — the model must return `answered` plus the excerpt numbers it used.
3. **Citation check** — numbers it did not receive are stripped; if none survive, the
   answer is downgraded to "not found".

Retrieval is hybrid: SQLite FTS5 (lexical) and embeddings (semantic), merged with
reciprocal rank fusion. There is no vector database — a space holds a few thousand
chunks and cosine over a float32 BLOB is measured in milliseconds. If the embedding
provider is down, the lexical half keeps working; search gets weaker, not absent.

| Variable | Default | Notes |
|---|---|---|
| `ENABLE_RAG` | `false` | one embedding + one generation call per question |
| `EMBEDDING_PROVIDER` | follows `LLM_PROVIDER` | can differ from the generation provider |
| `GEMINI_EMBEDDING_MODEL` | `gemini-embedding-001` | `text-embedding-004` is **gone** (404) |
| `TOGETHER_EMBEDDING_MODEL` | `BAAI/bge-m3` | multilingual — decisive for Turkish |
| `EMBEDDING_BATCH_SIZE` | `64` | chunks per embedding request |
| `RAG_CHUNK_CHARS` / `RAG_CHUNK_OVERLAP_CHARS` | `1200` / `200` | overlap keeps a boundary-straddling answer whole |
| `RAG_TOP_K` | `8` | chunks placed in the prompt |
| `RAG_MAX_CHUNKS_PER_SOURCE` | `3` | stops one long video from filling the context |
| `RAG_MIN_SIMILARITY` | `0.70` | **model-specific**, see below |
| `RAG_CONTEXT_CHAR_LIMIT` | `12000` | |
| `MAX_SPACES_PER_USER` | `10` | storage and embedding cost sits with the server owner |
| `MAX_DOCUMENTS_PER_SPACE` | `25` | |
| `MAX_UPLOAD_BYTES` | `20971520` | 20 MB, enforced while reading, not from `Content-Length` |

**`RAG_MIN_SIMILARITY` is measured, not guessed.** With `gemini-embedding-001`, relevant
questions scored 0.804–0.852 against their chunk and irrelevant ones 0.496–0.542. The
value was originally planned at `0.55`, which sat 0.008 above the irrelevant ceiling —
the gate would effectively never have closed. Gemini embeddings keep even unrelated text
around ~0.5, so **re-measure if you change the embedding model**. Rejected queries log
their best score for exactly this purpose. Details in
[`docs/rag-plan.md`](docs/rag-plan.md).

A source that yields no searchable text — a video with no transcript, a scanned PDF — is
recorded as `no_text`, not `failed`, and shown as "kapsam dışı" in the UI. That
distinction matters: you need to know what *cannot* be searched, otherwise a later "not
found" reads like a bug. There is no OCR; the UI says so plainly.

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

### Multi-user, limits, and quota

| Variable | Default | Notes |
|---|---|---|
| `AUTH_MODE` | `single_user` | `single_user` \| `multi_user` (Google sign-in) |
| `SESSION_TTL_SEC` | `1209600` (14 d) | session cookie lifetime |
| `MAX_RUNS_PER_USER_PER_DAY` | `0` | per user; `0` = unlimited. **`3` is recommended for `multi_user`** |
| `MAX_UNITS_PER_DAY` | — | service-wide quota ceiling; empty = the project quota below |
| `YOUTUBE_DAILY_QUOTA_UNITS` | `10000` | tell it here if you had the quota raised |
| `ADMIN_USER_IDS` | — | comma-separated ids that may read `/api/admin/usage`. Empty in `multi_user` = nobody |

Leaving `MAX_RUNS_PER_USER_PER_DAY` at `0` in `multi_user` mode lets one signed-in user
drain the shared quota for everyone; the server logs a warning about that combination at
startup rather than failing, since a closed deployment may want it.

To find your own id for `ADMIN_USER_IDS`, sign in and call `GET /api/auth/me`.

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
| `provider_cooldown` | cooldowns and consecutive-failure counters, **server-wide** |
| `run`, `run_subtopic`, `run_video` | run history, scoped by `user_id` |
| `session` | sign-in sessions; the token is stored as a SHA-256 digest, never in the clear |
| `oauth_token` | YouTube OAuth tokens, per user, encrypted when a key is set |
| `api_usage` | quota units spent per day, per user, per endpoint |
| `provider_event` | per-day counts of rate limits, failures and cooldowns |

Cooldowns are **not** scoped per user, despite the run history being. Every limit they
guard against is shared: `yt-dlp` and the transcript API are throttled by the server's IP,
and the Data API key is one key for everyone. Scoped per user, the second person was simply
unprotected — they walked into the same limit from the same address and typically extended
it. A success clears the failure streak but does not cancel an unexpired rate-limit
cooldown; a lucky request does not override a server that asked for a pause.

Expired rows are purged at the end of each run — caches, finished sessions, and daily
counters older than 90 days. The cache key carries a schema version, so changing the
candidate model invalidates stale entries automatically instead of serving records that are
missing new fields.

**Deleting a run deletes its files too.** A run lives in two places, and for a long time
only the database half was removed: the study plan and result JSON stayed on disk after the
user pressed delete. Directories left behind by crashes are collected at startup.

---

## Project layout

```
api/                        FastAPI layer — the application entry point
  main.py                   app, CORS, provider-error → HTTP mapping, startup housekeeping
  deps.py                   current user (session or single-user), admin guard, config composition
  schemas.py                request/response contracts
  sse.py                    Server-Sent Events for live progress
  routers/                  runs, config, auth, admin, spaces
web/                        React + Vite + TypeScript frontend
  src/api/                  typed client
  src/hooks/useRunStream.ts EventSource wrapper for live progress
  src/hooks/useJobStream.ts  generic job progress (learning-space ingest)
  src/features/run/         form, progress, results
  src/features/history/     past runs
  src/features/space/       learning spaces: sources, ingest, grounded Q&A
  src/styles.css            palette carried over from src/ui/theme.py
src/
  config/settings.py        env → validated AppConfig
  models/domain.py          pydantic domain models
  providers/                external systems, one module each
    errors.py               temporary / rate-limited / permanent / video-level
    llm_provider.py         Gemini and Together.ai, with a model fallback chain
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
    rag_service.py          ingest + retrieval + the three abstention gates
    chunking.py             transcript/document → overlapping, timestamped chunks
    embedding_service.py    float32 BLOB vectors, cosine search (no vector DB)
    document_parser.py      PDF / DOCX / TXT / MD → (page, text)
    run_retention.py        deleting a run from the database AND from disk
    space_retention.py      deleting a learning space from the database AND from disk
  storage/sqlite_store.py   cache, provider cooldowns, run history, OAuth tokens, quota counters
  storage/crypto.py         at-rest encryption for stored OAuth tokens
  jobs/                     JobRunner abstraction (in-process today)
  utils/                    text, retry, logging, yt-dlp options
tests/                      471 tests
```

## Tests

```bash
pytest -q
```

359 backend tests, no network access, around ten seconds. Each significant bug fixed in this codebase
has a regression test named after the behaviour it locks in. The suite runs without a `.env`
and without any API key — CI has neither.

```bash
cd web && npm test
```

47 frontend tests. Most cover `useRunStream` — the SSE hook, which holds the most intricate
logic on that side. They run against a fake `EventSource`, which is what makes them
deterministic: the test decides when `progress`, `done`, or a bodiless `error` arrives, so
nothing waits on a timer. The fake mirrors the browser contract in two details that matter —
a closed source delivers nothing, and a dropped connection is a bodiless `Event` rather than
a `MessageEvent` — because the hook distinguishes exactly those cases, and a sloppy fake
would verify behaviour that cannot occur.

### CI

`.github/workflows/ci.yml` runs four jobs on push and pull request:

| Job | What it protects |
|---|---|
| `backend` | Python 3.13: import check, `pyflakes`, full test suite |
| `minimum-deps` | Installs the **lower bound** of every range in `requirements.txt` and runs the same checks |
| `frontend` | `web/`: regenerates types from the backend, typecheck, tests, production build |
| `secrets` | Scans tracked files *and history* for API-key patterns; fails if `.env` is tracked |

### The API contract

`web/src/api/types.ts` is hand-written and readable; `web/src/api/schema.d.ts` is generated
from the backend's OpenAPI document and committed. `web/src/api/contract.ts` asserts one
against the other at compile time, so renaming a backend field breaks `npm run typecheck`.

```bash
cd web && npm run gen:types
```

The check is **directional**, because the two directions are not the same claim. For request
bodies the UI may be *narrower* than the API — it offers `"en" | "tr"` while the API accepts
any language string — so the assertion only requires that what the UI sends is acceptable.
For response bodies narrowing is unsafe: anything the API can return must fit the UI's type.
A plain equality check would fail the first case for no reason and is why this is not one.

Two wrinkles are worth knowing before editing the generator:

- **SSE bodies are not in OpenAPI.** FastAPI derives schemas from route responses, and the
  events endpoint returns a `StreamingResponse`, so the `progress` and `done` payloads are
  invisible to it — which is exactly where drift had grown. `scripts/dump_openapi.py` adds
  them explicitly.
- **Response fields with defaults are marked required.** Pydantic treats a field with a
  default as not-required, which is right for requests and misleading for responses: FastAPI
  serializes defaults, so the key is always on the wire. The script marks them required for
  schemas not reachable from a `requestBody`. Without this the generated types are
  pessimistic in a way that buries real drift in noise.
- **CI regenerates the types rather than byte-comparing them.** The generated schema depends
  on the installed pydantic version — 2.11 emits `additionalProperties: true` for free-form
  dicts and 2.8 does not, which turns `Record<string, unknown>` into `Record<string, never>`
  downstream. Since the project deliberately supports a version *range*, a byte-exact check
  can never be stable; the first CI run failed on exactly that. The assertions are semantic
  and were verified to hold against schemas generated at both ends of the range. The
  committed `schema.d.ts` is a local convenience — typecheck without installing Python — and
  CI warns, rather than fails, when it has fallen behind.

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

