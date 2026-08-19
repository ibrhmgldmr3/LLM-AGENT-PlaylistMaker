// API sozlesmesi. `npm run gen:types` ile OpenAPI'den uretilen `schema.d.ts`
// bunlarin yerini alabilir; elle tutmak simdilik daha okunakli.

export type Language = "en" | "tr";
export type Difficulty = "mixed" | "beginner" | "intermediate" | "advanced";
export type Freshness = "balanced" | "evergreen" | "recent";
/**
 * `interrupted` is yurutucusunde bir durum DEGIL, bir cikarim: sunucu yeniden
 * baslatildiginda bellekteki isler oluyor ama calistirma kaydi kaliyor. Onu
 * bitirecek hicbir sey kalmadigi icin "beklemede" gostermek yalan olurdu.
 */
export type RunState =
  | "pending"
  | "running"
  | "done"
  | "failed"
  | "cancelled"
  | "interrupted";

export interface FilterOptions {
  language: Language;
  difficulty: Difficulty;
  max_duration_minutes: number;
  freshness_preference: Freshness;
  include_english: boolean;
}

/**
 * Yanitta geri yansiyan filtreler.
 *
 * `language` burada BILEREK dar degil: istek tarafindaki `Language` arayuzun
 * SUNDUGU secenekleri anlatiyor, API ise alani `str` olarak kabul ediyor ve
 * ne gonderildiyse sonucta aynen geri veriyor. Baska bir istemci "de" gonderip
 * o calistirma gecmisten okundugunda buraya "de" gelir. Ikisini ayni tiple
 * anlatmak, TypeScript'in imkansiz dedigi bir degerin calisma zamaninda
 * gelmesi demekti.
 */
export interface FilterOptionsEcho extends Omit<FilterOptions, "language"> {
  language: string;
}

export interface RunOptionsOverride {
  max_subtopics?: number;
  enable_asr_fallback?: boolean;
  enable_study_notes?: boolean;
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

/**
 * SSE `done` olayinin govdesi.
 *
 * `user_id` BURADA YOKTU degil, FAZLAYDI: eskiden bu tip is katmaninin
 * `snapshot()` sozlugunu tarif ettigi varsayiliyordu ama sunucu artik
 * `RunSnapshotBody` modelinden serilestiriyor ve `user_id` yollamiyor
 * (istemcinin isine yaramiyor). Ayni duzeltmede `job_id` -> `run_id`
 * adlandirmasi API'nin geri kalaniyla hizalandi; onceden bu alan calisma
 * zamaninda `undefined` gelirken TypeScript `string` oldugunu iddia ediyordu.
 */
export interface RunSnapshot {
  run_id: string;
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

export type StudyNoteStatus = "available" | "no_transcript" | "failed";

/**
 * Bir alt konu icin transkriptten uretilen calisma notu.
 *
 * "no_transcript" AYRI bir durum: transkripti olmayan bir video icin not
 * UYDURULMUYOR (bkz. backend `StudyNote` modelinin docstring'i). Arayuz bu
 * durumu ayirt etmek ZORUNDA -- `content: null` ile "bos not" gostermek,
 * "bu video icin bilgi yok" ile "uretim basarisiz oldu" arasindaki farki
 * kaybederdi.
 */
export interface StudyNote {
  subtopic: string;
  video_id: string;
  status: StudyNoteStatus;
  content: string | null;
  error: string | null;
}

export interface PlaylistResult {
  run_id: string;
  topic: string;
  filters: FilterOptionsEcho;
  subtopics: SubtopicResult[];
  recommendations: Recommendation[];
  created_at: string;
  warnings: string[];
  exports: { json_path: string; markdown_path: string } | null;
  published_playlist_url: string | null;
  study_notes: StudyNote[];
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
  // `gemini_configured` GERIYE DONUK UYUM icin duruyor. Saglayici secilebilir
  // hale geldigi icin asil soru "LLM yapilandirilmis mi" ve onu bu alan
  // yanitliyor (bkz. backend `AppConfig.public_capabilities()`).
  llm_configured: boolean;
  youtube_search_configured: boolean;
  youtube_publish_configured: boolean;
  asr_available: boolean;
  cookies_configured: boolean;
  defaults: Record<string, unknown>;
}
