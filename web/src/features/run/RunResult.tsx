import {
  ArrowUpRight,
  Check,
  Download,
  FileJson,
  ListChecks,
  NotebookPen,
  ScrollText,
  Trophy,
  Video,
} from "lucide-react";
import { api } from "../../api/client";
import { PublishPanel } from "../publish/PublishPanel";
import { Fold, Meter, Note } from "../../components/ui";
import { useLanguage, useT } from "../../i18n";
import type { Dict } from "../../i18n/dict";
import { cn } from "../../lib/cn";
import { formatCount, formatDuration, formatTotalDuration } from "../../lib/format";
import { useProgress } from "../../hooks/useProgress";
import type { PlaylistResult, Recommendation, StudyNote, SubtopicResult } from "../../api/types";

/**
 * "Zayif eslesme" iki ayri kosulun BIRLESIMI.
 *
 * Once yalnizca `confidence_score < 7` bakiliyordu ve bu yanlis soruyu
 * yanitliyordu: guven puani TOPLAM puandan turuyor, toplam ise sure/dil/kanal
 * gibi alt konuyla ilgisiz sinyalleri de iceriyor. Sonuc: alt basliktan hicbir
 * kelime eslesmeyen bir video, uzun/guncel/populer oldugu icin 8.6 guven alip
 * kullaniciya UYARISIZ gosterilebiliyordu.
 *
 * Kayitli 13 kosuda olculdu: baslik alakasi ~0 olan 6 secimin 3'u eski kuralla
 * hic isaretlenmiyordu. Alaka esigi eklenince 42 secimin 3'u yerine 6'si
 * isaretleniyor -- yani kacirilanlar yakalaniyor, geri kalan sel gibi
 * etiketlenmiyor.
 *
 * Yanlis pozitif ucuz (yalnizca bir uyari), yanlis negatif pahali (alakasiz
 * video guvenilir gorunuyor); esikler bilerek o yone egimli.
 */
const WEAK_CONFIDENCE = 7;
const WEAK_TITLE_RELEVANCE = 0.5;

export function isWeakMatch(item: Recommendation): boolean {
  return (
    item.confidence_score < WEAK_CONFIDENCE ||
    item.metadata_score.title_relevance < WEAK_TITLE_RELEVANCE
  );
}

/**
 * Transkript durumu kullanicinin ise yarayacak sekilde: "var mi, yok mu ve
 * bu benim icin ne anlama geliyor". Ham degerler (`failed_temporary`) bir
 * gelistirici terimi; ekranda karsiligi yoksa gizlemek, yanlis bir sey
 * gostermekten iyidir.
 */
const TRANSCRIPT_LABEL: Record<string, keyof Dict> = {
  available: "transcript.available",
  unavailable: "transcript.unavailable",
  cooldown: "transcript.cooldown",
  failed_temporary: "transcript.cooldown",
  failed_permanent: "transcript.failed_permanent",
};

const SCORE_LABEL: Record<string, keyof Dict> = {
  total: "score.total",
  title_relevance: "score.title_relevance",
  description_relevance: "score.description_relevance",
  channel_quality: "score.channel_quality",
  duration_fit: "score.duration_fit",
  difficulty_fit: "score.difficulty_fit",
  language_match: "score.language_match",
  freshness: "score.freshness",
  engagement: "score.engagement",
};

