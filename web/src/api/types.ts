// API sozlesmesi. `npm run gen:types` ile OpenAPI'den uretilen `schema.d.ts`
// bunlarin yerini alabilir; elle tutmak simdilik daha okunakli.

export type Language = "en" | "tr";
export type Difficulty = "mixed" | "beginner" | "intermediate" | "advanced";
export type Freshness = "balanced" | "evergreen" | "recent";
export type RunState = "pending" | "running" | "done" | "failed" | "cancelled";

export interface FilterOptions {
  language: Language;
  difficulty: Difficulty;
  max_duration_minutes: number;
  freshness_preference: Freshness;
  include_english: boolean;
}

export interface RunOptionsOverride {
  max_subtopics?: number;
  enable_asr_fallback?: boolean;
}

export interface CreateRunRequest {
  topic: string;
  filters: FilterOptions;
  options?: RunOptionsOverride;
  create_youtube_playlist?: boolean;
}

export interface RunAccepted {
  run_id: string;
  state: RunState;
  events_url: string;
  result_url: string;
}

/** SSE `progress` olayinin govdesi. */
export interface ProgressEvent {
  stage: string;
  message: string;
  progress: number;
  current: number | null;
  total: number | null;
}

/** SSE `done` olayinin govdesi. */
export interface RunSnapshot {
  run_id: string;
  user_id: string;
  state: RunState;
  created_at: string;
  progress: number;
  stage: string | null;
  message: string | null;
  error: string | null;
}

export interface MetadataScore {
  total: number;
  title_relevance: number;
  description_relevance: number;
  channel_quality: number;
  duration_fit: number;
  difficulty_fit: number;
  language_match: number;
  freshness: number;
  engagement: number;
  rationale: string[];
}

export interface VideoCandidate {
  video_id: string;
  url: string;
  title: string;
  description: string;
  channel: string | null;
  subscriber_count: number | null;
  duration_sec: number | null;
  view_count: number | null;
  publish_date: string | null;
  language: string | null;
  is_live: boolean;
  metadata_score: number;
  discovery_provider: string | null;
}

export interface Recommendation {
  position: number;
  subtopic: string;
  video: VideoCandidate;
  why_selected: string;
  confidence_score: number;
  transcript_status: string;
  transcript_source: string | null;
  metadata_score: MetadataScore;
}

export interface SubtopicResult {
  subtopic: { title: string; normalized_title: string; search_query: string | null };
  query: string;
  candidates_considered: number;
  shortlisted_candidates: VideoCandidate[];
  selected_video_id: string | null;
  transcript_status: string;
  notes: string[];
}

export interface PlaylistResult {
  run_id: string;
  topic: string;
  filters: FilterOptions;
  subtopics: SubtopicResult[];
  recommendations: Recommendation[];
  created_at: string;
  warnings: string[];
  exports: { json_path: string; markdown_path: string } | null;
  published_playlist_url: string | null;
}

export interface RunResultResponse {
  run_id: string;
  state: RunState;
  result: PlaylistResult | null;
}

export interface RunSummary {
  run_id: string;
  topic: string;
  created_at: string;
  is_complete: boolean;
  filters: Record<string, unknown>;
}

export interface RunListResponse {
  items: RunSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface Capabilities {
  gemini_configured: boolean;
  youtube_search_configured: boolean;
  youtube_publish_configured: boolean;
  asr_available: boolean;
  cookies_configured: boolean;
  defaults: Record<string, unknown>;
}
