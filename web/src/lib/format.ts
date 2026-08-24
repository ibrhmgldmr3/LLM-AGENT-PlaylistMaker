/**
 * Ortak bicimlendiriciler.
 *
 * Ayni islerin (sure, sayi, tarih) bileşenlerin icinde AYRI AYRI yazilmis
 * kopyalari vardi ve sessizce ayrisiyorlardi: bir yerde "45dk", baska yerde
 * "45 dk", bir yerde "1s 12dk" (saat mi saniye mi?). Tek yerde toplandi.
 */

const NUMBER = new Intl.NumberFormat("tr-TR", { notation: "compact", maximumFractionDigits: 1 });
const DATE = new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "long", year: "numeric" });
const DATETIME = new Intl.DateTimeFormat("tr-TR", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

/** `2712` -> `"45 dk"`, `4320` -> `"1 sa 12 dk"`. */
export function formatDuration(seconds: number | null | undefined): string | null {
  if (!seconds || seconds <= 0) return null;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  if (!hours) return `${minutes} dk`;
  return minutes ? `${hours} sa ${minutes} dk` : `${hours} sa`;
}

/** Toplam sureyi ders planı basligi icin ozetler: `"yaklaşık 4 sa 20 dk"`. */
export function formatTotalDuration(secondsList: (number | null)[]): string | null {
  const total = secondsList.reduce<number>((sum, value) => sum + (value ?? 0), 0);
  const text = formatDuration(total);
  return text ? `yaklaşık ${text}` : null;
}

/** Saniyeyi `12:34` / `1:02:03` bicimine cevirir (oynatici zaman damgasi). */
export function formatClock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const pad = (value: number) => String(value).padStart(2, "0");
  return hours ? `${hours}:${pad(minutes)}:${pad(secs)}` : `${minutes}:${pad(secs)}`;
}

/** `1240000` -> `"1,2 Mn"`. */
export function formatCount(value: number | null | undefined): string | null {
  if (!value || value <= 0) return null;
  return NUMBER.format(value);
}

export function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "—" : DATE.format(date);
}

export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "—" : DATETIME.format(date);
}
