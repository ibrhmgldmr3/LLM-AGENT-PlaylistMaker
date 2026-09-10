import type { ReactNode } from "react";
import { Compass, LibraryBig, LogOut, MessagesSquare, Moon, Sun, User } from "lucide-react";
import { cn } from "../lib/cn";
import { useLanguage } from "../i18n";
import type { Dict } from "../i18n/dict";

export type Section = "start" | "courses" | "room";

interface NavEntry {
  id: Section;
  label: keyof Dict;
  short: keyof Dict;
  icon: React.ComponentType<{ className?: string }>;
}

const NAV: NavEntry[] = [
  { id: "start", label: "nav.start", short: "nav.start.short", icon: Compass },
  { id: "courses", label: "nav.courses", short: "nav.courses.short", icon: LibraryBig },
  { id: "room", label: "nav.room", short: "nav.room.short", icon: MessagesSquare },
];

interface Props {
  section: Section;
  onNavigate: (section: Section) => void;
  sections: Section[];
  title: string;
  actions?: ReactNode;
  dark: boolean;
  onToggleTheme: () => void;
  email?: string | null;
  onSignOut?: () => void;
  wide?: boolean;
  children: ReactNode;
}

/**
 * Uygulama kabugu: solda salon duvari, ustte baglam cubugu, ortada galeri
 * zemini.
 *
 * Gezinme dar ekranda alt cubuga TASINIR, gizlenmez: uc bolum de her boyutta
 * tek dokunusla erisilebilir olmali. Ray ile alt cubuk ayni `NAV` dizisinden
 * uretiliyor -- ikisini elle kopyalamak, birine eklenen bolumun digerinde
 * unutulmasi demekti.
 */
export function AppShell({
  section,
  onNavigate,
  sections,
  title,
  actions,
  dark,
  onToggleTheme,
  email,
  onSignOut,
  wide,
  children,
}: Props) {
  const { t, lang, setLang } = useLanguage();
  const items = NAV.filter((entry) => sections.includes(entry.id));

  return (
    <div className="app">
      <aside className="rail">
        <div className="rail__brand">
          <span className="rail__mark" aria-hidden>
            <Compass className="h-4 w-4" />
          </span>
          <span className="rail__name">{t("shell.brand")}</span>
        </div>

        <nav className="rail__nav" aria-label={t("nav.sections")}>
          {items.map((entry) => (
            <button
              key={entry.id}
              type="button"
              className="rail__link"
              aria-current={section === entry.id ? "page" : undefined}
              onClick={() => onNavigate(entry.id)}
            >
              <entry.icon className="h-[1.05rem] w-[1.05rem]" aria-hidden />
              {t(entry.label)}
            </button>
          ))}
        </nav>

        {email && (
          <div className="rail__foot">
            <p className="rail__user" title={email}>
              <User className="mr-1 inline h-3 w-3" aria-hidden />
              {email}
            </p>
          </div>
        )}
      </aside>

      <div className="main">
        <header className="bar">
          <h1 className="bar__title">{title}</h1>
          <div className="row" style={{ gap: "0.35rem", flexWrap: "nowrap" }}>
            {actions}
            {/* Dil secimi bir DUGME degil bir SECIM: iki dil bugun, yarin uc
                olabilir ve donguye giren bir dugme o gun yanlis arayuz olur. */}
            <label className="lang">
              <span className="sr-only">{t("shell.language")}</span>
              <select
                className="lang__select"
                value={lang}
                onChange={(event) => setLang(event.target.value === "en" ? "en" : "tr")}
              >
                <option value="tr">{t("shell.languageTr")}</option>
                <option value="en">{t("shell.languageEn")}</option>
              </select>
            </label>
            <button
              type="button"
              className="icon-btn"
              onClick={onToggleTheme}
              aria-label={dark ? t("shell.themeToLight") : t("shell.themeToDark")}
              title={dark ? t("shell.themeLight") : t("shell.themeDark")}
            >
              {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
            {onSignOut && (
              <button
                type="button"
                className="icon-btn"
                onClick={onSignOut}
                aria-label={t("shell.signOut")}
                title={t("shell.signOut")}
              >
                <LogOut className="h-4 w-4" />
              </button>
            )}
          </div>
        </header>

        <div className={cn("body", wide && "body--wide")}>{children}</div>
      </div>

      <nav className="tabbar" aria-label={t("nav.sections")}>
        {items.map((entry) => (
          <button
            key={entry.id}
            type="button"
            className="tabbar__link"
            aria-current={section === entry.id ? "page" : undefined}
            onClick={() => onNavigate(entry.id)}
          >
            <entry.icon className="h-5 w-5" aria-hidden />
            {t(entry.short)}
          </button>
        ))}
      </nav>
    </div>
  );
}
