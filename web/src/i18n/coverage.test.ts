import { describe, expect, it } from "vitest";

/**
 * Ceviri SIZINTISI taramasi.
 *
 * Sozluk yazildi ama cagri yerlerinin bir kismi donusturulmeden kaldi:
 * "Deftere sor", "Metin Yok", `aria-label="Sorun"`, tarih bicimlendiricisine
 * gomulu `tr-TR`... Hicbiri derleme hatasi veriyordu, hicbir test kiriliyordu;
 * arayuz Ingilizceyken ekranda Turkce duruyordu ve bunu yalnizca ekrana bakan
 * biri gorebiliyordu. Sessiz ariza tam olarak bu.
 *
 * Kaynak dosyalar Vite'in kendi `import.meta.glob`'uyla okunuyor: `node:fs`
 * bu projede tipsiz ve yalnizca bir tarama icin `@types/node` eklemek,
 * testin maliyetini tasidigi degerin uzerine cikarirdi.
 *
 * YAKALAMADIGI sey: `t()` cagrilmadan cizilen bir SOZLUK ANAHTARI (ornegin
 * `{t(prompt)}` yerine `{prompt}` yazmak). Anahtarlar ASCII ve sikca bir
 * degisken adi tasidiklari icin metinden ayirt edilemiyorlar; o sinifi ancak
 * o bileseni GERCEKTEN cizen bir test yakalar.
 */

const SOURCES = import.meta.glob("../**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const TURKISH = /[ğışçöüİĞŞÇÖÜ]/;
const PROP = /\b(aria-label|title|placeholder|alt)="([^"]{2,})"/g;
/* Kapanis etiketi ARANIYOR (`</`): jenerik tipler (`request<Detail>(`) da
   `>...<` bicimindedir ve kapanis etiketi olmadan onlardan ayirt edilemezler. */
const JSX_TEXT = />\s*([A-Za-zÇĞİÖŞÜçğıöşü][^<>{}\n]*?)\s*<\//g;

/** Yalnizca BUYUK HARF ve alt tire: sabit adi, kullaniciya donuk metin degil. */
const SHOUTED = /^[A-Z0-9_]+$/;

/** Ceviri gerektirmeyen ozel adlar. */
const PROPER_NOUNS = new Set(["Whisper ASR", "YouTube", "Markdown", "JSON"]);

function scanned(): [string, string][] {
  return Object.entries(SOURCES).filter(
    ([path]) =>
      // `./…` bu dosyanin kendi klasoru, yani sozlugun kendisi: Turkce metnin
      // BULUNMASI gereken tek yer.
      !path.startsWith("./") &&
      !path.includes("/i18n/") &&
      !path.includes("/test/") &&
      !/\.test\.tsx?$/.test(path),
  );
}

/** Yorum satirlari elenir: aciklama Turkce yazilir ve yazilmalidir. */
function codeLines(source: string): { line: string; number: number }[] {
  const lines: { line: string; number: number }[] = [];
  let inBlock = false;
  source.split("\n").forEach((line, index) => {
    const trimmed = line.trim();
    if (trimmed.startsWith("/*")) inBlock = true;
    if (inBlock) {
      if (trimmed.includes("*/")) inBlock = false;
      return;
    }
    if (trimmed.startsWith("*") || trimmed.startsWith("//")) return;
    lines.push({ line, number: index + 1 });
  });
  return lines;
}

describe("ceviri kapsami", () => {
  it("taranacak kaynak dosyalari buluyor", () => {
    // Glob yolu kayarsa iki tarama da SESSIZCE bos kumeyi gecerdi.
    expect(scanned().length).toBeGreaterThan(15);
  });

  it("bilesen kodunda Turkce metin kalmamis olmali", () => {
    const leaks: string[] = [];
    for (const [path, source] of scanned()) {
      for (const { line, number } of codeLines(source)) {
        if (TURKISH.test(line)) leaks.push(`${path}:${number}  ${line.trim()}`);
      }
    }
    expect(leaks).toEqual([]);
  });

  it("JSX metni ve erisilebilirlik etiketleri sozlukten gelmeli", () => {
    const leaks: string[] = [];
    for (const [path, source] of scanned()) {
      for (const { line, number } of codeLines(source)) {
        for (const match of line.matchAll(PROP)) {
          if (PROPER_NOUNS.has(match[2]) || SHOUTED.test(match[2])) continue;
          leaks.push(`${path}:${number}  ${match[1]}="${match[2]}"`);
        }
        if (!path.endsWith(".tsx")) continue;
        for (const match of line.matchAll(JSX_TEXT)) {
          const text = match[1].trim();
          if (!text || PROPER_NOUNS.has(text) || SHOUTED.test(text)) continue;
          leaks.push(`${path}:${number}  ${text}`);
        }
      }
    }
    expect(leaks).toEqual([]);
  });
});
