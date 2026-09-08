# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Turkish-speaking self-directed learners — students and people teaching themselves a
technical subject from YouTube. Confirmed: this is a **public product**, so strangers
sign up and meet it cold. Desktop and phone are **equally** first-class; neither is a
degraded fallback.

The situation is study, not browsing: someone has decided to learn a topic and wants a
path through it, then wants to interrogate what they collected without rewatching hours
of video to find one sentence.

## Product Purpose

Two joined jobs:

1. **Build the path.** One learning goal in; an ordered playlist out. The topic is split
   into distinct subtopics and each subtopic gets exactly one video, ranked on metadata
   and optionally on transcript content. The result exports and can be published as a
   real YouTube playlist.
2. **Question what you collected.** Those videos plus uploaded documents (PDF, DOCX, TXT,
   MD) form a **notebook** (`defter`). Questions are answered only from that notebook,
   with citations that link to the exact second of a video or page of a document.

Success is a learner who trusts the answer enough not to go re-check the video — and who
is told plainly when the answer is not there.

## Positioning

**The refusal is the product.** Any model can produce a confident paragraph; the
mechanism here is four independent gates that make an unsupported answer structurally
hard to emit — a deterministic retrieval threshold that never calls the model, a forced
output schema, a citation cross-check against what was actually supplied, and an
answer↔citation grounding check. The thresholds are measured against real notebooks, not
guessed, and the measurement tooling ships with the product.

A neighbouring product can copy "chat with your sources". It cannot truthfully copy "and
it tells you when it can't" without building the same gates.

## Operating Context

- Sources are **mostly English** (YouTube tutorials) while the interface and questions
  are **Turkish**. This cross-lingual split is normal, not an edge case, and it shapes
  retrieval, grounding checks and answer display.
- Transcripts come from YouTube where available, with Whisper ASR as an opt-in fallback;
  some sources legitimately yield no searchable text at all.
- Long operations (playlist runs, ingest, transcription) are asynchronous with live
  progress streamed over SSE. Waiting is a normal, visible part of using this.
- Free-tier API quotas bind hard: YouTube Data API allows roughly 8–16 playlist runs per
  day for *all* users, and the Gemini free tier caps generation requests per day.
  Hitting a limit is an expected state the interface must explain, not an error.

## Capabilities and Constraints

- Topic → subtopics → ranked videos → playlist; export as JSON/Markdown; publish to
  YouTube via OAuth.
- Notebooks: add videos from a run, upload documents, ask questions, get cited answers,
  read transcripts, mark moments.
- Notebooks are **isolated** from one another — a question is only ever answered from the
  notebook it was asked in.
- Notebook-level questions ("what's in this notebook", "summarise") are supported and
  take a different retrieval path from fact questions.
- Per-user limits: 10 notebooks, 25 documents per notebook, 20 MB per upload.
- A source with no extractable text is recorded as `no_text` (out of scope), never
  `failed` — the user must know what *cannot* be searched. There is no OCR.
- Turkish interface. Domain vocabulary: **defter** (notebook), **çalışma odası** (study
  room), **kaynak** (source), **alıntı** (citation).

## Brand Commitments

Name: **Make A Playlist**. Interface language is Turkish throughout.

Voice: plain and honest about limits. The product says "bulamadım" without apology or
euphemism, and distinguishes "your question is outside these sources" from "I could not
verify my own answer". No invented confidence.

## Evidence on Hand

Real: working playlist generation, three populated live notebooks (React, LLMs,
Economics), measured retrieval thresholds, citation-linked answers.

Absent — must not be fabricated: user counts, testimonials, reviews, press, pricing,
uptime claims, company or team identity. There are none.

## Product Principles

1. **Refusing well beats answering often.** An honest "not found" is a correct outcome,
   never an error state, and must never be styled as a failure.
2. **Show what cannot be searched.** Coverage gaps are surfaced, not hidden, or a later
   refusal reads as a bug.
3. **Every claim is traceable.** Answers carry citations that land on the exact second or
   page; a claim without a source does not ship.
4. **Waiting is designed, not endured.** Long asynchronous work is normal here; progress
   is legible and interruptible.
5. **Measure before tuning.** Thresholds and limits come from measurement against real
   data, and the tools to re-measure ship with the product.

## Accessibility & Inclusion

No formal standard has been set by the user. Desktop and mobile are equal-priority, so
touch targets and reflow at small sizes are product requirements rather than
nice-to-haves.
