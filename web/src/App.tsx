import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "./api/client";
import type { Capabilities, CreateRunRequest, PlaylistResult } from "./api/types";
import { useRunStream } from "./hooks/useRunStream";
import { RunForm } from "./features/run/RunForm";
import { RunProgress } from "./features/run/RunProgress";
import { RunResult } from "./features/run/RunResult";
import { HistoryList } from "./features/history/HistoryList";

type Tab = "run" | "history";

export default function App() {
  const [tab, setTab] = useState<Tab>("run");
  const [dark, setDark] = useState(false);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [openedResult, setOpenedResult] = useState<PlaylistResult | null>(null);

  const run = useRunStream();

  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
  }, [dark]);

  useEffect(() => {
    api.capabilities().then(setCapabilities).catch(() => setCapabilities(null));
  }, []);

  const start = useCallback(
    (payload: CreateRunRequest) => {
      setSubmitError(null);
      setOpenedResult(null);
      api
        .createRun(payload)
        .then((accepted) => run.watch(accepted.run_id))
        .catch((error: ApiError) => {
          const suffix = error.retryAfter ? ` (${error.retryAfter} sn sonra tekrar deneyin)` : "";
          setSubmitError(error.message + suffix);
        });
    },
    [run],
  );

  const openFromHistory = useCallback((runId: string) => {
    api.getRun(runId).then((body) => {
      setOpenedResult(body.result);
      setTab("run");
    });
  }, []);

  const busy = run.state === "running";
  const shownResult = openedResult ?? run.result;

  return (
    <div className="page">
      <header className="hero">
        <div className="hero__content">
          <p className="eyebrow">Learning Playlist Generator</p>
          <h1>Make A Playlist</h1>
          <p className="subtle">
            Metadata öncelikli sıralama, havuzlanmış aday keşfi ve isteğe bağlı transkript
            zenginleştirmesi.
          </p>
        </div>
      </header>

      <div className="row" style={{ justifyContent: "space-between", marginBottom: "1rem" }}>
        <div className="tabs">
          <button aria-selected={tab === "run"} onClick={() => setTab("run")}>
            Oluştur
          </button>
          <button aria-selected={tab === "history"} onClick={() => setTab("history")}>
            Geçmiş
          </button>
        </div>
        <button className="ghost" onClick={() => setDark((value) => !value)}>
          {dark ? "☀ Aydınlık" : "🌙 Karanlık"}
        </button>
      </div>

      {tab === "run" ? (
        <>
          <RunForm capabilities={capabilities} busy={busy} onSubmit={start} />

          {submitError && <p className="alert alert--error">{submitError}</p>}
          {run.error && <p className="alert alert--error">{run.error}</p>}

          {busy && (
            <RunProgress progress={run.progress} stage={run.stage} message={run.message} />
          )}

          {shownResult && <RunResult result={shownResult} />}
        </>
      ) : (
        <HistoryList onOpen={openFromHistory} />
      )}
    </div>
  );
}
