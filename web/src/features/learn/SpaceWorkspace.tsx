import { useMemo, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import * as Tooltip from "@radix-ui/react-tooltip";
import {
  ArrowLeft,
  ArrowUpRight,
  AudioWaveform,
  BookMarked,
  CircleSlash,
  Copy,
  FileText,
  ListVideo,
  MessagesSquare,
  MonitorPlay,
  Notebook,
  Plus,
  Quote,
  Send,
  ShieldAlert,
  Trash2,
  Upload,
  Video,
  X,
} from "lucide-react";
import { cn } from "../../lib/cn";
import { formatClock, formatDateTime } from "../../lib/format";
import { roomStyle } from "../../lib/roomKey";
import { useLanguage, useT } from "../../i18n";
import type { Dict } from "../../i18n/dict";
import type {
  Citation,
  RagRefusal,
  RunSummary,
  SpaceSource,
  SpaceSummary,
} from "../../api/types";
import { usePlaylist } from "../../hooks/usePlaylist";
import { useVideoRAG } from "../../hooks/useVideoRAG";
import { Empty, Meter, Note, Spinner } from "../../components/ui";

const PROMPTS: (keyof Dict)[] = ["ws.prompt0", "ws.prompt1", "ws.prompt2", "ws.prompt3"];

/**
 * Kaynak durumlari kullanicinin dilinde.
 *
 * `no_text` BILEREK "hata" degil "kapsam disi": transkripti olmayan bir video
 * ya da taranmis bir PDF gecerli bir dosyadir, yalnizca aranabilir metni
 * yoktur. Ikisini ayni gostermek kullaniciya dosyasinin bozuk oldugunu
 * dusundururdu -- ve asil onemlisi, NEYIN ARANAMAYACAGINI bilmesi gerekiyor,
 * yoksa o konuda "bulamadim" yanitini alip sistemi bozuk sanar.
 */
const STATUS: Record<SpaceSource["status"], { label: keyof Dict; tone?: "ok" | "warn" | "danger" }> = {
  pending: { label: "ws.preparing" },
  indexed: { label: "ws.ready", tone: "ok" },
  no_text: { label: "ws.status.noText", tone: "warn" },
  failed: { label: "ws.status.failed", tone: "danger" },
};

function timeFromUrl(url: string | null): number | null {
  if (!url) return null;
  const match = url.match(/[?&]t=(\d+)s?/);
  return match ? Number(match[1]) : null;
}

function citationSecond(citation: Citation): number | null {
  return citation.start_sec ?? timeFromUrl(citation.url);
}

function extractYouTubeId(url: string | null): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    if (parsed.hostname.includes("youtu.be")) return parsed.pathname.slice(1) || null;
    if (parsed.searchParams.get("v")) return parsed.searchParams.get("v");
    const parts = parsed.pathname.split("/").filter(Boolean);
    const marker = parts.findIndex((part) => ["embed", "shorts", "live"].includes(part));
    return marker >= 0 ? (parts[marker + 1] ?? null) : null;
  } catch {
    return null;
  }
}

function embedUrl(source: SpaceSource | null, startSecond: number): string | null {
  const id = extractYouTubeId(source?.url ?? null);
  if (!id) return null;
  const params = new URLSearchParams({
    rel: "0",
    modestbranding: "1",
    start: String(Math.max(0, Math.floor(startSecond))),
  });
  return `https://www.youtube.com/embed/${id}?${params.toString()}`;
}

/* ------------------------------------------------------------ yeni defter */

