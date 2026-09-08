/**
 * Elle yazilan `types.ts` ile OpenAPI'den uretilen `schema.d.ts` arasindaki
 * kaymayi DERLEME hatasina cevirir. Calisma zamaninda hicbir sey uretmez;
 * yalnizca tip duzeyinde iddialardir.
 *
 * `types.ts` neden hala elle yaziliyor: uretilen `schema.d.ts` 986 satir ve
 * `components["schemas"]["..."]` diye okunuyor. Elle yazilan surum okunakli ve
 * arayuzun ihtiyaci olan adlandirmayi kullaniyor. Buradaki iddialar sayesinde
 * okunakliligi korurken kaymayi da kaybetmiyoruz.
 *
 * Kontrol YON DUYARLI, cunku iki yon ayni sey degil:
 *
 * - `Sends`  : istek govdeleri. Arayuzun DAHA DAR olmasi mesru -- UI yalnizca
 *              "en"/"tr" sunuyor ama API her dil kodunu kabul ediyor. Sart:
 *              arayuzun urettigi her sey API'nin kabul ettigi kumeye dusmeli.
 * - `Receives`: yanit govdeleri. Burada daraltma TEHLIKELI -- API'nin
 *              donebildigi bir seyi arayuz tipi disliyorsa, o deger calisma
 *              zamaninda gelir ve TypeScript gelmeyecegini iddia etmis olur.
 *              Sart: API'nin donebildigi her sey arayuz tipine sigmali.
 *
 * Kor bir esitlik kontrolu ilk yonu yanlis yere kirmizi yakardi.
 *
 * BU DOSYAYI HICBIR SEY IMPORT ETMEZ -- ve etmemeli. Iddialar `tsc`in dosyayi
 * derlemesiyle kontrol ediliyor; calisma zamaninda uretilecek bir sey yok, o
 * yuzden pakete de girmiyor. "Kimse kullanmiyor" diye silmeyin: silindiginde
 * hicbir test kirilmaz, yalnizca kayma bekcisi sessizce ortadan kalkar.
 */

import type { components } from "./schema";
import type {
  AskRequest,
  Capabilities,
  CreateRunRequest,
  FilterOptions,
  MetadataScore,
  PlaylistResult,
  ProgressEvent,
  Recommendation,
  RunAccepted,
  RunListResponse,
  RunOptionsOverride,
  RunResultResponse,
  RunSnapshot,
  RunSummary,
  IngestAccepted,
  RagAnswer,
  SpaceDetail,
  SpaceListResponse,
  SpaceSource,
  SpaceSummary,
  SubtopicResult,
  VideoCandidate,
} from "./types";

type Wire = components["schemas"];

/** Arayuzun urettigi tip API'nin kabul ettigine sigiyor mu. */
type Sends<Hand, Schema> = [Hand] extends [Schema]
  ? true
  : { kayma: "istek tipi API'nin kabul ettigine sigmiyor"; gonderilen: Hand; beklenen: Schema };

/** API'nin donebildigi her sey arayuz tipine sigiyor mu. */
type Receives<Hand, Schema> = [Schema] extends [Hand]
  ? true
  : { kayma: "API'nin donebildigi deger arayuz tipine sigmiyor"; gelen: Schema; beklenen: Hand };

/** `true` disinda bir sey verilirse derleme burada patlar. */
type Check<T extends true> = T;

/**
 * `FilterOptions` tek pydantic modeli olarak hem istekte hem yanitta kullaniliyor
 * ve bu iki yonde farkli seyler dogru: istemci alanlari ATLAYABILIR (sunucu
 * varsayilani uygular), ama yanitta hepsi HER ZAMAN dolu gelir.
 *
 * `scripts/dump_openapi.py` yanit semalarini zorunlu isaretlerken bir
 * `requestBody`den ulasilabilen semalari bilerek atliyor -- yoksa yayimlanan
 * OpenAPI "bu alanlari gondermek zorundasin" diyerek ucuncu taraf istemcileri
 * yanlis yonlendirirdi. Bedeli, YANIT tarafinda tipin gereginden gevsek
 * kalmasi; burada yalnizca bu iddia icin sikilastiriyoruz.
 */
type CompleteFilters<T> = Omit<T, "filters"> & { filters: Required<Wire["FilterOptions"]> };

// --- istekler ---------------------------------------------------------------
export type _CreateRunRequest = Check<Sends<CreateRunRequest, Wire["CreateRunRequest"]>>;
export type _FilterOptions = Check<Sends<FilterOptions, Wire["FilterOptions"]>>;
export type _RunOptionsOverride = Check<Sends<RunOptionsOverride, Wire["RunOptionsOverride"]>>;

// --- yanitlar ---------------------------------------------------------------
export type _RunAccepted = Check<Receives<RunAccepted, Wire["RunAccepted"]>>;
export type _RunResultResponse = Check<
  Receives<
    RunResultResponse,
    Omit<Wire["RunResultResponse"], "result"> & {
      result: CompleteFilters<Wire["PlaylistResult"]> | null;
    }
  >
>;
export type _RunListResponse = Check<Receives<RunListResponse, Wire["RunListResponse"]>>;
export type _RunSummary = Check<Receives<RunSummary, Wire["RunSummary"]>>;
export type _Capabilities = Check<Receives<Capabilities, Wire["CapabilitiesResponse"]>>;
export type _PlaylistResult = Check<
  Receives<PlaylistResult, CompleteFilters<Wire["PlaylistResult"]>>
>;
export type _Recommendation = Check<Receives<Recommendation, Wire["Recommendation"]>>;
export type _VideoCandidate = Check<Receives<VideoCandidate, Wire["VideoCandidate"]>>;
export type _MetadataScore = Check<Receives<MetadataScore, Wire["MetadataScore"]>>;
export type _SubtopicResult = Check<Receives<SubtopicResult, Wire["SubtopicResult"]>>;

// --- SSE olay govdeleri -----------------------------------------------------
// Bunlar REST yaniti degil; `scripts/dump_openapi.py` semaya acikca ekliyor.
// Eklenmeseydi tip uretimi onlari hic gormezdi -- ve kaymanin en buyugu tam da
// buradaydi: `done` govdesi `job_id` tasiyordu, arayuz `run_id` bekliyordu.
export type _ProgressEvent = Check<Receives<ProgressEvent, Wire["ProgressEvent"]>>;
export type _RunSnapshot = Check<Receives<RunSnapshot, Wire["RunSnapshotBody"]>>;

// --- ogrenme alani ----------------------------------------------------------
export type _AskRequest = Check<Sends<AskRequest, Wire["AskRequest"]>>;
export type _SpaceSummary = Check<Receives<SpaceSummary, Wire["SpaceSummary"]>>;
export type _SpaceDetail = Check<Receives<SpaceDetail, Wire["SpaceDetail"]>>;
export type _SpaceListResponse = Check<Receives<SpaceListResponse, Wire["SpaceListResponse"]>>;
export type _SpaceSource = Check<Receives<SpaceSource, Wire["SpaceSource"]>>;
export type _IngestAccepted = Check<Receives<IngestAccepted, Wire["IngestAccepted"]>>;
export type _RagAnswer = Check<Receives<RagAnswer, Wire["RagAnswer"]>>;
