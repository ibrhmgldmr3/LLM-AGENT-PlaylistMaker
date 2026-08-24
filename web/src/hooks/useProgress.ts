import { useCallback, useEffect, useSyncExternalStore } from "react";
import {
  completedIds,
  knownTotal,
  rememberTotal,
  subscribe,
  toggleCompleted,
} from "../lib/progress";

const EMPTY: string[] = [];

/**
 * Bir dersin isaretlenmis unitelerini okur ve degistirir.
 *
 * `useSyncExternalStore` bilerek: iki ayri ekran (Derslerim listesi ve acik
 * ders sayfasi) AYNI depoyu gosteriyor ve birinde yapilan isaretleme
 * digerinde aninda gorunmek zorunda.
 */
export function useProgress(runId: string | null, total?: number) {
  const completed = useSyncExternalStore(
    subscribe,
    () => (runId ? completedIds(runId) : EMPTY),
    () => EMPTY,
  );

  // Unite sayisini yaz: Derslerim listesi ozet uctan bu sayiyi alamiyor.
  useEffect(() => {
    if (runId && total) rememberTotal(runId, total);
  }, [runId, total]);

  const toggle = useCallback(
    (videoId: string) => {
      if (runId) toggleCompleted(runId, videoId);
    },
    [runId],
  );

  return { completed, toggle, count: completed.length };
}

/** Liste ekranlari icin: isaretli unite sayisi ve (biliniyorsa) toplam. */
export function useRunProgress(runId: string): { done: number; total: number | null } {
  return useSyncExternalStore(
    subscribe,
    () => snapshot(runId),
    () => EMPTY_SNAPSHOT,
  );
}

const EMPTY_SNAPSHOT = { done: 0, total: null } as const;

// `useSyncExternalStore` her okumada ayni referansi gormeli; degismedigi
// surece onbellekten donuyoruz, yoksa React sonsuz donguye girer.
const snapshots = new Map<string, { done: number; total: number | null }>();

function snapshot(runId: string): { done: number; total: number | null } {
  const done = completedIds(runId).length;
  const total = knownTotal(runId);
  const cached = snapshots.get(runId);
  if (cached && cached.done === done && cached.total === total) return cached;
  const next = { done, total };
  snapshots.set(runId, next);
  return next;
}
