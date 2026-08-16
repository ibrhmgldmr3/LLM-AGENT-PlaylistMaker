import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../../api/client";
import type { CredentialItem } from "../../api/client";

/**
 * Kullanicinin kendi API anahtarlari (BYOK).
 *
 * Girilen deger BIR DAHA OKUNAMAZ: sunucu yalnizca "girilmis mi" bilgisini
 * donduruyor, degerin kendisini degil. Bu yuzden alanlar mevcut degeri
 * gostermiyor; kullanici degistirmek isterse yenisini yaziyor.
 */
export function CredentialsPanel({ onChange }: { onChange?: () => void }) {
  const [items, setItems] = useState<CredentialItem[] | null>(null);
  const [editable, setEditable] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .credentials()
      .then((body) => {
        setItems(body.items);
        setEditable(body.editable);
      })
      .catch((exc: ApiError) => setError(exc.message));
  }, []);

  useEffect(load, [load]);

  const save = (name: string) => {
    const value = (drafts[name] ?? "").trim();
    if (!value) return;
    setSaving(name);
    setError(null);
    api
      .saveCredential(name, value)
      .then(() => {
        setDrafts((previous) => ({ ...previous, [name]: "" }));
        load();
        onChange?.();
      })
      .catch((exc: ApiError) => setError(exc.message))
      .finally(() => setSaving(null));
  };

  const remove = (name: string) => {
    setError(null);
    api
      .deleteCredential(name)
      .then(() => {
        load();
        onChange?.();
      })
      .catch((exc: ApiError) => setError(exc.message));
  };

  if (!items) return null;

  return (
    <div className="card">
      <div className="card__head">
        <h3>API anahtarlarınız</h3>
      </div>

      {!editable ? (
        <p className="muted">
          Bu kurulum tek kullanıcılı; anahtarlar <code>.env</code> dosyasından okunuyor.
        </p>
      ) : (
        <p className="muted">
          Çalıştırmalar sizin anahtarlarınızla yapılır. Girilen değer bir daha
          görüntülenemez — değiştirmek isterseniz yenisini yazın.
        </p>
      )}

      {error && <p className="alert alert--error">{error}</p>}

      {items.map((item) => (
        <div key={item.name} style={{ marginTop: "0.8rem" }}>
          <label htmlFor={item.name}>
            {item.label}
            {item.required ? " (zorunlu)" : " (isteğe bağlı)"}
            {item.configured && <span className="badge" style={{ marginLeft: "0.5rem" }}>girildi</span>}
          </label>
          {editable && (
            <div className="row" style={{ gap: "0.5rem" }}>
              <input
                id={item.name}
                type="password"
                autoComplete="off"
                placeholder={item.configured ? "Değiştirmek için yeni değer" : "Anahtarı yapıştırın"}
                value={drafts[item.name] ?? ""}
                onChange={(event) =>
                  setDrafts((previous) => ({ ...previous, [item.name]: event.target.value }))
                }
              />
              <button
                type="button"
                onClick={() => save(item.name)}
                disabled={saving === item.name || !(drafts[item.name] ?? "").trim()}
              >
                Kaydet
              </button>
              {item.configured && (
                <button type="button" className="ghost" onClick={() => remove(item.name)}>
                  Sil
                </button>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