function CreateNotebookDialog({
  open,
  onOpenChange,
  onCreate,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreate: (name: string) => Promise<void>;
}) {
  const t = useT();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);

  // Eskiden iki adimli bir sihirbazdi ve HER IKI adim da ayni tek soruyu
  // soruyordu; ikinci adim yalnizca bir tiklama daha ekliyordu.
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = value.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    try {
      await onCreate(trimmed);
      setValue("");
      onOpenChange(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="dialog">
          <div className="row row--between" style={{ alignItems: "flex-start" }}>
            <Dialog.Title className="section-title">{t("ws.newTitle")}</Dialog.Title>
            <Dialog.Close asChild>
              <button type="button" className="icon-btn" aria-label={t("ws.close")}>
                <X className="h-4 w-4" />
              </button>
            </Dialog.Close>
          </div>
          <Dialog.Description className="lede" style={{ margin: "0.4rem 0 1.2rem", fontSize: "0.92rem" }}>
            {t("ws.newBody")}
          </Dialog.Description>

          <form onSubmit={submit} className="stack stack--tight">
            <label className="label" htmlFor="notebook-name">
              {t("ws.nameLabel")}
            </label>
            <input
              id="notebook-name"
              type="text"
              value={value}
              autoComplete="off"
              placeholder={t("ws.namePlaceholder")}
              onChange={(event) => setValue(event.target.value)}
            />
            <button
              type="submit"
              className="btn btn--primary"
              style={{ marginTop: "0.7rem" }}
              disabled={!value.trim() || busy}
            >
              {busy && <Spinner />}
              {busy ? t("ws.creating") : t("ws.create")}
            </button>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/* -------------------------------------------------------------- defter listesi */

function NotebookList({
  spaces,
  runs,
  loading,
  onOpen,
  onCreateClick,
  onDelete,
}: {
  spaces: SpaceSummary[];
  runs: RunSummary[];
  loading: boolean;
  onOpen: (spaceId: string) => void;
  onCreateClick: () => void;
  onDelete: (spaceId: string) => void;
}) {
  const { t, lang } = useLanguage();
  return (
    <div className="stack stack--loose">
      <div className="stack stack--tight">
        <h2 className="section-title">{t("ws.listTitle")}</h2>
        <p className="lede">
          {t("ws.listLede")}
        </p>
        <div className="row" style={{ marginTop: "0.4rem" }}>
          <button type="button" className="btn btn--primary" onClick={onCreateClick}>
            <Plus className="h-4 w-4" aria-hidden />
            {t("ws.newButton")}
          </button>
          {runs.length > 0 && (
            <span className="meta">
              {t("ws.runsReady", { n: runs.length })}
            </span>
          )}
        </div>
      </div>

      {loading ? (
        <p className="meta">
          <Spinner /> {t("ws.loadingList")}
        </p>
      ) : spaces.length === 0 ? (
        <Empty
          icon={Notebook}
          title={t("ws.emptyTitle")}
          action={
            <button type="button" className="btn btn--primary" onClick={onCreateClick}>
              <Plus className="h-4 w-4" aria-hidden />
              {t("ws.emptyAction")}
            </button>
          }
        >
          {t("ws.emptyBody")}
        </Empty>
      ) : (
        <div className="courses">
          {spaces.map((space) => (
            // Defterin SALON RENGI kimliginden turetiliyor: ayni renk bu
            // satirda, defterin icinde ve alintilarinin kunyesinde. Kullanici
            // rengi bir kez ogrenip sonra okumadan taniyor.
            <article className="course-row" key={space.space_id} style={roomStyle(space.space_id)}>
              <span className="course-row__mark" aria-hidden>
                <Notebook className="h-4 w-4" />
              </span>
              <div className="course-row__body min-w-0">
                <h3 className="course-row__title">{space.name}</h3>
                <p className="meta" style={{ marginTop: "0.2rem" }}>
                  {space.source_count === 0
                    ? t("ws.noSources")
                    : t("ws.sourceCount", { n: space.source_count })}
                  {" · "}
                  {formatDateTime(space.updated_at, lang)}
                </p>
              </div>
              <div className="row" style={{ gap: "0.4rem" }}>
                <button type="button" className="btn" onClick={() => onOpen(space.space_id)}>
                  {t("ws.open")}
                </button>
                <button
                  type="button"
                  className="icon-btn icon-btn--danger"
                  onClick={() => onDelete(space.space_id)}
                  aria-label={t("ws.deleteNotebook", { name: space.name })}
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ oynatıcı */

function Player({
  source,
  startSecond,
}: {
  source: SpaceSource | null;
  startSecond: number;
}) {
  const t = useT();
  const src = embedUrl(source, startSecond);
  return (
    <div className="player">
      <div className="player__bar">
        <h3 className="player__title">{source?.title ?? t("ws.noSourceSelected")}</h3>
        {source?.url && (
          <a
            className="btn btn--quiet"
            href={source.url}
            target="_blank"
            rel="noreferrer"
          >
            {t("ws.openOnYouTube")}
            <ArrowUpRight className="h-3.5 w-3.5" aria-hidden />
          </a>
        )}
      </div>
      <div className="player__frame">
        {src ? (
          <iframe
            key={`${source?.source_id}-${startSecond}`}
            src={src}
            title={source?.title ?? "Video"}
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
            allowFullScreen
          />
        ) : (
          <div className="player__empty">
            <MonitorPlay className="h-8 w-8" aria-hidden />
            <p>
              {source
                ? t("ws.notAVideo")
                : t("ws.pickSource")}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- işaretli anlar */

/**
 * Bu paneldeki her satir GERCEK: sohbette gelen cevaplarin kaynak gostergeleri.
 *
 * Burada once bir "transkript" ve bir "AI ozeti" sekmesi vardi; ikisi de
 * ORNEK metinle dolduruluyordu, yani kullaniciya videonun icerigi diye
 * uydurulmus cumleler gosteriliyordu. Bir ogrenme aracinda bu, ozelligin
 * kendisini degersiz kilar. Uydurma panel kaldirildi; yerine yalnizca
 * sunucudan gelen alintilar kondu.
 */
function Moments({
  citations,
  onSeek,
  activeSecond,
}: {
  citations: Citation[];
  onSeek: (second: number) => void;
  activeSecond: number;
}) {
  const t = useT();
  if (citations.length === 0) {
    return (
      <div className="panel panel__pad">
        <h3 className="section-title" style={{ fontSize: "1rem", marginBottom: "0.4rem" }}>
          {t("ws.marks")}
        </h3>
        <p className="hint" style={{ marginTop: 0 }}>
            {t("ws.marksEmpty")}
        </p>
      </div>
    );
  }

  return (
    <div className="panel panel__pad">
      <h3 className="section-title" style={{ fontSize: "1rem", marginBottom: "0.6rem" }}>
        {t("ws.marks")}
      </h3>
      <div className="lines">
        {citations.map((citation, index) => {
          const second = citationSecond(citation);
          return (
            <button
              key={`${citation.source_id}-${index}`}
              type="button"
              className={cn("line", second !== null && second === activeSecond && "line--active")}
              // TRANSKRIPT ISARETI. Bu liste birden cok kaynaktan an topluyor;
              // renk olmadan hangi satirin hangi kaynaktan geldigi ancak
              // metni okuyarak anlasiliyordu.
              style={roomStyle(citation.source_id)}
              onClick={() => second !== null && onSeek(second)}
              disabled={second === null}
            >
              <span className="line__t">
                {second !== null
                  ? formatClock(second)
                  : citation.page
                    ? t("format.page", { n: citation.page })
                    : "—"}
              </span>
              <span className="min-w-0">{citation.quote}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- transkript & metin */

function SourceTranscriptViewer({
  source,
  sourceText,
  loading,
  activeSecond,
  onSeek,
  onTranscribe,
  transcribing,
}: {
  source: SpaceSource | null;
  sourceText: ReturnType<typeof useVideoRAG>["sourceText"];
  loading: boolean;
  activeSecond: number;
  onSeek: (second: number) => void;
  onTranscribe: (sourceId: string) => void;
  transcribing: boolean;
}) {
  const t = useT();
  const [filterQuery, setFilterQuery] = useState("");

  if (!source) {
    return (
      <div className="panel panel__pad">
        <p className="hint" style={{ margin: 0 }}>
          {t("ws.selectForText")}
        </p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="panel panel__pad">
        <p className="meta" style={{ margin: 0 }}>
          <Spinner /> {t("ws.loadingText")}
        </p>
      </div>
    );
  }

  if (source.status === "no_text") {
    return (
      <div className="panel panel__pad stack stack--tight">
        <div className="row row--between" style={{ alignItems: "center" }}>
          <h3 className="section-title" style={{ fontSize: "1rem" }}>
            {t("ws.noCaptionsTitle")}
          </h3>
          <span className="tag tag--warn">{t("ws.status.noText")}</span>
        </div>
        <p className="hint" style={{ marginTop: 0 }}>
          {source.kind === "video"
            ? t("ws.noCaptions")
            : t("ws.noTextFromFile")}
        </p>
        {source.kind === "video" && (
          <div className="row" style={{ marginTop: "0.5rem" }}>
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => onTranscribe(source.source_id)}
              disabled={transcribing}
            >
              {transcribing ? <Spinner /> : <AudioWaveform className="h-4 w-4" aria-hidden />}
              {transcribing ? t("ws.asrRunning") : t("ws.asrStart")}
            </button>
          </div>
        )}
      </div>
    );
  }

  const chunks = sourceText?.chunks ?? [];
  const filteredChunks = filterQuery.trim()
    ? chunks.filter((c) => c.text.toLowerCase().includes(filterQuery.toLowerCase()))
    : chunks;

  const isAsr = sourceText?.transcript_source === "asr";

  return (
    <div className="panel panel__pad stack stack--tight">
      <div className="row row--between" style={{ alignItems: "center" }}>
        <div className="row" style={{ gap: "0.5rem", alignItems: "center" }}>
          <h3 className="section-title" style={{ fontSize: "1rem" }}>
            {source.kind === "video" ? "Transkript" : t("ws.docText")}
          </h3>
          {isAsr && (
            <span className="tag tag--chalk" title={sourceText?.transcript_backend ?? "Whisper"}>
              Whisper ASR
            </span>
          )}
          <span className="meta">{t("ws.sectionCount", { n: chunks.length })}</span>
        </div>

        {chunks.length > 2 && (
          <div className="row" style={{ gap: "0.4rem" }}>
            <input
              type="text"
              value={filterQuery}
              onChange={(e) => setFilterQuery(e.target.value)}
              placeholder={t("ws.searchInText")}
              style={{ padding: "0.25rem 0.5rem", fontSize: "0.85rem", width: "11rem" }}
            />
            {filterQuery && (
              <button
                type="button"
                className="icon-btn"
                onClick={() => setFilterQuery("")}
                aria-label={t("ws.clearSearch")}
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        )}
      </div>

      {chunks.length === 0 ? (
        <p className="hint" style={{ margin: 0 }}>
          {sourceText?.full_text || t("ws.noTextYet")}
        </p>
      ) : filteredChunks.length === 0 ? (
        <p className="hint" style={{ margin: "0.5rem 0" }}>
          {t("ws.filterEmpty", { q: filterQuery })}
        </p>
      ) : (
        // TRANSKRIPT ISARETI. Renk kabin ustunde bir kez veriliyor: bu
        // listenin tamami TEK kaynagin metni, dolayisiyla anahtar satir
        // basina degil kaynagin kendisi basina duser ve `--key` cocuklara
        // kaskad uzerinden iner.
        <div
          className="lines"
          style={{ maxHeight: "20rem", overflowY: "auto", ...roomStyle(source.source_id) }}
        >
          {filteredChunks.map((chunk) => {
            const hasTime = chunk.start_sec !== null && chunk.start_sec !== undefined;
            const isActive =
              hasTime &&
              Math.abs((chunk.start_sec ?? 0) - activeSecond) < 5;
            return (
              <button
                key={chunk.chunk_id}
                type="button"
                className={cn("line", isActive && "line--active")}
                onClick={() => hasTime && onSeek(chunk.start_sec!)}
                disabled={!hasTime}
                style={{ textAlign: "left" }}
              >
                <span className="line__t">
                  {hasTime
                    ? formatClock(chunk.start_sec!)
                    : chunk.page
                      ? t("format.page", { n: chunk.page })
                      : `§${chunk.ordinal + 1}`}
                </span>
                <span className="min-w-0" style={{ whiteSpace: "pre-wrap" }}>
                  {chunk.text}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------- sohbet */

/**
 * Yanit VERILEMEDIGI durumlar. UC AYRI DIL, biri digerinin soluk hali degil.
 *
 * Bunlar bir cevap balonu DEGIL, cunku cevap degiller. Reddi `.msg--ai`
 * icinde gostermek -- bu urunun eski hali -- kullaniciya "sistem sana bir sey
 * anlatti" diyordu; oysa anlatilan sey sistemin KONUSMAYI reddettigi.
 *
 * Ayrim kullanici icin islevsel: "kapsam disi"nda soruyu ya da defteri
 * degistirmek anlamli, "dogrulanamadi"da ayni soruyu tekrar sormak. Ikisini
 * ayni cumleyle anlatmak kullaniciyi yanlis harekete yonlendiriyordu.
 *
 * Kirmizi YOK: reddetmek bu ozelligin VAADI, arizasi degil.
 */
function AbsenceNotice({ kind, text }: { kind: RagRefusal; text: string }) {
  const t = useT();
  const unverified = kind === "unverified";
  const tone =
    kind === "unverified"
      ? "notice--unverified"
      : kind === "no_content"
        ? "notice--outofscope"
        : "notice--absent";
  const what =
    kind === "unverified"
      ? t("ws.unverified")
      : kind === "no_content"
        ? t("ws.outOfScope")
        : "Bu defterde yok";

  return (
    <div className={cn("notice", tone)} role="status">
      {unverified ? (
        <ShieldAlert className="h-4 w-4" aria-hidden />
      ) : (
        <CircleSlash className="h-4 w-4" aria-hidden />
      )}
      <p className="notice__body">
        <span className="notice__what">{what}</span>
        {text}
      </p>
    </div>
  );
}

function Chat({
  messages,
  asking,
  ready,
  onAsk,
  onCitationClick,
}: {
  messages: ReturnType<typeof useVideoRAG>["messages"];
  asking: boolean;
  ready: boolean;
  onAsk: (question: string) => void;
  onCitationClick: (citation: Citation) => void;
}) {
  const t = useT();
  const [question, setQuestion] = useState("");

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = question.trim();
    if (!trimmed) return;
    onAsk(trimmed);
    setQuestion("");
  };

  return (
    <aside className="room__aside">
      <div className="panel-head">
        <h3>{t("ws.askTitle")}</h3>
        <MessagesSquare className="h-4 w-4 text-[color:var(--ink-3)]" aria-hidden />
      </div>

      {ready && messages.length === 0 && (
        <div className="row" style={{ gap: "0.4rem", padding: "0.85rem 1rem 0" }}>
          {PROMPTS.map((prompt) => (
            <button
              key={prompt}
              type="button"
              className="chip"
              onClick={() => onAsk(t(prompt))}
              disabled={asking}
            >
              {t(prompt)}
            </button>
          ))}
        </div>
      )}

      <div className="thread">
        {messages.length === 0 && (
          <Empty icon={Quote} title={ready ? t("ws.noQuestionYet") : t("ws.addSourceFirst")}>
            {ready
              ? t("ws.askHint")
              : t("ws.needSources")}
          </Empty>
        )}

        {messages.map((message) =>
          message.refusal ? (
            <AbsenceNotice key={message.id} kind={message.refusal} text={message.content} />
          ) : (
          <article
            key={message.id}
            className={cn("msg", message.role === "user" ? "msg--me" : "msg--ai")}
          >
            <p>
              {message.content ||
                (message.streaming ? t("ws.searching") : "")}
            </p>

            {message.role === "assistant" && message.content && !message.streaming && (
              <div className="row" style={{ gap: "0.25rem", marginTop: "0.4rem" }}>
                <button
                  type="button"
                  className="icon-btn"
                  style={{ width: "1.9rem", height: "1.9rem", flexBasis: "1.9rem" }}
                  onClick={() => void navigator.clipboard?.writeText(message.content)}
                  aria-label={t("ws.copyAnswer")}
                >
                  <Copy className="h-3.5 w-3.5" />
                </button>
              </div>
            )}

            {message.citations.length > 0 && (
              <div className="cites">
                {message.citations.map((citation, index) => {
                  const second = citationSecond(citation);
                  return (
                    <button
                      key={`${citation.source_id}-${index}`}
                      type="button"
                      className="cite"
                      // KUNYE. Salon rengi kaynagin kimliginden turuyor, yani
                      // ayni kaynak asagidaki isaretli anlar listesinde ve
                      // kaynak listesinde de AYNI rengi tasiyor.
                      style={roomStyle(citation.source_id)}
                      onClick={() => onCitationClick(citation)}
                      title={citation.quote}
                    >
                      <BookMarked className="h-3 w-3 shrink-0" aria-hidden />
                      {/* Metin KENDI kabinda: `text-overflow` bir esnek
                          kutunun cocuklarina islemez, bu yuzden kunye
                          basliklari uc nokta olmadan kelime ortasinda
                          kesiliyordu -- ve kesilmis bir kunye hangi kaynaga
                          gittigini soylemez. */}
                      <span className="cite__text">
                        {second !== null ? `${formatClock(second)} · ` : ""}
                        {citation.title}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
          </article>
          ),
        )}
      </div>

      <form className="ask" onSubmit={submit}>
        <input
          type="text"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder={ready ? t("ws.askPlaceholder") : t("ws.addSourceFirst")}
          disabled={!ready}
          aria-label={t("ws.questionLabel")}
        />
        <button
          type="submit"
          className="btn btn--primary"
          disabled={!ready || asking || !question.trim()}
          aria-label={t("ws.send")}
        >
          {asking ? <Spinner /> : <Send className="h-4 w-4" aria-hidden />}
        </button>
      </form>
    </aside>
  );
}

/* ------------------------------------------------------------- kaynak ekleme */

function AddSources({
  runs,
  busy,
  onAddRun,
  onUpload,
}: {
  runs: RunSummary[];
  busy: boolean;
  onAddRun: (runId: string) => void;
  onUpload: (file: File) => void;
}) {
  const t = useT();
  const [runId, setRunId] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <div className="row" style={{ gap: "0.5rem" }}>
      <select
        value={runId}
        onChange={(event) => setRunId(event.target.value)}
        disabled={busy || runs.length === 0}
        aria-label={t("ws.runToAdd")}
        style={{ width: "auto", minWidth: "min(100%, 15rem)" }}
      >
        <option value="">
          {runs.length === 0 ? t("ws.noCompletedRuns") : t("ws.pickRun")}
        </option>
        {runs.map((run) => (
          <option key={run.run_id} value={run.run_id}>
            {run.topic}
          </option>
        ))}
      </select>
      <button
        type="button"
        className="btn"
        disabled={!runId || busy}
        onClick={() => onAddRun(runId)}
      >
        <ListVideo className="h-4 w-4" aria-hidden />
        {t("ws.addVideos")}
      </button>

      <input
        ref={inputRef}
        type="file"
        className="sr-only"
        accept=".pdf,.docx,.txt,.md"
        aria-label={t("ws.uploadDoc")}
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onUpload(file);
          // Ayni dosyayi tekrar secebilmek icin girdiyi sifirla; `change` olayi
          // ayni deger yeniden secildiginde tetiklenmiyor.
          if (inputRef.current) inputRef.current.value = "";
        }}
      />
      <button
        type="button"
        className="btn"
        disabled={busy}
        onClick={() => inputRef.current?.click()}
      >
        <Upload className="h-4 w-4" aria-hidden />
        {t("ws.uploadDoc")}
      </button>
    </div>
  );
}

/* --------------------------------------------------------------- defter detayı */

function NotebookDetail({ spaceId, onBack }: { spaceId: string; onBack: () => void }) {
  const t = useT();
  const rag = useVideoRAG(spaceId);
  const [startSecond, setStartSecond] = useState(0);
  const [activeTab, setActiveTab] = useState<"transcript" | "moments">("transcript");
  const ingesting = rag.ingestJob.state === "running";
  const ready = Boolean(rag.space && rag.space.chunk_count > 0);

  // Secili kaynaga ait alintilar, sohbetin tamamindan toplaniyor: kullanici
  // bir videoya donduğunde o videoda daha once isaretlenmis anlari bulur.
  const moments = useMemo(() => {
    const seen = new Set<string>();
    return rag.messages
      .flatMap((message) => message.citations)
      .filter((citation) => citation.source_id === rag.selectedSourceId)
      .filter((citation) => {
        const key = `${citation.start_sec}-${citation.quote}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
  }, [rag.messages, rag.selectedSourceId]);

  const handleCitation = (citation: Citation) => {
    rag.setSelectedSourceId(citation.source_id);
    const second = citationSecond(citation);
    if (second !== null) setStartSecond(second);
  };

  if (rag.loading && !rag.space) {
    return (
      <p className="meta">
        <Spinner /> {t("ws.opening")}
      </p>
    );
  }

  const sources = rag.space?.sources ?? [];

  return (
    <div className="stack">
      <div className="row row--between">
        <button type="button" className="btn btn--quiet" onClick={onBack}>
          <ArrowLeft className="h-4 w-4" aria-hidden />
          {t("ws.listTitle")}
        </button>
        <AddSources
          runs={rag.runs}
          busy={ingesting}
          onAddRun={rag.addRun}
          onUpload={rag.uploadDocument}
        />
      </div>

      <h2 className="section-title" style={{ fontSize: "1.5rem" }}>
        {rag.space?.name}
      </h2>

      {rag.error && (
        <Note tone="danger" role="alert">
          {rag.error}
        </Note>
      )}

      {ingesting && (
        <div className="panel panel__pad stack stack--tight">
          <div className="row row--between">
            <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>{t("ws.ingesting")}</span>
            <span className="meta">{Math.round(rag.ingestJob.progress * 100)}%</span>
          </div>
          <Meter
            value={rag.ingestJob.progress * 100}
            variant="signal"
            label={t("ws.ingestProgress")}
          />
          <p className="hint" style={{ marginTop: 0 }}>
            {rag.ingestJob.message ?? t("ws.processing")}
          </p>
        </div>
      )}
      {rag.ingestJob.error && (
        <Note tone="danger" role="alert">
          {rag.ingestJob.error}
        </Note>
      )}

      {sources.length === 0 ? (
        <Empty icon={Upload} title={t("ws.spaceEmptyTitle")}>
            {t("ws.spaceEmptyBody")}
        </Empty>
      ) : (
        <div className="room">
          <main className="stack">
            <Player source={rag.selectedSource} startSecond={startSecond} />

            <div className="row" style={{ gap: "0.4rem", marginTop: "0.2rem" }}>
              <button
                type="button"
                className={cn("btn", activeTab === "transcript" ? "btn--primary" : "btn--quiet")}
                onClick={() => setActiveTab("transcript")}
              >
                <FileText className="h-4 w-4" aria-hidden />
                {rag.selectedSource?.kind === "video" ? "Transkript" : "Metin"}
              </button>
              <button
                type="button"
                className={cn("btn", activeTab === "moments" ? "btn--primary" : "btn--quiet")}
                onClick={() => setActiveTab("moments")}
              >
                <BookMarked className="h-4 w-4" aria-hidden />
                {t("ws.marks")} ({moments.length})
              </button>
            </div>

            {activeTab === "transcript" ? (
              <SourceTranscriptViewer
                source={rag.selectedSource}
                sourceText={rag.sourceText}
                loading={rag.sourceTextLoading}
                activeSecond={startSecond}
                onSeek={(second) => setStartSecond(second)}
                onTranscribe={(sourceId) => void rag.transcribeSource(sourceId)}
                transcribing={ingesting}
              />
            ) : (
              <Moments
                citations={moments}
                activeSecond={startSecond}
                onSeek={(second) => setStartSecond(second)}
              />
            )}

            <div className="panel panel__pad">
              <h3 className="section-title" style={{ fontSize: "1rem", marginBottom: "0.7rem" }}>
                Kaynaklar
              </h3>
              <div className="sources">
                {sources.map((source) => {
                  const status = STATUS[source.status];
                  const active = source.source_id === rag.selectedSourceId;
                  // ANLAMSAL ARAMA BOSLUGU. Gomme saglayicisi arizalandiginda
                  // is DUSMUYOR -- leksik arama calisiyor ve kaynak `indexed`
                  // kaliyor. Dogru karar, ama arayuz farki gostermezse defter
                  // tamamen saglikli gorunuyor ve sonraki "bulamadim" hata gibi
                  // okunuyordu. Eksiklik daha once yalnizca ingest bittiginde,
                  // akipticen bir mesajda soyleniyordu.
                  const unembedded =
                    source.status === "indexed"
                      ? source.chunk_count - source.embedded_chunk_count
                      : 0;
                  return (
                    // KENAR BANDI. Rengin OGRENILDIGI yer burasi: kullanici
                    // kaynagi adiyla burada goruyor, sonra alintida ve
                    // isaretli anlarda ayni rengi okumadan taniyor.
                    <div
                      key={source.source_id}
                      className={cn("source", active && "source--active")}
                      style={roomStyle(source.source_id)}
                    >
                      <span className="source__icon" aria-hidden>
                        {source.kind === "video" ? (
                          <Video className="h-4 w-4" />
                        ) : (
                          <FileText className="h-4 w-4" />
                        )}
                      </span>
                      <button
                        type="button"
                        className="min-w-0 text-left"
                        onClick={() => {
                          rag.setSelectedSourceId(source.source_id);
                          setStartSecond(0);
                        }}
                        aria-pressed={active}
                      >
                        <span className="source__title">{source.title}</span>
                        <span className="source__status">
                          {t(status.label)}
                          {source.status === "indexed" && t("ws.sections", { n: source.chunk_count })}
                          {source.error && ` · ${source.error}`}
                        </span>
                        {unembedded > 0 && (
                          <span
                            className="source__gap"
                            title={t("ws.gapBody")}
                          >
                            {unembedded === source.chunk_count
                              ? t("ws.gapTitle")
                              : t("ws.gapPartial", { n: unembedded })}
                          </span>
                        )}
                      </button>
                      <button
                        type="button"
                        className="icon-btn icon-btn--danger"
                        onClick={() => rag.deleteSource(source.source_id)}
                        aria-label={t("ws.removeSource", { name: source.title })}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          </main>

          <Chat
            messages={rag.messages}
            asking={rag.asking}
            ready={ready}
            onAsk={rag.ask}
            onCitationClick={handleCitation}
          />
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------- dış kabuk */

export function SpaceWorkspace() {
  const playlist = usePlaylist();
  const [selectedSpaceId, setSelectedSpaceId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  const selectedSpace = useMemo(
    () => playlist.spaces.find((space) => space.space_id === selectedSpaceId) ?? null,
    [playlist.spaces, selectedSpaceId],
  );

  const createSpace = async (name: string) => {
    const space = await playlist.createSpace(name);
    setSelectedSpaceId(space.space_id);
  };

  return (
    <Tooltip.Provider delayDuration={200}>
      <CreateNotebookDialog open={createOpen} onOpenChange={setCreateOpen} onCreate={createSpace} />

      {selectedSpaceId && selectedSpace ? (
        <NotebookDetail spaceId={selectedSpaceId} onBack={() => setSelectedSpaceId(null)} />
      ) : (
        <div className="stack">
          {playlist.error && (
            <Note tone="danger" role="alert">
              {playlist.error}
            </Note>
          )}
          <NotebookList
            spaces={playlist.spaces}
            runs={playlist.runs}
            loading={playlist.loading}
            onOpen={setSelectedSpaceId}
            onCreateClick={() => setCreateOpen(true)}
            onDelete={(spaceId) => void playlist.deleteSpace(spaceId)}
          />
        </div>
      )}
    </Tooltip.Provider>
  );
}
