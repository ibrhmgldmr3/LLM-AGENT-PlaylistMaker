import type {
  Capabilities,
  CreateRunRequest,
  RunAccepted,
  RunListResponse,
  RunResultResponse,
} from "./types";

/** Sunucunun dondurdugu hata govdesini tasiyan istisna. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly retryAfter?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });

  if (!response.ok) {
    // API hatalari `{ detail, code }` seklinde donuyor.
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* govde JSON degilse durum metnini kullan */
    }
    const retryAfter = response.headers.get("Retry-After");
    throw new ApiError(detail, response.status, retryAfter ? Number(retryAfter) : undefined);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export interface Session {
  signed_in: boolean;
  user_id: string | null;
  email: string | null;
  /** Kurulum cok kullanicili mi. `false` ise giris ekrani hic gosterilmez. */
  auth_required: boolean;
}

export interface CredentialItem {
  name: string;
  label: string;
  required: boolean;
  /** Yalnizca "girilmis mi". DEGER hicbir zaman donmuyor. */
  configured: boolean;
}

export interface CredentialsResponse {
  editable: boolean;
  items: CredentialItem[];
}

export interface YoutubeAuthStatus {
  connected: boolean;
  configured: boolean;
}

export interface PublishResponse {
  url: string;
  added: number;
  warnings: string[];
}

export const api = {
  capabilities: () => request<Capabilities>("/api/config"),

  me: () => request<Session>("/api/auth/me"),

  logout: () => request<void>("/api/auth/logout", { method: "POST" }),

  credentials: () => request<CredentialsResponse>("/api/credentials"),

  saveCredential: (name: string, value: string) =>
    request<void>(`/api/credentials/${name}`, { method: "PUT", body: JSON.stringify({ value }) }),

  deleteCredential: (name: string) =>
    request<void>(`/api/credentials/${name}`, { method: "DELETE" }),

  youtubeStatus: () => request<YoutubeAuthStatus>("/api/auth/youtube/status"),

  youtubeAuthUrl: () =>
    request<{ authorization_url: string; state: string }>("/api/auth/youtube/start"),

  youtubeDisconnect: () => request<void>("/api/auth/youtube", { method: "DELETE" }),

  publish: (runId: string) =>
    request<PublishResponse>(`/api/runs/${runId}/publish`, { method: "POST" }),

  createRun: (payload: CreateRunRequest) =>
    request<RunAccepted>("/api/runs", { method: "POST", body: JSON.stringify(payload) }),

  getRun: (runId: string) => request<RunResultResponse>(`/api/runs/${runId}`),

  listRuns: (limit = 20, offset = 0) =>
    request<RunListResponse>(`/api/runs?limit=${limit}&offset=${offset}`),

  // `cancelRun` ve `deleteRun` AYNI uca gidiyor: sunucu calisan bir isi iptal
  // ediyor, bitmis olani gecmisten siliyor. Tek isim kullanmak cagri yerinde
  // niyeti gizliyordu -- ilerleme ekraninda "sil", gecmis listesinde "iptal et"
  // gibi okunuyordu. Ayni uc, iki niyet, iki ad.
  cancelRun: (runId: string) => request<void>(`/api/runs/${runId}`, { method: "DELETE" }),

  deleteRun: (runId: string) => request<void>(`/api/runs/${runId}`, { method: "DELETE" }),

  exportUrl: (runId: string, artifact: "json" | "markdown") =>
    `/api/runs/${runId}/export/${artifact}`,
};
