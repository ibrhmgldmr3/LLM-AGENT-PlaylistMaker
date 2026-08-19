interface Props {
  progress: number;
  stage: string | null;
  message: string | null;
  onCancel?: () => void;
}

const STAGE_LABELS: Record<string, string> = {
  topic_planning: "Alt konu planlama",
  candidate_search: "Aday arama",
  metadata_ranking: "Metadata sıralama",
  transcript_enrichment: "Transkript zenginleştirme",
  final_playlist_assembly: "Playlist derleme",
  study_notes: "Çalışma notları üretiliyor",
  done: "Tamamlandı",
};

export function RunProgress({ progress, stage, message, onCancel }: Props) {
  const percent = Math.round(progress * 100);
  return (
    <div className="card" aria-live="polite">
      <div className="card__head">
        <h3>{stage ? (STAGE_LABELS[stage] ?? stage) : "Başlatılıyor"}</h3>
        <span className="badge">{percent}%</span>
        {onCancel && (
          <button type="button" className="ghost" onClick={onCancel}>
            İptal et
          </button>
        )}
      </div>
      <div className="bar" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
        <div className="bar__fill" style={{ width: `${percent}%` }} />
      </div>
      {message && <p className="muted" style={{ marginBottom: 0 }}>{message}</p>}
    </div>
  );
}
