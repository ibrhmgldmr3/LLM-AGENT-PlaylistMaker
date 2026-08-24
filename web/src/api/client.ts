import type {
  Capabilities,
  CreateRunRequest,
  IngestAccepted,
  RagAnswer,
  RunAccepted,
  RunListResponse,
  RunResultResponse,
  SpaceDetail,
  SpaceListResponse,
  SpaceSummary,
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

/**
 * Oturum dustugunde cagrilir.
 *
 * Merkezi olmasinin sebebi: oturum durumu uygulama ACILIRKEN bir kez
 * ogreniliyordu. Cerez sonradan duserse (14 gunluk omur dolar, kullanici
 * temizler, sunucu oturumu siler) arayuz uygulamayi gostermeye devam ediyor
 * ama her istek 401 aliyordu -- giris ekrani gelmiyor, kullanicinin sayfayi
 * elle yenilemesi gerekiyordu.
 *
 * Her cagriya tek tek eklenmedi: 401 herhangi bir uctan gelebilir ve her
 * cagiranin bunu hatirlamasi gerekmemeli.
 */
let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

interface RequestInitWithBody extends RequestInit {
  /**
   * `multipart/form-data` govdeler icin.
   *
   * Varsayilan `application/json` basligi FormData govdede YANLIS: tarayici
   * `boundary` parametresini kendisi uretiyor ve basligi elle koymak onu ezip
   * sunucunun govdeyi ayristirmasini imkansiz kiliyor.
   */
  omitContentType?: boolean;
}

async function request<T>(path: string, init?: RequestInitWithBody): Promise<T> {
  const { omitContentType, ...rest } = init ?? {};
  const response = await fetch(path, {
    ...rest,
    headers: omitContentType
      ? { ...rest.headers }
      : { "Content-Type": "application/json", ...rest.headers },
  });

  if (!response.ok) {
    // `/api/auth/me` ve `/api/config` oturumsuzken de 200 donuyor, dolayisiyla
    // buraya dusen bir 401 gercekten "oturum gitti" demek.
    if (response.status === 401) onUnauthorized?.();
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

  // ------------------------------------------------------- ogrenme alani

  listSpaces: (limit = 50, offset = 0) =>
    request<SpaceListResponse>(`/api/spaces?limit=${limit}&offset=${offset}`),

  createSpace: (name: string) =>
    request<SpaceSummary>("/api/spaces", { method: "POST", body: JSON.stringify({ name }) }),

  getSpace: (spaceId: string) => request<SpaceDetail>(`/api/spaces/${spaceId}`),

  deleteSpace: (spaceId: string) =>
    request<void>(`/api/spaces/${spaceId}`, { method: "DELETE" }),

  addRunToSpace: (spaceId: string, runId: string) =>
    request<IngestAccepted>(`/api/spaces/${spaceId}/sources/run`, {
      method: "POST",
      body: JSON.stringify({ run_id: runId }),
    }),

  /**
   * Dokuman yukler.
   *
   * `Content-Type` BILEREK silinmis: `request` varsayilan olarak
   * `application/json` koyuyor ve multipart govdede bu baslik, tarayicinin
   * uretmesi gereken `boundary` degerini EZER -- sunucu govdeyi ayristiramaz.
   */
  uploadDocument: (spaceId: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    body.append("title", file.name);
    return request<IngestAccepted>(`/api/spaces/${spaceId}/sources/document`, {
      method: "POST",
      body,
      headers: {},
      omitContentType: true,
    });
  },

  deleteSource: (spaceId: string, sourceId: string) =>
    request<void>(`/api/spaces/${spaceId}/sources/${encodeURIComponent(sourceId)}`, {
      method: "DELETE",
    }),

  ask: (spaceId: string, question: string, language = "Türkçe") =>
    request<RagAnswer>(`/api/spaces/${spaceId}/ask`, {
      method: "POST",
      body: JSON.stringify({ question, language }),
    }),
};
