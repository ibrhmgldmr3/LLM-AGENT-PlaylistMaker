import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../../api/client";
import type { RunSummary, SpaceDetail as SpaceDetailBody, SpaceSource } from "../../api/types";
import { useJobStream } from "../../hooks/useJobStream";
import { ChatPanel } from "./ChatPanel";

/**
 * Kaynak durumlarinin kullaniciya gorunen hali.
 *
 * `no_text` BILEREK "hata" degil "kapsam disi": transkripti olmayan bir video
 * ya da taranmis bir PDF gecerli bir dosyadir, yalnizca aranabilir metni
 * yoktur. Ikisini ayni gostermek kullaniciya dosyasinin bozuk oldugunu
 * dusundururdu -- ve asil onemlisi, NEYIN ARANAMAYACAGINI bilmesi gerekiyor,
 * yoksa o konuda "bulamadim" yanitini alip sistemi bozuk sanar.
 */
const STATUS_LABEL: Record<SpaceSource["status"], string> = {
  pending: "sırada",
  indexed: "indekslendi",
  no_text: "kapsam dışı — metin yok",
  failed: "başarısız",
};

function SourceRow({
  source,
  onDelete,
}: {
  source: SpaceSource;
  onDelete: (sourceId: string) => void;
}) {
  return (
    <div className="row source-row">
      <div style={{ flex: 1 }}>
        <span>{source.kind === "video" ? "🎬" : "📄"} </span>
        {source.url ? (
          <a href={source.url} target="_blank" rel="noreferrer">
            {source.title}
          </a>
        ) : (
          <span>{source.title}</span>
        )}
        <p className="muted" style={{ margin: 0 }}>
          {STATUS_LABEL[source.status]}
          {source.status === "indexed" && ` · ${source.chunk_count} parça`}
          {source.error && ` · ${source.error}`}
        </p>
      </div>
      <button className="ghost" onClick={() => onDelete(source.source_id)}>
        Kaldır
      </button>
    </div>
  );
}

export function SpaceDetail({ spaceId, onBack }: { spaceId: string; onBack: () => void }) {
  const [space, setSpace] = useState<SpaceDetailBody | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selectedRun, setSelectedRun] = useState("");
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    api
      .getSpace(spaceId)
      .then(setSpace)
      .catch((exception: ApiError) => setError(exception.message));
  }, [spaceId]);

  // Iceri alma bitince alani YENIDEN OKU: kaynak durumlari ve parca sayilari
  // arka planda degisti ve akis onlari tasimıyor (yalnizca ilerleme tasiyor).
  const job = useJobStream(load);

  useEffect(load, [load]);

  useEffect(() => {
    api
      .listRuns(50, 0)
      .then((body) => setRuns(body.items.filter((item) => item.is_complete)))
      .catch(() => setRuns([]));
  }, []);

  const addRun = () => {
    if (!selectedRun) return;
    setError(null);
    api
      .addRunToSpace(spaceId, selectedRun)
      .then((accepted) => job.watch(accepted.job_id, accepted.events_url))
      .catch((exception: ApiError) => setError(exception.message));
  };

  const upload = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setError(null);
    api
      .uploadDocument(spaceId, file)
      .then((accepted) => {
        // Kaynak satiri HEMEN gorunsun: sunucu kaydi yukleme aninda yaziyor.
        load();
        job.watch(accepted.job_id, accepted.events_url);
      })
      .catch((exception: ApiError) => setError(exception.message))
      // Ayni dosyayi tekrar secebilmek icin girdiyi sifirla; `change` olayi
      // ayni deger yeniden secildiginde tetiklenmiyor.
      .finally(() => {
        if (fileRef.current) fileRef.current.value = "";
      });
  };

  const removeSource = (sourceId: string) => {
    api
      .deleteSource(spaceId, sourceId)
      .then(load)
      .catch((exception: ApiError) => setError(exception.message));
  };

  if (!space) {
    return (
      <section>
        <button className="ghost" onClick={onBack}>
          ← Alanlar
        </button>
        {error ? <p className="alert alert--error">{error}</p> : <p className="muted">Yükleniyor…</p>}
      </section>
    );
  }

  const busy = job.state === "running";

  return (
    <section>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <button className="ghost" onClick={onBack}>
          ← Alanlar
        </button>
        <p className="muted" style={{ margin: 0 }}>
          {space.source_count} kaynak · {space.chunk_count} parça
        </p>
      </div>

      <h2>{space.name}</h2>

      {error && <p className="alert alert--error">{error}</p>}

      <div className="card">
        <h3>Kaynak ekle</h3>
        <div className="row" style={{ gap: "0.5rem", flexWrap: "wrap" }}>
          <select
            value={selectedRun}
            onChange={(event) => setSelectedRun(event.target.value)}
            aria-label="Çalıştırma"
            disabled={busy}
          >
            <option value="">Tamamlanmış bir çalıştırma seçin…</option>
            {runs.map((run) => (
              <option key={run.run_id} value={run.run_id}>
                {run.topic}
              </option>
            ))}
          </select>
          <button onClick={addRun} disabled={busy || !selectedRun}>
            Videoları ekle
          </button>

          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.docx,.txt,.md"
            onChange={upload}
            aria-label="Doküman"
            disabled={busy}
          />
        </div>

        {busy && (
          <div style={{ marginTop: "0.8rem" }}>
            {/* Mevcut `.bar` bileseni yeniden kullaniliyor (bkz. RunProgress);
                ikinci bir ilerleme cubugu stili uydurmak, ikisinin sessizce
                ayrisabilecegi bir yer daha yaratirdi. */}
            <div
              className="bar"
              role="progressbar"
              aria-valuenow={Math.round(job.progress * 100)}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div className="bar__fill" style={{ width: `${Math.round(job.progress * 100)}%` }} />
            </div>
            <p className="muted" style={{ marginBottom: 0 }}>{job.message ?? "İşleniyor…"}</p>
          </div>
        )}
        {job.state === "done" && job.message && <p className="alert">{job.message}</p>}
        {job.error && <p className="alert alert--error">{job.error}</p>}
      </div>

      <div className="card">
        <h3>Kaynaklar</h3>
        {space.sources.length === 0 ? (
          <p className="muted">Henüz kaynak yok.</p>
        ) : (
          space.sources.map((source) => (
            <SourceRow key={source.source_id} source={source} onDelete={removeSource} />
          ))
        )}
      </div>

      <ChatPanel spaceId={spaceId} ready={space.chunk_count > 0} />
    </section>
  );
}
