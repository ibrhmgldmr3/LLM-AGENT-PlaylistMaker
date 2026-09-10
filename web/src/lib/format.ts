/**
 * Ortak bicimlendiriciler.
 *
 * Ayni islerin (sure, sayi, tarih) bileşenlerin icinde AYRI AYRI yazilmis
 * kopyalari vardi ve sessizce ayrisiyorlardi: bir yerde "45dk", baska yerde
 * "45 dk", bir yerde "1s 12dk" (saat mi saniye mi?). Tek yerde toplandi.
 *
 * DIL BURADA ZORUNLU, varsayilan degil. Once `tr-TR` ve "sa"/"dk" bu dosyaya
 * gomuluydu; arayuz Ingilizceye gecince tarihler "7 Eyl 2026" kaliyordu ve
 * hicbir sey bunu soylemiyordu. Parametreyi zorunlu yapmak, dili gecirmeyi
 * unutan her cagriyi DERLEME HATASINA cevirir.
 *
 * Metnin kendisi burada DEGIL sozlukte: buraya gomulen bir "yaklasik",
 * derleyicinin goremedigi ikinci bir metin deposu yaratirdi.
 */

import type { Translate } from "../i18n";
import type { Lang } from "../i18n/dict";

const LOCALE: Record<Lang, string> = { tr: "tr-TR", en: "en-US" };

const numberFormats = new Map<Lang, Intl.NumberFormat>();
const dateFormats = new Map<Lang, Intl.DateTimeFormat>();

function numberFormat(lang: Lang): Intl.NumberFormat {
  let cached = numberFormats.get(lang);
  if (!cached) {
    cached = new Intl.NumberFormat(LOCALE[lang], { notation: "compact", maximumFractionDigits: 1 });
    numberFormats.set(lang, cached);
  }
  return cached;
}

function dateFormat(lang: Lang): Intl.DateTimeFormat {
  let cached = dateFormats.get(lang);
  if (!cached) {
    cached = new Intl.DateTimeFormat(LOCALE[lang], {
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
    dateFormats.set(lang, cached);
  }
  return cached;
}

/** `2712` -> `"45 dk"`, `4320` -> `"1 sa 12 dk"`. */
export function formatDuration(seconds: number | null | undefined, t: Translate): string | null {
  if (!seconds || seconds <= 0) return null;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  if (!hours) return t("format.minutes", { n: minutes });
  return minutes ? t("format.hoursMinutes", { h: hours, m: minutes }) : t("format.hours", { n: hours });
}

/** Toplam sureyi ders planı basligi icin ozetler: `"yaklaşık 4 sa 20 dk"`. */
export function formatTotalDuration(secondsList: (number | null)[], t: Translate): string | null {
  const total = secondsList.reduce<number>((sum, value) => sum + (value ?? 0), 0);
  const text = formatDuration(total, t);
  return text ? t("format.about", { text }) : null;
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
export function formatCount(value: number | null | undefined, lang: Lang): string | null {
  if (!value || value <= 0) return null;
  return numberFormat(lang).format(value);
}

export function formatDateTime(iso: string, lang: Lang): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "—" : dateFormat(lang).format(date);
}
