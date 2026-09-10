import { useCallback, useEffect, useRef, useState } from "react";
import type { ProgressEvent, RunSnapshot, RunState } from "../api/types";
import { useT } from "../i18n";

interface JobState {
  jobId: string | null;
  state: RunState | "idle";
  progress: number;
  message: string | null;
  error: string | null;
}

const IDLE: JobState = {
  jobId: null,
  state: "idle",
  progress: 0,
  message: null,
  error: null,
};

/**
 * Genel amacli is ilerlemesi izleyicisi.
 *
 * `useRunStream`den AYRI cunku o, isin bitiminde playlist sonucunu ayri bir
 * uctan cekmek ve iptali yonetmek gibi calistirmaya OZGU isler yapiyor. Iceri
 * alma isinde sonuc diye cekilecek bir sey yok -- bitince alan yeniden
 * okunuyor. Ortak olan yalnizca `EventSource` mekanigi ve o buraya alindi;
 * ikisini tek kancada birlestirmek, kullanilmayan yarisi surekli tasinan bir
 * arayuz uretirdi.
 *
 * `onDone` bir REF'te tutuluyor: cagiran taraf her render'da yeni bir fonksiyon
 * verse bile akis yeniden kurulmamali.
 */
export function useJobStream(onDone?: () => void) {
  const t = useT();

  const [state, setState] = useState<JobState>(IDLE);
  const sourceRef = useRef<EventSource | null>(null);
  const doneRef = useRef(onDone);
  doneRef.current = onDone;

  const close = useCallback(() => {
    sourceRef.current?.close();
    sourceRef.current = null;
  }, []);

  useEffect(() => close, [close]);

  const watch = useCallback(
    (jobId: string, eventsUrl: string) => {
      close();
      setState({ ...IDLE, jobId, state: "running", message: t("stream.starting") });

      const source = new EventSource(eventsUrl);
      sourceRef.current = source;

      source.addEventListener("progress", (event) => {
        const data: ProgressEvent = JSON.parse((event as MessageEvent).data);
        setState((previous) => ({
          ...previous,
          state: "running",
          progress: data.progress,
          message: data.message,
        }));
      });

      source.addEventListener("done", (event) => {
        const snapshot: RunSnapshot = JSON.parse((event as MessageEvent).data);
        close();
        setState((previous) => ({
          ...previous,
          state: snapshot.state,
          progress: snapshot.state === "done" ? 1 : previous.progress,
          message: snapshot.message,
          error: snapshot.error,
        }));
        doneRef.current?.();
      });

      source.addEventListener("error", (event) => {
        const raw = (event as MessageEvent).data;
        if (raw) {
          close();
          setState((previous) => ({ ...previous, state: "failed", error: JSON.parse(raw).detail }));
          return;
        }
        // Govdesiz `error`: gecici kopmada tarayici kendisi yeniden deniyor.
        // Yalnizca KALICI kapanmada (readyState CLOSED) durum bildiriyoruz;
        // aksi halde arayuz sonsuza kadar "calisiyor"da asili kalirdi.
        if (source.readyState === 2 /* EventSource.CLOSED */) {
          close();
          setState((previous) => ({
            ...previous,
            state: "failed",
            error: t("stream.ingestLost"),
          }));
          doneRef.current?.();
        }
      });
    },
    [close],
  );

  const reset = useCallback(() => {
    close();
    setState(IDLE);
  }, [close]);

  return { ...state, watch, reset };
}
