import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import type { RunSummary } from "../../api/types";

const PAGE_SIZE = 10;

export function HistoryList({ onOpen }: { onOpen: (runId: string) => void }) {
  const [items, setItems] = useState<RunSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((nextOffset: number) => {
    api
      .listRuns(PAGE_SIZE, nextOffset)
      .then((body) => {
        setItems(body.items);
        setTotal(body.total);
        setOffset(body.offset);
        setError(null);
      })
      .catch((exception: Error) => setError(exception.message));
  }, []);

  useEffect(() => load(0), [load]);

  const remove = (runId: string) => {
    api
      .deleteRun(runId)
      .then(() => load(offset))
      .catch((exception: Error) => setError(exception.message));
  };

  if (error) return <p className="alert alert--error">{error}</p>;
  if (items.length === 0) return <p className="muted">Henüz çalıştırma yok.</p>;

  return (
    <section>
      {items.map((item) => (
        <div className="card" key={item.run_id}>
          <div className="card__head">
            <div>
              <h3>{item.topic}</h3>
              <p className="muted" style={{ margin: 0 }}>
                {new Date(item.created_at).toLocaleString("tr-TR")} ·{" "}
                {item.is_complete ? "tamamlandı" : "yarım kaldı"}
              </p>
            </div>
            <div className="row">
              {item.is_complete && (
                <button className="ghost" onClick={() => onOpen(item.run_id)}>
                  Aç
                </button>
              )}
              <button className="ghost" onClick={() => remove(item.run_id)}>
                Sil
              </button>
            </div>
          </div>
        </div>
      ))}

      {total > PAGE_SIZE && (
        <div className="row" style={{ justifyContent: "center" }}>
          <button className="ghost" disabled={offset === 0} onClick={() => load(offset - PAGE_SIZE)}>
            ← Önceki
          </button>
          <span className="muted">
            {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} / {total}
          </span>
          <button
            className="ghost"
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => load(offset + PAGE_SIZE)}
          >
            Sonraki →
          </button>
        </div>
      )}
    </section>
  );
}
