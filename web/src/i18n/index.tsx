import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { DICT, type Dict, type Lang } from "./dict";

export type { Lang } from "./dict";

const LANG_KEY = "derslik:lang";

/**
 * Arayuz dili. TEMA ile AYNI desende: `localStorage` + sistem varsayilani.
 *
 * Kutuphane YOK. i18next ve benzerleri cogul kurallari, tarih bicimleri ve
 * tembel yukleme icin var; burada iki dil ve duz metinler soz konusu.
 * `Intl` zaten tarayicida ve sayilari/tarihleri o bicimliyor.
 *
 * `PLAYLIST DILI` ILE KARISTIRILMAMALI. `Tercihler > Dil`, uretilecek
 * videolarin ve calisma notlarinin dilini secer ve bir CALISTIRMA ayaridir;
 * bu ise arayuzun kendi dili. Ikisi bilerek ayri: Turkce arayuzle Ingilizce
 * kaynak toplamak mesru bir kullanim.
 */
function initialLang(): Lang {
  try {
    const saved = window.localStorage.getItem(LANG_KEY);
    if (saved === "tr" || saved === "en") return saved;
  } catch {
    /* gizli sekme: tercih okunamaz, tarayici diline duselim */
  }
  return navigator.language?.toLowerCase().startsWith("tr") ? "tr" : "en";
}

/** `{ad}` yer tutucularini doldurur. */
function fill(template: string, params?: Record<string, string | number>): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (whole, key: string) =>
    key in params ? String(params[key]) : whole,
  );
}

export type Translate = (key: keyof Dict, params?: Record<string, string | number>) => string;

interface LanguageValue {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: Translate;
  /** Yanit dilinin sunucuya gonderilecek adi (`AskRequest.language`). */
  answerLanguage: string;
}

const LanguageContext = createContext<LanguageValue | null>(null);

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(initialLang);

  useEffect(() => {
    // `<html lang>` ekran okuyucunun telaffuzunu ve tarayicinin ceviri
    // teklifini yonetiyor; metin degisip bu kalirsa ikisi de yanlis olur.
    document.documentElement.lang = lang;
  }, [lang]);

  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    try {
      window.localStorage.setItem(LANG_KEY, next);
    } catch {
      /* gizli sekme: secim bu oturumda gecerli, kalici degil */
    }
  }, []);

  const value = useMemo<LanguageValue>(() => {
    const table = DICT[lang];
    return {
      lang,
      setLang,
      // Eksik anahtar ANAHTARIN KENDISINI donuyor, bos dize degil: eksik bir
      // ceviri gorunur bir hata olmali, sessizce kaybolan bir etiket degil.
      t: (key, params) => fill(table[key] ?? String(key), params),
      answerLanguage: lang === "tr" ? "Türkçe" : "English",
    };
  }, [lang, setLang]);

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useLanguage(): LanguageValue {
  const value = useContext(LanguageContext);
  if (!value) throw new Error("useLanguage, LanguageProvider içinde çağrılmalı");
  return value;
}

/** Kisayol: yalnizca ceviri fonksiyonu gerekenler icin. */
export function useT(): Translate {
  return useLanguage().t;
}
