import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, Plus } from "lucide-react";
import { api, ApiError, setUnauthorizedHandler } from "./api/client";
import type { Capabilities, CreateRunRequest, PlaylistResult } from "./api/types";
import { useRunStream } from "./hooks/useRunStream";
import { RunForm } from "./features/run/RunForm";
import { RunProgress } from "./features/run/RunProgress";
import { RunResult } from "./features/run/RunResult";
import { HistoryList } from "./features/history/HistoryList";
import { SignIn } from "./features/auth/SignIn";
import { SpaceWorkspace } from "./features/learn/SpaceWorkspace";
import { AppShell, type Section } from "./components/AppShell";
import { Note } from "./components/ui";
import { LanguageProvider, useT } from "./i18n";
import type { Session } from "./api/client";

const THEME_KEY = "derslik:theme";

function initialDark(): boolean {
  try {
    const saved = window.localStorage.getItem(THEME_KEY);
    if (saved) return saved === "dark";
  } catch {
    /* gizli sekme: tercih okunamaz, sistem ayarina duselim */
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

/** Dil saglayicisi EN DISTA: kabuk ve her ekran cevirilere erisebilmeli. */
export default function App() {
  return (
    <LanguageProvider>
      <AppRoot />
    </LanguageProvider>
  );
}

function AppRoot() {
  const t = useT();
  const [section, setSection] = useState<Section>("start");
  const [dark, setDark] = useState(initialDark);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [openedCourse, setOpenedCourse] = useState<PlaylistResult | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  // `createRun` yaniti donene KADAR true. `run.state` tek basina yetmiyordu:
  // istek ucusta iken hicbir sey "calisiyor" gorunmuyor, hizli cift tiklama
  // ya da yavas ag iki AYRI sunucu calistirmasi baslatiyor ve ikincisi
  // izlenmedigi icin sahipsiz kaliyordu.
  const [submitting, setSubmitting] = useState(false);
  const run = useRunStream();

  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    document.documentElement.classList.toggle("dark", dark);
    try {
      window.localStorage.setItem(THEME_KEY, dark ? "dark" : "light");
    } catch {
      /* yazilamadi: tema yalnizca bu oturum icin gecerli olur */
    }
  }, [dark]);

  // Herhangi bir uctan 401 gelirse oturum durumunu YENIDEN ogren. `me()` 401
  // donmuyor (oturumsuzken `signed_in: false` doner), yani burada dongu yok.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      api.me().then(setSession).catch(() => setSession(null));
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  useEffect(() => {
    api.capabilities().then(setCapabilities).catch(() => setCapabilities(null));
    // Oturum durumu ONCE ogreniliyor: cok kullanicili kurulumda giris ekrani
    // disinda hicbir sey gosterilmemeli.
    api.me().then(setSession).catch(() => setSession(null));
  }, []);

  const start = useCallback(
    (payload: CreateRunRequest) => {
      setSubmitError(null);
      setSubmitting(true);
      api
        .createRun(payload)
        .then((accepted) => run.watch(accepted.run_id))
        .catch((error: ApiError) => {
          const suffix = error.retryAfter ? ` (${error.retryAfter} sn sonra tekrar deneyin)` : "";
          setSubmitError(error.message + suffix);
        })
        .finally(() => setSubmitting(false));
    },
    [run],
  );

  const openCourse = useCallback((runId: string) => {
    api.getRun(runId).then((body) => {
      setOpenedCourse(body.result);
      setSection("courses");
    });
  }, []);

  const navigate = useCallback((next: Section) => {
    // Bolum degistirirken acik ders KAPANIYOR: "Derslerim"e donunce eski
    // dersin acik kalmasi, listeye ulasmak icin iki tiklama demekti.
    if (next !== "courses") setOpenedCourse(null);
    setSection(next);
  }, []);

  // Form kilidi istegin GONDERILDIGI anda basliyor (cift gonderim korumasi);
  // ilerleme paneli ise ancak gercek bir calistirma varken anlamli.
  const busy = submitting || run.state === "running";
  const running = run.state === "running";
  const ragOn = capabilities?.rag_available !== false;
  const sections: Section[] = ragOn ? ["start", "courses", "room"] : ["start", "courses"];

  if (session?.auth_required && !session.signed_in) {
    return <SignIn dark={dark} onToggleTheme={() => setDark((value) => !value)} />;
  }

  const title =
    section === "start"
      ? t("nav.start")
      : section === "room"
        ? t("nav.room")
        : (openedCourse?.topic ?? t("nav.courses"));

  const actions =
    section === "courses" ? (
      openedCourse ? (
        <button type="button" className="btn btn--quiet" onClick={() => setOpenedCourse(null)}>
          <ArrowLeft className="h-4 w-4" aria-hidden />
          {t("nav.courses")}
        </button>
      ) : (
        <button type="button" className="btn" onClick={() => navigate("start")}>
          <Plus className="h-4 w-4" aria-hidden />
          {t("app.newPlan")}
        </button>
      )
    ) : null;

  return (
    <AppShell
      section={section}
      sections={sections}
      onNavigate={navigate}
      title={title}
      actions={actions}
      dark={dark}
      onToggleTheme={() => setDark((value) => !value)}
      email={session?.auth_required && session.signed_in ? session.email : null}
      onSignOut={
        session?.auth_required && session.signed_in
          ? () => api.logout().then(() => window.location.reload())
          : undefined
      }
      wide={section === "room"}
    >
      {section === "start" && (
        <div className="stack stack--loose">
          <RunForm capabilities={capabilities} busy={busy} onSubmit={start} />

          {submitError && (
            <Note tone="danger" role="alert" title={t("app.startFailed")}>
              {submitError}
            </Note>
          )}

          {/* Iptal kirmizi kutuda gosterilmiyor: kullanicinin kendi karari,
              bozulan bir sey degil. */}
          {run.error &&
            (run.state === "cancelled" ? (
              <Note role="status">{run.error}</Note>
            ) : (
              <Note tone="danger" role="alert" title={t("app.interrupted")}>
                {run.error}
              </Note>
            ))}

          {running && (
            <RunProgress
              progress={run.progress}
              stage={run.stage}
              message={run.message}
              onCancel={run.cancel}
            />
          )}

          {run.result && (
            <div className="stack">
              <Note tone="ok" role="status" title={t("app.ready")}>
                {t("app.readySaved")}
              </Note>
              <RunResult result={run.result} />
            </div>
          )}
        </div>
      )}

      {section === "courses" &&
        (openedCourse ? (
          <RunResult result={openedCourse} />
        ) : (
          <HistoryList onOpen={openCourse} onCreate={() => navigate("start")} />
        ))}

      {section === "room" && <SpaceWorkspace />}
    </AppShell>
  );
}
