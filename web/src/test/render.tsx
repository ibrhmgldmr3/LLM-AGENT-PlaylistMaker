import { render, type RenderOptions, type RenderResult } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { LanguageProvider } from "../i18n";

/**
 * Bileseni ceviri saglayicisiyla ve TURKCE'ye SABITLENMIS dille render eder.
 *
 * Iki sebep:
 *
 * 1. `useT` saglayici disinda hata firlatiyor -- eksik cevirinin sessizce
 *    kaybolmamasi icin bilerek boyle. Dolayisiyla metin tasiyan her bilesen
 *    testte de saglayiciya ihtiyac duyuyor.
 * 2. Dil sabitleniyor cunku testler metnin KENDISINI kontrol ediyor. Varsayilan
 *    `navigator.language`den geliyor ve jsdom'da bu "en-US": ayni test
 *    gelistiricinin makinesinde Turkce, CI'da Ingilizce metin gorurdu.
 *    Gecmesi ortama bagli bir test, test degildir.
 *
 * Ingilizce metni dogrulayan bir test yazilacaksa `lang` ile acikca istenir.
 */
export function renderWithLanguage(
  ui: ReactElement,
  { lang = "tr", ...options }: RenderOptions & { lang?: "tr" | "en" } = {},
): RenderResult {
  window.localStorage.setItem("derslik:lang", lang);
  return render(ui, {
    wrapper: ({ children }: { children: ReactNode }) => (
      <LanguageProvider>{children}</LanguageProvider>
    ),
    ...options,
  });
}
