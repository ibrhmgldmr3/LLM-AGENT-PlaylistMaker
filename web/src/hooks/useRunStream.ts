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

  const close = useCallback(() => {
    sourceRef.current?.close();
    sourceRef.current = null;
  }, []);

  useEffect(() => close, [close]);

  const watch = useCallback(
    (runId: string) => {
      close();
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
          api
            .getRun(runId)
            .then((body) =>
              setState((previous) => ({
                ...previous,
                state: "done",
                progress: 1,
                result: body.result,
              })),
            )
            .catch((error: Error) =>
              setState((previous) => ({ ...previous, state: "failed", error: error.message })),
            );
        } else {
          setState((previous) => ({
            ...previous,
            state: snapshot.state,
            error: snapshot.error ?? "Çalıştırma tamamlanamadı",
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
        }
        // Govdesiz `error` baglanti kopmasidir; EventSource kendisi yeniden dener.
      });
    },
    [close],
  );

  const reset = useCallback(() => {
    close();
    setState(IDLE);
  }, [close]);

  return { ...state, watch, reset, cancel: close };
}
