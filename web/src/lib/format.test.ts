import { describe, expect, it } from "vitest";
import { DICT, type Dict, type Lang } from "../i18n/dict";
import { formatCount, formatDateTime, formatDuration, formatTotalDuration } from "./format";

/**
 * Bicimlendiriciler DILI zorunlu parametre olarak aliyor. Once `tr-TR` ve
 * "sa"/"dk" dosyaya gomuluydu; arayuz Ingilizceye gecince tarihler "7 Eyl 2026"
 * kaliyordu ve bunu yalnizca ekrana bakan biri gorebiliyordu.
 */
function translate(lang: Lang) {
  return (key: keyof Dict, params?: Record<string, string | number>) =>
    Object.entries(params ?? {}).reduce<string>(
      (text, [name, value]) => text.replaceAll(`{${name}}`, String(value)),
      DICT[lang][key],
    );
}

const tr = translate("tr");
const en = translate("en");

describe("formatDuration", () => {
  it("saat ve dakikayi kullanicinin dilinde yazar", () => {
    expect(formatDuration(2712, tr)).toBe("45 dk");
    expect(formatDuration(2712, en)).toBe("45 min");
    expect(formatDuration(4320, tr)).toBe("1 sa 12 dk");
    expect(formatDuration(4320, en)).toBe("1 h 12 min");
    expect(formatDuration(7200, tr)).toBe("2 sa");
  });

  it("sifir ve tanimsiz sureyi metne cevirmez", () => {
    // `null` "sure bilinmiyor" demek; "0 dk" yazmak bilinmeyeni BILINEN bir
    // deger gibi gosterirdi.
    expect(formatDuration(0, tr)).toBeNull();
    expect(formatDuration(null, tr)).toBeNull();
    expect(formatDuration(undefined, tr)).toBeNull();
  });
});

describe("formatTotalDuration", () => {
  it("toplami yaklasiklik ekiyle ozetler", () => {
    expect(formatTotalDuration([3600, 1200], tr)).toBe("yaklaşık 1 sa 20 dk");
    expect(formatTotalDuration([3600, 1200], en)).toBe("about 1 h 20 min");
  });

  it("hicbir sure yoksa bos doner", () => {
    expect(formatTotalDuration([null, null], tr)).toBeNull();
  });
});

describe("yerel bicimler", () => {
  it("tarih iki dilde AYRI bicimlenir", () => {
    const iso = "2026-09-07T10:12:45.000Z";
    expect(formatDateTime(iso, "tr")).not.toBe(formatDateTime(iso, "en"));
  });

  it("gecersiz tarih patlamaz", () => {
    expect(formatDateTime("bir tarih degil", "tr")).toBe("—");
  });

  it("sayi kisaltmasi dile gore degisir", () => {
    // Turkcede ondalik ayirici virgul, Ingilizcede nokta.
    expect(formatCount(1240000, "tr")).toContain(",");
    expect(formatCount(1240000, "en")).toContain(".");
    expect(formatCount(0, "tr")).toBeNull();
  });
});
