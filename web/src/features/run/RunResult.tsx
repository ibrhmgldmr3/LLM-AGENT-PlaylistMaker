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
const TRANSCRIPT_LABEL: Record<string, string> = {
  available: "Transkript var",
  unavailable: "Transkript yok",
  cooldown: "Transkript şimdilik alınamadı",
  failed_temporary: "Transkript şimdilik alınamadı",
  failed_permanent: "Transkript alınamıyor",
};

const SCORE_LABEL: Record<string, string> = {
  total: "Toplam",
  title_relevance: "Başlık uyumu",
  description_relevance: "Açıklama uyumu",
  channel_quality: "Kanal güvenilirliği",
  duration_fit: "Süre uygunluğu",
  difficulty_fit: "Seviye uygunluğu",
  language_match: "Dil uyumu",
  freshness: "Güncellik",
  engagement: "İzlenme ve etkileşim",
};

function ScoreBreakdown({ item }: { item: Recommendation }) {
  const rows = Object.entries(item.metadata_score)
    .filter(([key, value]) => key !== "rationale" && key !== "total" && typeof value === "number")
    .map(([key, value]) => [key, value as number] as const)
    .sort((a, b) => b[1] - a[1]);
  const peak = rows.reduce((max, [, value]) => Math.max(max, value), 0);
  const rationale = item.metadata_score.rationale ?? [];

  return (
    <>
      <p className="hint" style={{ marginTop: 0, marginBottom: "0.85rem" }}>
        Bu video {item.confidence_score.toFixed(1)}/10 güven puanıyla seçildi. Çubuklar, tek
        tek ölçütlerin bu seçim içindeki göreli ağırlığını gösterir.
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
  if (note.status === "no_transcript") {
    return (
      <p className="hint" style={{ margin: 0 }}>
        Bu videonun transkripti bulunamadı, o yüzden çalışma notu üretilmedi. Uydurulmuş bir
        özet göstermektense boş bırakıyoruz.
      </p>
    );
  }
  if (note.status === "failed") {
    return (
      <Note tone="danger" title="Çalışma notu üretilemedi">
        {note.error ?? "Bilinmeyen bir hata oluştu."}
      </Note>
    );
  }
  const isAsr = note.transcript_source === "asr";
  return (
    <>
      <div className="row row--between" style={{ alignItems: "center", marginBottom: "0.4rem" }}>
        <p className="hint" style={{ margin: 0 }}>
          Videonun transkriptinden üretildi — özet niteliğindedir, videonun kendisiyle doğrula.
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
  const weak = isWeakMatch(item);
  const duration = formatDuration(item.video.duration_sec);
  const subscribers = formatCount(item.video.subscriber_count);
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
          done ? `${index + 1}. dersin işaretini kaldır` : `${index + 1}. dersi izledim olarak işaretle`
        }
        title={done ? "İşareti kaldır" : "İzledim olarak işaretle"}
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
          {weak && <span className="tag tag--warn">Zayıf eşleşme</span>}
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
            {done ? "İşareti kaldır" : "İzledim"}
          </button>
        </div>
      </div>

      <div className="unit__extra">
        {weak && (
          <Note tone="warn" title="Bu başlık için iyi bir aday bulunamadı">
            Konuyu biraz daraltmayı ya da tercihlerden İngilizce içeriği açmayı deneyebilirsin.
          </Note>
        )}
        {note && (
          <Fold
            summary="Çalışma notu"
            icon={NotebookPen}
            open={note.status === "available"}
            className={weak ? "mt-2" : undefined}
          >
            <StudyNoteBody note={note} />
          </Fold>
        )}
        <Fold summary="Neden bu video seçildi?" icon={ListChecks}>
          <ScoreBreakdown item={item} />
        </Fold>
      </div>
    </article>
  );
}

function SubtopicDiagnostics({ item }: { item: SubtopicResult }) {
  return (
    <div style={{ marginBottom: "1.25rem" }}>
      <h4 style={{ fontSize: "0.92rem" }}>{item.subtopic.title}</h4>
      <p className="meta" style={{ marginTop: "0.2rem" }}>
        {item.candidates_considered} aday değerlendirildi ·{" "}
        {item.selected_video_id ? "bir video seçildi" : "uygun video bulunamadı"}
      </p>
      <p className="hint">
        Arama sorgusu: <code>{item.query}</code>
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
  const total = result.recommendations.length;
  const { completed, toggle } = useProgress(result.run_id, total);
  const doneCount = result.recommendations.filter((item) =>
    completed.includes(item.video.video_id),
  ).length;
  const totalTime = formatTotalDuration(result.recommendations.map((item) => item.video.duration_sec));
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
            <span className="tag tag--chalk">{total} ders</span>
            {totalTime && <span className="tag tag--chalk">{totalTime}</span>}
            {noteCount > 0 && <span className="tag tag--chalk">{noteCount} çalışma notu</span>}
          </div>
        </div>

        {total > 0 && (
          <div className="course__progress">
            <div className="course__progress-top">
              <span className="course__progress-label">İlerlemen</span>
              <span className="course__progress-count">
                {doneCount} / {total}
              </span>
            </div>
            <Meter
              value={doneCount}
              max={total}
              variant="chalk"
              label={`${total} dersin ${doneCount} tanesi tamamlandı`}
            />
            <p className="course__progress-label" style={{ marginTop: "0.5rem" }}>
              {remaining === 0 ? (
                <>
                  <Trophy className="mr-1 inline h-3.5 w-3.5" aria-hidden />
                  Hepsini bitirdin.
                </>
              ) : (
                `${remaining} ders kaldı.`
              )}
            </p>
          </div>
        )}
      </header>

      {total === 0 ? (
        <Note tone="warn" title="Bu tercihlerle ders planı çıkmadı">
          Konuyu biraz genişletmeyi, süre sınırını yükseltmeyi ya da İngilizce içeriği açmayı
          deneyebilirsin.
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
            Markdown indir
          </a>
          <a className="btn" href={api.exportUrl(result.run_id, "json")}>
            <FileJson className="h-4 w-4" aria-hidden />
            JSON indir
          </a>
        </div>

        {result.subtopics.length > 0 && (
          <Fold summary="Bu plan nasıl kuruldu?" icon={ScrollText}>
            <p className="hint" style={{ marginTop: 0, marginBottom: "1rem" }}>
              Her alt başlık için ayrı bir arama yapıldı ve adaylar puanlanarak sıralandı.
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
