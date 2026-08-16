import { useState } from "react";
import { api, ApiError } from "../../api/client";

/**
 * Giris ekrani. Cok kullanicili kurulumda oturum yokken TEK gorunen sey.
 *
 * Giris, YouTube yayin izniyle ayni Google onayindan geciyor: kullanici zaten
 * yayin icin hesap bagliyor, ikinci bir onay ekrani eklemek gereksizdi.
 */
export function SignIn() {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const start = () => {
    setBusy(true);
    setError(null);
    api
      .youtubeAuthUrl()
      // Tam sayfa yonlendirme: Google onay ekrani bir iframe'de acilamaz.
      .then((body) => {
        window.location.href = body.authorization_url;
      })
      .catch((exc: ApiError) => {
        setError(exc.message);
        setBusy(false);
      });
  };

  return (
    <div className="card">
      <div className="card__head">
        <h3>Giriş yapın</h3>
      </div>
      <p className="muted">
        Google hesabınızla giriş yaparsınız; aynı izinle oluşturduğunuz playlist'i YouTube'a
        yayınlayabilirsiniz.
      </p>
      <p className="muted">
        Giriş sonrası kendi <strong>Gemini API anahtarınızı</strong> girmeniz gerekiyor.
        Çalıştırmalar sizin anahtarınızla yapılır.
      </p>
      {error && <p className="alert alert--error">{error}</p>}
      <button type="button" onClick={start} disabled={busy}>
        {busy ? "Yönlendiriliyor…" : "Google ile giriş yap"}
      </button>
    </div>
  );
}
