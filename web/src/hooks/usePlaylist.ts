import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import { useT } from "../i18n";
import type { RunSummary, SpaceSummary } from "../api/types";

export function usePlaylist() {
  const t = useT();

  const [spaces, setSpaces] = useState<SpaceSummary[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [spaceBody, runBody] = await Promise.all([api.listSpaces(50, 0), api.listRuns(50, 0)]);
      setSpaces(spaceBody.items);
      setRuns(runBody.items.filter((run) => run.is_complete));
    } catch (exception) {
      setError(exception instanceof ApiError ? exception.message : t("error.spacesLoad"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const createSpace = useCallback(
    async (name: string) => {
      const space = await api.createSpace(name);
      await load();
      return space;
    },
    [load],
  );

  const deleteSpace = useCallback(
    async (spaceId: string) => {
      await api.deleteSpace(spaceId);
      await load();
    },
    [load],
  );

  return { spaces, runs, loading, error, reload: load, createSpace, deleteSpace };
}
