import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../../api/client";
import type { SpaceSummary } from "../../api/types";

export function SpaceList({ onOpen }: { onOpen: (spaceId: string) => void }) {
  const [items, setItems] = useState<SpaceSummary[]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const load = useCallback(() => {
    api
      .listSpaces()
      .then((body) => {
        setItems(body.items);
        setError(null);
      })
      .catch((exception: ApiError) => setError(exception.message));
  }, []);

  useEffect(load, [load]);

  const create = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || creating) return;
    setCreating(true);
    setError(null);
    api
      .createSpace(trimmed)
      .then((space) => {
        setName("");
        load();
        onOpen(space.space_id);
      })
      .catch((exception: ApiError) => setError(exception.message))
      .finally(() => setCreating(false));
  };

  const remove = (spaceId: string) => {
    api
      .deleteSpace(spaceId)
      .then(load)
      .catch((exception: ApiError) => setError(exception.message));
  };

  return (
    <section>
      <div className="card">
        <h3>Yeni öğrenme alanı</h3>
        <p className="muted" style={{ marginTop: 0 }}>
          Bir alan, birden çok playlist'in videolarını ve yüklediğiniz dokümanları tek bir
          aranabilir havuzda toplar.
        </p>
        <form onSubmit={create} className="row" style={{ gap: "0.5rem" }}>
          <input
            type="text"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Örn. Kalman filtresi çalışmam"
            aria-label="Alan adı"
            style={{ flex: 1 }}
          />
          <button type="submit" disabled={creating || !name.trim()}>
            Oluştur
          </button>
        </form>
      </div>

      {error && <p className="alert alert--error">{error}</p>}

      {items.length === 0 ? (
        <p className="muted">Henüz öğrenme alanı yok.</p>
      ) : (
        items.map((space) => (
          <div className="card" key={space.space_id}>
            <div className="card__head">
              <div>
                <h3>{space.name}</h3>
                <p className="muted" style={{ margin: 0 }}>
                  {space.source_count} kaynak · {space.chunk_count} parça ·{" "}
                  {new Date(space.updated_at).toLocaleString("tr-TR")}
                </p>
              </div>
              <div className="row">
                <button className="ghost" onClick={() => onOpen(space.space_id)}>
                  Aç
                </button>
                <button className="ghost" onClick={() => remove(space.space_id)}>
                  Sil
                </button>
              </div>
            </div>
          </div>
        ))
      )}
    </section>
  );
}
