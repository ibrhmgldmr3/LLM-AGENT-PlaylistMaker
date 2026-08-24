import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { PlaylistResult, ProgressEvent, RunSnapshot, RunState } from "../api/types";

interface StreamState {
  runId: string | null;
  state: RunState | "idle";
  progress: number;
  stage: string | null;
  message: string | null;
  error: string | null;
  result: PlaylistResult | null;
}

const IDLE: StreamState = {
  runId: null,
  state: "idle",
  progress: 0,
  stage: null,
  message: null,
  error: null,
  result: null,
};

/**
 * Bir calistirmayi izler: SSE ile ilerleme, bitince sonucu ceker.
 *
 * `EventSource` kullaniliyor cunku akis tek yonlu ve tarayici yeniden baglanmayi
 * kendisi yonetiyor. Sunucu bitmis bir is icin `done` olayini aninda gonderip
 * kapattigi icin yeniden baglanan istemci takilmiyor.
 */
export function useRunStream() {
  const [state, setState] = useState<StreamState>(IDLE);
  const sourceRef = useRef<EventSource | null>(null);
  // AKTIF calistirma. `state.runId` yerine ref: `done` sonrasi calisan asenkron
  // `getRun` geri cagrisi, o sirada gecerli olan degeri okumak zorunda ve
  // closure icindeki `state` fotografi eskimis oluyor.
  const activeRunRef = useRef<string | null>(null);

  const close = useCallback(() => {
    sourceRef.current?.close();
    sourceRef.current = null;
  }, []);

  useEffect(() => close, [close]);

  const watch = useCallback(
    (runId: string) => {
      close();
      activeRunRef.current = runId;
      setState({ ...IDLE, runId, state: "running" });

      const source = new EventSource(`/api/runs/${runId}/events`);
      sourceRef.current = source;

      source.addEventListener("progress", (event) => {
        const data: ProgressEvent = JSON.parse((event as MessageEvent).data);
        setState((previous) => ({
          ...previous,
          state: "running",
          progress: data.progress,
          stage: data.stage,
          message: data.message,
        }));
      });

      source.addEventListener("done", (event) => {
        const snapshot: RunSnapshot = JSON.parse((event as MessageEvent).data);
        close();
        if (snapshot.state === "done") {
          // Sonucu ayri uctan cek: SSE yalnizca ilerleme tasiyor.
          //
          // Yaniti YAZMADAN once bu calistirmanin hala aktif olup olmadigina
          // bakiliyor. A calisirken kullanici B'yi baslatirsa A'nin gec gelen
          // yaniti B'nin ekranina dusuyordu: yanlis playlist, dogru gorunumde.
          api
            .getRun(runId)
            .then((body) => {
              if (activeRunRef.current !== runId) return;
              setState((previous) => ({
                ...previous,
                state: "done",
                progress: 1,
                result: body.result,
              }));
            })
            .catch((error: Error) => {
              if (activeRunRef.current !== runId) return;
              setState((previous) => ({ ...previous, state: "failed", error: error.message }));
            });
        } else if (activeRunRef.current === runId) {
          setState((previous) => ({
            ...previous,
            state: snapshot.state,
            // Iptal bir BASARISIZLIK degil, kullanicinin kendi karari. Ortak
            // geri-dusus metni ("tamamlanamadi") uctan uca denemede kirmizi
            // hata kutusunda cikip bir seyin bozuldugunu ima ediyordu.
            error:
              snapshot.error ??
              (snapshot.state === "cancelled"
                ? "Hazırlığı sen iptal ettin."
                : snapshot.state === "interrupted"
                  ? "Sunucu yeniden başlatıldığı için hazırlık yarıda kaldı."
                  : "Ders planı tamamlanamadı."),
          }));
        }
      });

      source.addEventListener("error", (event) => {
        // Sunucunun gonderdigi `event: error` (bilinmeyen calistirma vb.)
        const raw = (event as MessageEvent).data;
        if (raw) {
          close();
          const data = JSON.parse(raw);
          setState((previous) => ({ ...previous, state: "failed", error: data.detail }));
          // Sunucunun kendi mesaji daha bilgilendirici; asagidaki genel
          // metinle EZILMEMELI.
          return;
        }
        // Govdesiz `error`: ya gecici kopma ya da KALICI kapanma. Ikisini
        // `readyState` ayiriyor -- gecici kopmada tarayici CONNECTING'e donup
        // kendisi yeniden deniyor, sunucu 200 disi yanit verdiginde (oturum
        // dustu ve 401 geldi, sunucu kapandi) baglanti CLOSED oluyor ve bir
        // daha denenmiyor.
        //
        // Eskiden ikisi de sessizce yok sayiliyordu: kalici kapanmada arayuz
        // SONSUZA KADAR "calisiyor"da asili kaliyor, ilerleme cubugu donuyor
        // ve kullaniciya hicbir sey olmadigini soyleyen bir sey yoktu.
        if (source.readyState === 2 /* EventSource.CLOSED */) {
          close();
          setState((previous) => ({
            ...previous,
            state: "failed",
            error:
              "Sunucuyla bağlantı koptu. Oturumun düşmüş olabilir; sayfayı " +
              "yenileyip Derslerim bölümünden kontrol et.",
          }));
        }
      });
    },
    [close],
  );

  const reset = useCallback(() => {
    close();
    activeRunRef.current = null;
    setState(IDLE);
  }, [close]);

  /**
   * Calistirmayi SUNUCUDA durdurur.
   *
   * Akis burada bilerek KAPATILMIYOR: sunucu isi iptal edince akis `done`
   * olayini `state: "cancelled"` ile gonderiyor ve durumu o gecis yonetiyor.
   * Burada kapatsaydik son durumu hic gormez, arayuz "calisiyor"da asili
   * kalirdi. Isin iptali bir sonraki ilerleme bildiriminde gerceklestigi icin
   * arada kisa bir bekleme olabilir; mesaj bunu gorunur kiliyor.
   */
  const cancel = useCallback(async () => {
    const runId = state.runId;
    if (!runId) return;
    try {
      await api.cancelRun(runId);
      setState((previous) => ({ ...previous, message: "İptal ediliyor…" }));
    } catch (error) {
      setState((previous) => ({ ...previous, error: (error as Error).message }));
    }
  }, [state.runId]);

  // `stopWatching` eskiden `cancel` adiyla disa veriliyordu ve bu yanilticiydi:
  // yalnizca YEREL akisi kapatiyor, sunucudaki calistirma devam ediyordu.
  // Gercek iptal artik `cancel`; ikisi ayri seyler ve adlari da oyle.
  return { ...state, watch, reset, cancel, stopWatching: close };
}
