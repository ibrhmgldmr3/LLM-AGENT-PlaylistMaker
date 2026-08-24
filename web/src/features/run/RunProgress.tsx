import { Check } from "lucide-react";
import { cn } from "../../lib/cn";

interface Props {
  progress: number;
  stage: string | null;
  message: string | null;
  onCancel?: () => void;
}

/**
 * Asamalar kullanicinin dilinde ve SIRAYLA gosteriliyor.
 *
 * Eskiden tek bir asama adi ve bir yuzde vardi; bekleyen kisi ne kadar
 * kaldigini goremiyordu. Guzergah gorunumu "neredeyim / daha ne var"
 * sorusunu, hicbir ek veri gerektirmeden yanitliyor.
 *
 * `transcript_enrichment` ve `study_notes` opsiyonel: kullanici acmadiysa
 * sunucu bu asamalari atliyor ve sirada onlardan sonraki bir asama geliyor.
 * Atlanmis asamalar "tamamlandi" olarak isaretleniyor -- yaptigi is yok ama
 * GERIDE kaldilar ve "bekliyor" gostermek yanlis olurdu.
 */
const STEPS: { id: string; label: string }[] = [
  { id: "topic_planning", label: "Konu alt başlıklara ayrılıyor" },
  { id: "candidate_search", label: "Her başlık için videolar aranıyor" },
  { id: "metadata_ranking", label: "En uygun videolar seçiliyor" },
  { id: "transcript_enrichment", label: "Transkriptler okunuyor" },
  { id: "study_notes", label: "Çalışma notları yazılıyor" },
  { id: "final_playlist_assembly", label: "Ders planı sıraya diziliyor" },
];

export function RunProgress({ progress, stage, message, onCancel }: Props) {
  const percent = Math.round(progress * 100);
  const known = STEPS.findIndex((step) => step.id === stage);
  // Bilinmeyen bir asama adi geldiginde (sunucu yeni bir asama ekledi)
  // guzergah bos kalmiyor: ham ad ek bir adim olarak gosteriliyor.
  const extra = stage && known === -1 && stage !== "done" ? stage : null;
  const activeIndex = stage === "done" ? STEPS.length : known;

  return (
    <section className="panel panel--board panel__pad" aria-live="polite">
      <div className="row row--between" style={{ marginBottom: "1.1rem" }}>
        <div>
          <h2 className="section-title" style={{ color: "var(--chalk)" }}>
            Ders planın hazırlanıyor
          </h2>
          <p style={{ color: "var(--chalk-2)", fontSize: "0.88rem", marginTop: "0.25rem" }}>
            Bu genelde bir iki dakika sürer. Sekmeyi açık bırakman yeterli.
          </p>
        </div>
        <div className="row" style={{ gap: "0.6rem" }}>
          <span className="tag tag--chalk" style={{ fontVariantNumeric: "tabular-nums" }}>
            %{percent}
          </span>
          {onCancel && (
            <button type="button" className="btn btn--chalk" onClick={onCancel}>
              Vazgeç
            </button>
          )}
        </div>
      </div>

      <div
        className="meter meter--chalk"
        role="progressbar"
        aria-label="Hazırlık ilerlemesi"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        style={{ marginBottom: "1.3rem" }}
      >
        <div className="meter__fill" style={{ transform: `scaleX(${percent / 100})` }} />
      </div>

      <ol className="route">
        {STEPS.map((step, index) => {
          const done = activeIndex > index;
          const now = activeIndex === index;
          return (
            <li
              key={step.id}
              className={cn(
                "route__step",
                done && "route__step--done",
                now && "route__step--now",
              )}
            >
              <span className="route__dot" aria-hidden>
                {done ? <Check className="h-3 w-3" /> : index + 1}
              </span>
              <div>
                <p className="route__label">
                  {step.label}
                  {done && <span className="sr-only"> — tamamlandı</span>}
                  {now && <span className="sr-only"> — şu an</span>}
                </p>
                {now && message && <p className="route__msg">{message}</p>}
              </div>
            </li>
          );
        })}

        {extra && (
          <li className="route__step route__step--now">
            <span className="route__dot" aria-hidden>
              {STEPS.length + 1}
            </span>
            <div>
              <p className="route__label">{extra}</p>
              {message && <p className="route__msg">{message}</p>}
            </div>
          </li>
        )}
      </ol>
    </section>
  );
}
