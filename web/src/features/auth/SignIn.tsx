import { useState } from "react";
import { Compass, ListChecks, MessagesSquare, Moon, ShieldCheck, Sun } from "lucide-react";
import { api, ApiError } from "../../api/client";
import { Note, Spinner } from "../../components/ui";

/**
 * Giris ekrani. Cok kullanicili kurulumda oturum yokken TEK gorunen sey.
 *
 * Giris, YouTube yayin izniyle ayni Google onayindan geciyor: kullanici zaten
 * yayin icin hesap bagliyor, ikinci bir onay ekrani eklemek gereksizdi.
 *
 * Ekran yalnizca bir dugmeden ibaret DEGIL: giris yapmadan once uygulamanin ne
 * yaptigini bilmeyen biri, tanimadigi bir siteye Google hesabini baglamak
 * istemez. Uc madde, izin ekranina gitmeden once neye evet dedigini anlatiyor.
 */
const POINTS = [
  {
    icon: Compass,
    title: "Bir konu yaz, ders planı al",
    body: "Konu alt başlıklara ayrılır, her biri için en uygun video seçilir ve sıraya dizilir.",
  },
  {
    icon: ListChecks,
    title: "İzlediklerini işaretle",
    body: "Nerede kaldığını takip et; planı baştan sona bitir.",
  },
  {
    icon: MessagesSquare,
    title: "Derse soru sor",
    body: "Cevaplar yalnızca senin kaynaklarına dayanır ve videonun tam saniyesine götürür.",
  },
];

export function SignIn({ dark, onToggleTheme }: { dark: boolean; onToggleTheme: () => void }) {
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
    <div className="signin">
      <div className="signin__brand">
        <span className="rail__mark" aria-hidden>
          <Compass className="h-4 w-4" />
        </span>
        <span className="rail__name">Derslik</span>
        <button
          type="button"
          className="icon-btn icon-btn--chalk"
          style={{ marginLeft: "auto" }}
          onClick={onToggleTheme}
          aria-label={dark ? "Aydınlık temaya geç" : "Karanlık temaya geç"}
        >
          {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </button>
      </div>

      <div className="signin__body">
        <h1 className="signin__title">Kendi kendine öğrenmenin düzenli hali.</h1>
        <p className="signin__lede">
          YouTube'da doğru videoyu aramakla geçen zamanı, baştan sona izlenecek bir ders
          planına çeviriyoruz.
        </p>

        <ul className="signin__points">
          {POINTS.map((point) => (
            <li key={point.title}>
              <span className="signin__point-mark" aria-hidden>
                <point.icon className="h-4 w-4" />
              </span>
              <div>
                <strong>{point.title}</strong>
                <p>{point.body}</p>
              </div>
            </li>
          ))}
        </ul>

        {error && (
          <Note tone="danger" role="alert" title="Giriş başlatılamadı">
            {error}
          </Note>
        )}

        <div className="signin__cta">
          <button type="button" className="btn btn--primary btn--lg" onClick={start} disabled={busy}>
            {busy && <Spinner />}
            {busy ? "Yönlendiriliyor…" : "Google ile giriş yap"}
          </button>
          <p className="signin__fine">
            <ShieldCheck className="h-3.5 w-3.5" aria-hidden />
            Aynı izinle hazırladığın planı YouTube'a playlist olarak yayınlayabilirsin. API
            anahtarı girmen gerekmez.
          </p>
        </div>
      </div>
    </div>
  );
}
