import { api } from "../../api/client";
import { PublishPanel } from "../publish/PublishPanel";
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

function formatDuration(seconds: number | null): string {
  if (!seconds) return "?";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return hours ? `${hours}s ${minutes}dk` : `${minutes}dk`;
}

function compact(value: number | null): string {
  if (!value) return "?";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${Math.round(value / 1_000)}K`;
  return String(value);
}

export function isWeakMatch(item: Recommendation): boolean {
  return (
    item.confidence_score < WEAK_CONFIDENCE ||
    item.metadata_score.title_relevance < WEAK_TITLE_RELEVANCE
  );
}

function RecommendationCard({ item, note }: { item: Recommendation; note?: StudyNote }) {
  const weak = isWeakMatch(item);
  return (
    <article className="card">
      <div className="card__head">
        <div>
          <h3>
            {item.position}. {item.video.title}
          </h3>
          <p className="muted" style={{ margin: 0 }}>
            {item.subtopic}
            {item.video.channel && ` · ${item.video.channel}`}
            {item.video.subscriber_count && ` · ${compact(item.video.subscriber_count)} abone`}
            {` · ${formatDuration(item.video.duration_sec)}`}
          </p>
        </div>
        <span className={weak ? "badge badge--weak" : "badge"}>
          {item.confidence_score.toFixed(2)}/10
        </span>
      </div>

      {weak && (
        <p className="alert" style={{ marginTop: "0.7rem" }}>
          ⚠️ Zayıf eşleşme — bu alt konu için havuzda iyi bir aday bulunamadı. Konuyu
          daraltmayı veya İngilizce içeriği açmayı deneyin.
        </p>
      )}

      <p style={{ marginBottom: "0.6rem" }}>{item.why_selected}</p>

      <div className="row">
        <a href={item.video.url} target="_blank" rel="noreferrer">
          Videoyu aç ↗
        </a>
        <span className="muted">Transkript: {item.transcript_status}</span>
      </div>

      {note && (
        <details style={{ marginTop: "0.7rem" }} open={note.status === "available"}>
          <summary>Çalışma notu</summary>
          {note.status === "available" && (
            <>
              <p className="muted" style={{ marginTop: "0.5rem", marginBottom: "0.4rem" }}>
                Video transkriptinden üretildi — özet niteliğindedir, videonun kendisiyle
                doğrulayın.
              </p>
              <div style={{ whiteSpace: "pre-wrap" }}>{note.content}</div>
            </>
          )}
          {note.status === "no_transcript" && (
            <p className="muted" style={{ marginTop: "0.5rem" }}>
              Bu video için transkript bulunamadığından çalışma notu üretilmedi.
            </p>
          )}
          {note.status === "failed" && (
            <p className="alert alert--error" style={{ marginTop: "0.5rem" }}>
              Çalışma notu üretilemedi: {note.error}
            </p>
          )}
        </details>
      )}

      <details style={{ marginTop: "0.7rem" }}>
        <summary>Puan dökümü</summary>
        <table>
          <tbody>
            {Object.entries(item.metadata_score)
              .filter(([key]) => key !== "rationale")
              .map(([key, value]) => (
                <tr key={key}>
                  <th>{key}</th>
                  <td>{typeof value === "number" ? value.toFixed(3) : String(value)}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </details>
    </article>
  );
}

function SubtopicRow({ item }: { item: SubtopicResult }) {
  return (
    <details className="card">
      <summary>
        <strong>{item.subtopic.title}</strong>{" "}
        <span className="muted">
          · {item.candidates_considered} aday · {item.selected_video_id ? "seçildi" : "boş"}
        </span>
      </summary>
      <p className="muted" style={{ marginTop: "0.6rem" }}>
        Sorgu: <code>{item.query}</code>
      </p>
      {item.shortlisted_candidates.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Başlık</th>
              <th>Kanal</th>
              <th>Puan</th>
            </tr>
          </thead>
          <tbody>
            {item.shortlisted_candidates.map((candidate) => (
              <tr key={candidate.video_id}>
                <td>{candidate.title}</td>
                <td>{candidate.channel ?? "—"}</td>
                <td>{candidate.metadata_score.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </details>
  );
}

export function RunResult({ result }: { result: PlaylistResult }) {
  return (
    <section>
      {result.warnings.map((warning) => (
        <p className="alert" key={warning}>
          {warning}
        </p>
      ))}

      {result.recommendations.length > 0 && (
        // `key` calistirma kimligi: `PublishPanel` kendi hata/bildirim state'ini
        // tutuyor ve React ayni konumdaki bileseni YENIDEN KULLANDIGI icin
        // gecmisten acilan A calistirmasinin yayinlama hatasi, ardindan gecilen
        // B calistirmasinin ekraninda asili kaliyordu. Farkli `key` = yeni
        // bilesen ornegi = sifirlanmis state.
        <PublishPanel
          key={result.run_id}
          runId={result.run_id}
          publishedUrl={result.published_playlist_url}
        />
      )}

      <div className="row" style={{ marginBottom: "1rem" }}>
        <a className="badge" href={api.exportUrl(result.run_id, "markdown")}>
          Markdown indir
        </a>
        <a className="badge" href={api.exportUrl(result.run_id, "json")}>
          JSON indir
        </a>
      </div>

      {result.recommendations.length === 0 ? (
        <p className="alert">Bu filtrelerle öneri üretilemedi.</p>
      ) : (
        result.recommendations.map((item) => (
          <RecommendationCard
            key={item.video.video_id}
            item={item}
            note={result.study_notes.find((candidate) => candidate.video_id === item.video.video_id)}
          />
        ))
      )}

      <h2 style={{ fontSize: "1.1rem", marginTop: "2rem" }}>Alt konu tanılaması</h2>
      {result.subtopics.map((item) => (
        <SubtopicRow key={item.subtopic.normalized_title} item={item} />
      ))}
    </section>
  );
}
