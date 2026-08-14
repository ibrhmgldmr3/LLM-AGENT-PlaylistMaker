import { api } from "../../api/client";
import { PublishPanel } from "../publish/PublishPanel";
import type { PlaylistResult, Recommendation, SubtopicResult } from "../../api/types";

/** Bu esigin altindaki oneriler "zayif eslesme" olarak isaretlenir (Streamlit ile ayni). */
const WEAK_MATCH_THRESHOLD = 7;

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

function RecommendationCard({ item }: { item: Recommendation }) {
  const weak = item.confidence_score < WEAK_MATCH_THRESHOLD;
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
        <PublishPanel runId={result.run_id} publishedUrl={result.published_playlist_url} />
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
        result.recommendations.map((item) => <RecommendationCard key={item.video.video_id} item={item} />)
      )}

      <h2 style={{ fontSize: "1.1rem", marginTop: "2rem" }}>Alt konu tanılaması</h2>
      {result.subtopics.map((item) => (
        <SubtopicRow key={item.subtopic.normalized_title} item={item} />
      ))}
    </section>
  );
}