function ScoreBreakdown({ item }: { item: Recommendation }) {
  const t = useT();
  const rows = Object.entries(item.metadata_score)
    .filter(([key, value]) => key !== "rationale" && key !== "total" && typeof value === "number")
    .map(([key, value]) => [key, value as number] as const)
    .sort((a, b) => b[1] - a[1]);
  const peak = rows.reduce((max, [, value]) => Math.max(max, value), 0);
  const rationale = item.metadata_score.rationale ?? [];

  return (
    <>
      <p className="hint" style={{ marginTop: 0, marginBottom: "0.85rem" }}>
        {t("result.scoreRationale", { score: item.confidence_score.toFixed(1) })}
      </p>
      <div className="scores">
        {rows.map(([key, value]) => (
          <div className="score" key={key}>
            <span className="score__name">{SCORE_LABEL[key] ?? key}</span>
            <span className="score__val">{value.toFixed(2)}</span>
            <span className="score__bar">
              <span style={{ width: peak > 0 ? `${(value / peak) * 100}%` : "0%" }} />
            </span>
          </div>
        ))}
      </div>
      {rationale.length > 0 && (
        <ul className="prose" style={{ marginTop: "1rem", fontSize: "0.9rem" }}>
          {rationale.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
    </>
  );
}

function StudyNoteBody({ note }: { note: StudyNote }) {
  const t = useT();
  if (note.status === "no_transcript") {
    return (
      <p className="hint" style={{ margin: 0 }}>
        {t("note.noTranscript")}
      </p>
    );
  }
  if (note.status === "failed") {
    return (
      <Note tone="danger" title={t("note.failed")}>
        {note.error ?? t("note.unknownError")}
      </Note>
    );
  }
  const isAsr = note.transcript_source === "asr";
  return (
    <>
      <div className="row row--between" style={{ alignItems: "center", marginBottom: "0.4rem" }}>
        <p className="hint" style={{ margin: 0 }}>
          {t("note.fromTranscript")}
        </p>
        {isAsr && (
          <span className="tag tag--chalk" title={note.transcript_backend ?? "Whisper ASR"}>
            ASR Transkripti
          </span>
        )}
      </div>
      <div className="prose" style={{ whiteSpace: "pre-wrap" }}>
        {note.content}
      </div>
    </>
  );
}

function Unit({
  item,
  index,
  note,
  done,
  onToggle,
}: {
  item: Recommendation;
  index: number;
  note?: StudyNote;
  done: boolean;
  onToggle: () => void;
}) {
  const { t, lang } = useLanguage();
  const weak = isWeakMatch(item);
  const duration = formatDuration(item.video.duration_sec, t);
  const subscribers = formatCount(item.video.subscriber_count, lang);
  const transcript = TRANSCRIPT_LABEL[item.transcript_status];

  return (
    <article
      className={cn("unit", done && "unit--done")}
      // Uniteler siralı bir guzergah: hepsi ayni anda degil, sırayla beliriyor.
      // Gecikme 8. uniteden sonra sabitleniyor; uzun planlarda son unitelerin
      // bir saniye sonra gelmesi beklemeye donusurdu.
      style={{ animationDelay: `${Math.min(index, 7) * 45}ms` }}
    >
      <button
        type="button"
        className="unit__num"
        onClick={onToggle}
        aria-pressed={done}
        aria-label={
          done
            ? t("result.unmarkAria", { n: index + 1 })
            : t("result.markAria", { n: index + 1 })
        }
        title={done ? t("result.unmark") : t("result.markWatched")}
      >
        {done ? <Check className="h-4 w-4" aria-hidden /> : index + 1}
      </button>

      <div className="min-w-0">
        <div className="unit__head">
          <div className="min-w-0">
            {/* Basligin kendisi ALT KONU: ders planinda ogrenilecek sey odur,
                video yalnizca o dersin malzemesi. Eskiden video basligi one
                cikiyor, plan bir arama sonucu listesi gibi okunuyordu. */}
            <h3 className="unit__title">{item.subtopic}</h3>
            <p className="unit__meta">
              <Video className="h-3.5 w-3.5" aria-hidden />
              <span className="truncate">{item.video.title}</span>
            </p>
            <p className="unit__meta">
              {item.video.channel && <span>{item.video.channel}</span>}
              {item.video.channel && subscribers && <span className="dot" aria-hidden />}
              {subscribers && <span>{subscribers} abone</span>}
              {duration && <span className="dot" aria-hidden />}
              {duration && <span>{duration}</span>}
              {transcript && <span className="dot" aria-hidden />}
              {transcript && <span>{transcript}</span>}
            </p>
          </div>
          {weak && <span className="tag tag--warn">{t("result.weakMatch")}</span>}
        </div>

        {item.why_selected && <p className="unit__why">{item.why_selected}</p>}

        <div className="unit__actions">
          <a
            className="btn btn--primary"
            href={item.video.url}
            target="_blank"
            rel="noreferrer"
          >
            Dersi izle
            <ArrowUpRight className="h-4 w-4" aria-hidden />
          </a>
          <button type="button" className="btn btn--quiet" onClick={onToggle}>
            {done ? t("result.unmark") : t("result.watched")}
          </button>
        </div>
      </div>

      <div className="unit__extra">
        {weak && (
          <Note tone="warn" title={t("result.noCandidate")}>
          {t("result.weakHint")}
          </Note>
        )}
        {note && (
          <Fold
            summary={t("result.studyNote")}
            icon={NotebookPen}
            open={note.status === "available"}
            className={weak ? "mt-2" : undefined}
          >
            <StudyNoteBody note={note} />
          </Fold>
        )}
        <Fold summary={t("result.whyThis")} icon={ListChecks}>
          <ScoreBreakdown item={item} />
        </Fold>
      </div>
    </article>
  );
}

function SubtopicDiagnostics({ item }: { item: SubtopicResult }) {
  const t = useT();
  return (
    <div style={{ marginBottom: "1.25rem" }}>
      <h4 style={{ fontSize: "0.92rem" }}>{item.subtopic.title}</h4>
      <p className="meta" style={{ marginTop: "0.2rem" }}>
        {t("result.candidatesConsidered", { n: item.candidates_considered })}
        {item.selected_video_id ? t("result.selectedOne") : t("result.selectedNone")}
      </p>
      <p className="hint">
        {t("result.searchQuery")} <code>{item.query}</code>
      </p>
      {item.shortlisted_candidates.length > 0 && (
        <ol className="stack stack--tight" style={{ marginTop: "0.6rem" }}>
          {item.shortlisted_candidates.map((candidate) => (
            <li key={candidate.video_id} className="row row--between" style={{ gap: "1rem" }}>
              <span style={{ fontSize: "0.86rem" }}>
                {candidate.title}
                {candidate.channel && <span className="muted"> · {candidate.channel}</span>}
              </span>
              <span className="score__val">{candidate.metadata_score.toFixed(2)}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

export function RunResult({ result }: { result: PlaylistResult }) {
  const t = useT();
  const total = result.recommendations.length;
  const { completed, toggle } = useProgress(result.run_id, total);
  const doneCount = result.recommendations.filter((item) =>
    completed.includes(item.video.video_id),
  ).length;
  const totalTime = formatTotalDuration(
    result.recommendations.map((item) => item.video.duration_sec),
    t,
  );
  const remaining = total - doneCount;
  // Yalnizca GERCEKTEN uretilmis notlar sayiliyor: transkripti olmadigi icin
  // uretilmeyen ya da hata alan notlari saymak, olmayan bir seyi vaat ederdi.
  const noteCount = result.study_notes.filter((note) => note.status === "available").length;

  return (
    <section className="stack stack--loose">
      {result.warnings.map((warning) => (
        <Note tone="warn" key={warning}>
          {warning}
        </Note>
      ))}

      <header className="course__head">
        <div className="min-w-0">
          <h2 className="course__title">{result.topic}</h2>
          <div className="course__facts">
            <span className="tag tag--chalk">{t("result.lessons", { n: total })}</span>
            {totalTime && <span className="tag tag--chalk">{totalTime}</span>}
            {noteCount > 0 && <span className="tag tag--chalk">{t("result.notes", { n: noteCount })}</span>}
          </div>
        </div>

        {total > 0 && (
          <div className="course__progress">
            <div className="course__progress-top">
              <span className="course__progress-label">{t("result.progress")}</span>
              <span className="course__progress-count">
                {doneCount} / {total}
              </span>
            </div>
            <Meter
              value={doneCount}
              max={total}
              variant="chalk"
              label={t("result.meterLabel", { total, done: doneCount })}
            />
            <p className="course__progress-label" style={{ marginTop: "0.5rem" }}>
              {remaining === 0 ? (
                <>
                  <Trophy className="mr-1 inline h-3.5 w-3.5" aria-hidden />
                  {t("result.allDone")}
                </>
              ) : (
                t("result.remaining", { n: remaining })
              )}
            </p>
          </div>
        )}
      </header>

      {total === 0 ? (
        <Note tone="warn" title={t("result.noPlanTitle")}>
          {t("result.noPlanBody")}
        </Note>
      ) : (
        <>
          {/* `key` calistirma kimligi: `PublishPanel` kendi hata/bildirim state'ini
              tutuyor ve React ayni konumdaki bileseni YENIDEN KULLANDIGI icin
              gecmisten acilan A calistirmasinin yayinlama hatasi, ardindan gecilen
              B calistirmasinin ekraninda asili kaliyordu. Farkli `key` = yeni
              bilesen ornegi = sifirlanmis state. */}
          <PublishPanel
            key={result.run_id}
            runId={result.run_id}
            publishedUrl={result.published_playlist_url}
          />

          <div className="units">
            {result.recommendations.map((item, index) => (
              <Unit
                key={item.video.video_id}
                item={item}
                index={index}
                done={completed.includes(item.video.video_id)}
                onToggle={() => toggle(item.video.video_id)}
                note={result.study_notes.find(
                  (candidate) => candidate.video_id === item.video.video_id,
                )}
              />
            ))}
          </div>
        </>
      )}

      <div className="stack stack--tight">
        <div className="row">
          <a className="btn" href={api.exportUrl(result.run_id, "markdown")}>
            <Download className="h-4 w-4" aria-hidden />
            {t("result.downloadMd")}
          </a>
          <a className="btn" href={api.exportUrl(result.run_id, "json")}>
            <FileJson className="h-4 w-4" aria-hidden />
            {t("result.downloadJson")}
          </a>
        </div>

        {result.subtopics.length > 0 && (
          <Fold summary={t("result.howBuilt")} icon={ScrollText}>
            <p className="hint" style={{ marginTop: 0, marginBottom: "1rem" }}>
              {t("result.howBuiltBody")}
            </p>
            {result.subtopics.map((item) => (
              <SubtopicDiagnostics key={item.subtopic.normalized_title} item={item} />
            ))}
          </Fold>
        )}
      </div>
    </section>
  );
}
