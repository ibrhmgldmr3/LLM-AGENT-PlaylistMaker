import { useState } from "react";
import { api, ApiError } from "../../api/client";
import type { Citation, RagAnswer } from "../../api/types";

/** Saniyeyi `12:34` / `1:02:03` bicimine cevirir. */
function formatTimestamp(seconds: number): string {
  const total = Math.floor(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const pad = (value: number) => String(value).padStart(2, "0");
  return hours ? `${hours}:${pad(minutes)}:${pad(secs)}` : `${minutes}:${pad(secs)}`;
}

function locationLabel(citation: Citation): string {
  if (citation.start_sec !== null) return formatTimestamp(citation.start_sec);
  if (citation.page !== null) return `s. ${citation.page}`;
  return "";
}

function CitationChip({ citation }: { citation: Citation }) {
  const where = locationLabel(citation);
  const label = where ? `${citation.title} · ${where}` : citation.title;

  // Baglantisi olmayan kaynak (dokuman) DUGME DEGIL: tiklanacak bir yer yok ve
  // tiklanabilir gorunmesi bos bir vaat olurdu.
  const body = citation.url ? (
    <a href={citation.url} target="_blank" rel="noreferrer" className="chip__link">
      {label}
    </a>
  ) : (
    <span className="chip__link">{label}</span>
  );

  return (
    <li className="chip" title={citation.quote}>
      {body}
      <span className="chip__quote">{citation.quote}</span>
    </li>
  );
}

export function ChatPanel({ spaceId, ready }: { spaceId: string; ready: boolean }) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<RagAnswer | null>(null);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ask = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = question.trim();
    if (!trimmed || asking) return;
    setAsking(true);
    setError(null);
    setAnswer(null);
    api
      .ask(spaceId, trimmed)
      .then(setAnswer)
      .catch((exception: ApiError) => setError(exception.message))
      .finally(() => setAsking(false));
  };

  return (
    <section className="card">
      <h3>Soru sor</h3>
      <p className="muted" style={{ marginTop: 0 }}>
        Yanıt yalnızca bu alandaki videolara ve dokümanlara dayanır. Kaynaklarda yoksa
        uydurulmaz.
      </p>

      <form onSubmit={ask} className="row" style={{ gap: "0.5rem" }}>
        <input
          type="text"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Anlamadığınız noktayı yazın…"
          aria-label="Soru"
          disabled={!ready}
          style={{ flex: 1 }}
        />
        <button type="submit" disabled={!ready || asking || !question.trim()}>
          {asking ? "Aranıyor…" : "Sor"}
        </button>
      </form>

      {!ready && (
        <p className="muted">
          Önce bir çalıştırma ekleyin ya da doküman yükleyin; aranabilir içerik olmadan
          soru sorulamaz.
        </p>
      )}

      {error && <p className="alert alert--error">{error}</p>}

      {answer?.answered && (
        <div className="answer">
          <p className="answer__text">{answer.answer}</p>
          <h4>Kaynaklar</h4>
          <ul className="chips">
            {answer.citations.map((citation, index) => (
              <CitationChip key={`${citation.source_id}-${index}`} citation={citation} />
            ))}
          </ul>
        </div>
      )}

      {answer && !answer.answered && (
        // NOTR kutu, hata kutusu DEGIL. "Bulamadım" bu özelliğin VAADI: cevabı
        // olmayan soruya cevap uydurmamak. Kırmızı bir hata kutusunda göstermek
        // kullanıcıya sistemin bozulduğunu düşündürürdü.
        <p className="alert" role="status">
          {answer.reason ?? "Bu soruyu karşılayan bir bölüm bulunamadı."}
        </p>
      )}
    </section>
  );
}
