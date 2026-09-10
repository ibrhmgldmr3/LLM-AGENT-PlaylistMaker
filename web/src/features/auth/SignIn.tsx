import { useState } from "react";
import { Compass, ListChecks, MessagesSquare, Moon, ShieldCheck, Sun } from "lucide-react";
import { api, ApiError } from "../../api/client";
import { Note, Spinner } from "../../components/ui";
import { useT } from "../../i18n";
import type { Dict } from "../../i18n/dict";

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
const POINTS: { icon: React.ComponentType<{ className?: string }>; title: keyof Dict; body: keyof Dict }[] = [
  { icon: Compass, title: "signin.point1.title", body: "signin.point1.body" },
  { icon: ListChecks, title: "signin.point2.title", body: "signin.point2.body" },
  { icon: MessagesSquare, title: "signin.point3.title", body: "signin.point3.body" },
];

export function SignIn({ dark, onToggleTheme }: { dark: boolean; onToggleTheme: () => void }) {
  const t = useT();
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
        <span className="rail__name">{t("shell.brand")}</span>
        <button
          type="button"
          className="icon-btn icon-btn--chalk"
          style={{ marginLeft: "auto" }}
          onClick={onToggleTheme}
          aria-label={dark ? t("shell.themeToLight") : t("shell.themeToDark")}
        >
          {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </button>
      </div>

      <div className="signin__body">
        <h1 className="signin__title">{t("signin.title")}</h1>
        <p className="signin__lede">{t("signin.lede")}</p>

        <ul className="signin__points">
          {POINTS.map((point) => (
            <li key={point.title}>
              <span className="signin__point-mark" aria-hidden>
                <point.icon className="h-4 w-4" />
              </span>
              <div>
                <strong>{t(point.title)}</strong>
                <p>{t(point.body)}</p>
              </div>
            </li>
          ))}
        </ul>

        {error && (
          <Note tone="danger" role="alert" title={t("signin.failed")}>
            {error}
          </Note>
        )}

        <div className="signin__cta">
          <button type="button" className="btn btn--primary btn--lg" onClick={start} disabled={busy}>
            {busy && <Spinner />}
            {busy ? t("signin.redirecting") : t("signin.google")}
          </button>
          <p className="signin__fine">
            <ShieldCheck className="h-3.5 w-3.5" aria-hidden />
            {t("signin.fine")}
          </p>
        </div>
      </div>
    </div>
  );
}
