import type { ReactNode } from "react";
import { Compass, LibraryBig, LogOut, MessagesSquare, Moon, Sun, User } from "lucide-react";
import { cn } from "../lib/cn";

export type Section = "start" | "courses" | "room";

interface NavEntry {
  id: Section;
  label: string;
  short: string;
  icon: React.ComponentType<{ className?: string }>;
}

const NAV: NavEntry[] = [
  { id: "start", label: "Öğrenmeye başla", short: "Başla", icon: Compass },
  { id: "courses", label: "Derslerim", short: "Derslerim", icon: LibraryBig },
  { id: "room", label: "Çalışma odası", short: "Çalışma", icon: MessagesSquare },
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
 * Uygulama kabugu: solda tahta yesili ray, ustte baglam cubugu, ortada kagit.
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
  const items = NAV.filter((entry) => sections.includes(entry.id));

  return (
    <div className="app">
      <aside className="rail">
        <div className="rail__brand">
          <span className="rail__mark" aria-hidden>
            <Compass className="h-4 w-4" />
          </span>
          <span className="rail__name">Derslik</span>
        </div>

        <nav className="rail__nav" aria-label="Ana bölümler">
          {items.map((entry) => (
            <button
              key={entry.id}
              type="button"
              className="rail__link"
              aria-current={section === entry.id ? "page" : undefined}
              onClick={() => onNavigate(entry.id)}
            >
              <entry.icon className="h-[1.05rem] w-[1.05rem]" aria-hidden />
              {entry.label}
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
            <button
              type="button"
              className="icon-btn"
              onClick={onToggleTheme}
              aria-label={dark ? "Aydınlık temaya geç" : "Karanlık temaya geç"}
              title={dark ? "Aydınlık tema" : "Karanlık tema"}
            >
              {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
            {onSignOut && (
              <button
                type="button"
                className="icon-btn"
                onClick={onSignOut}
                aria-label="Çıkış yap"
                title="Çıkış yap"
              >
                <LogOut className="h-4 w-4" />
              </button>
            )}
          </div>
        </header>

        <div className={cn("body", wide && "body--wide")}>{children}</div>
      </div>

      <nav className="tabbar" aria-label="Ana bölümler">
        {items.map((entry) => (
          <button
            key={entry.id}
            type="button"
            className="tabbar__link"
            aria-current={section === entry.id ? "page" : undefined}
            onClick={() => onNavigate(entry.id)}
          >
            <entry.icon className="h-5 w-5" aria-hidden />
            {entry.short}
          </button>
        ))}
      </nav>
    </div>
  );
}
