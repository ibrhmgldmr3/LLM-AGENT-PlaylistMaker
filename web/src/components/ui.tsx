import type { ReactNode } from "react";
import {
  ChevronDown,
  CircleAlert,
  CircleCheck,
  Info,
  LoaderCircle,
  TriangleAlert,
} from "lucide-react";
import { cn } from "../lib/cn";

/* -------------------------------------------------------------------- uyari */

type Tone = "info" | "warn" | "danger" | "ok";

const TONE_ICON = {
  info: Info,
  warn: TriangleAlert,
  danger: CircleAlert,
  ok: CircleCheck,
} as const;

const TONE_CLASS = {
  info: "",
  warn: "note--warn",
  danger: "note--danger",
  ok: "note--ok",
} as const;

/**
 * Bilgi/uyari kutusu.
 *
 * `tone` ile `role` AYRI: "cevap bulunamadi" NOTR bir sonuctur ama duyurulmasi
 * gerekir (`role="status"`), yayin hatasi ise kirmizidir ve `role="alert"`
 * ister. Ikisini tek bir bayrakla yonetmek, sistemin bozuldugunu dusunduren
 * kirmizi kutularin geri gelmesi demekti.
 */
export function Note({
  tone = "info",
  title,
  children,
  role,
  className,
}: {
  tone?: Tone;
  title?: string;
  children: ReactNode;
  role?: "status" | "alert";
  className?: string;
}) {
  const Icon = TONE_ICON[tone];
  return (
    <div className={cn("note", TONE_CLASS[tone], className)} role={role}>
      <Icon className="h-4 w-4" aria-hidden />
      <div className="min-w-0">
        {title && <strong>{title}</strong>}
        <div>{children}</div>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- boş durum */

export function Empty({
  icon: Icon,
  title,
  children,
  action,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty__mark">
        <Icon className="h-5 w-5" aria-hidden />
      </span>
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action && <div style={{ marginTop: "1rem" }}>{action}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ ölçüm */

export function Meter({
  value,
  max = 100,
  label,
  variant,
}: {
  value: number;
  max?: number;
  label: string;
  variant?: "signal" | "chalk";
}) {
  const percent = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
  return (
    <div
      className={cn("meter", variant === "signal" && "meter--signal", variant === "chalk" && "meter--chalk")}
      role="progressbar"
      aria-label={label}
      aria-valuenow={Math.round(percent)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div className="meter__fill" style={{ transform: `scaleX(${percent / 100})` }} />
    </div>
  );
}

/* ---------------------------------------------------------------- yükleniyor */

export function Spinner({ className }: { className?: string }) {
  return <LoaderCircle className={cn("spin h-4 w-4", className)} aria-hidden />;
}

/* -------------------------------------------------------------------- katlar */

/**
 * Katlanabilir bolum. `<details>` uzerine kuruldu: JavaScript olmadan da
 * acilir, Ctrl+F sayfa aramasi icerigi bulur ve klavye ile calisir.
 */
export function Fold({
  summary,
  icon: Icon,
  children,
  open,
  className,
}: {
  summary: string;
  icon?: React.ComponentType<{ className?: string }>;
  children: ReactNode;
  open?: boolean;
  className?: string;
}) {
  return (
    <details className={cn("fold", className)} open={open}>
      <summary>
        {Icon && <Icon className="h-3.5 w-3.5" aria-hidden />}
        {summary}
        <ChevronDown className="fold__caret h-4 w-4" aria-hidden />
      </summary>
      <div className="fold__body">{children}</div>
    </details>
  );
}
