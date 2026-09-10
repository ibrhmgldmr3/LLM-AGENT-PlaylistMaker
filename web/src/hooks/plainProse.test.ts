import { describe, expect, it } from "vitest";
import { plainProse } from "./useVideoRAG";

/**
 * Yanit yuzeyi bu urunun MERKEZI ve orada `* **UFE (Uretici Fiyat Endeksi):**`
 * gibi ham Markdown gorunuyordu. Istem artik duz metin istiyor; bu soyucu,
 * modelin yine de Markdown yazdigi durumlar icin ikinci savunma.
 */
describe("plainProse", () => {
  it("kalin ve egik isaretlerini soker, metni birakir", () => {
    expect(plainProse("**ÜFE** ve *TÜFE* farklıdır.")).toBe("ÜFE ve TÜFE farklıdır.");
    expect(plainProse("__kalın__ metin")).toBe("kalın metin");
  });

  it("madde imlerini gercek madde imine cevirir ve SATIR yapisini korur", () => {
    // Liste bir SUS degil: onerilen sorulardan biri zaten "Önemli noktaları
    // listele". Isaret degisir, yapi kalir.
    expect(plainProse("* TÜFE\n* ÜFE\n- Deflatör")).toBe("• TÜFE\n• ÜFE\n• Deflatör");
  });

  it("baslik ve kod isaretlerini kaldirir", () => {
    expect(plainProse("## Başlık\nnormal `kod` metni")).toBe("Başlık\nnormal kod metni");
    expect(plainProse("```\nblok\n```")).toBe("blok");
  });

  it("duz metne dokunmaz", () => {
    const duz = "Enflasyon, fiyatlardaki artışların sürekli hale gelmesidir.\n\nİkinci paragraf.";
    expect(plainProse(duz)).toBe(duz);
  });

  it("carpim ve yildiz iceren normal metni bozmaz", () => {
    // Tek yildiz yalnizca ESLESEN bir cift varsa kaldiriliyor; yalniz kalan
    // bir yildiz metnin kendisidir.
    expect(plainProse("2 * 3 = 6")).toBe("2 * 3 = 6");
  });
});
