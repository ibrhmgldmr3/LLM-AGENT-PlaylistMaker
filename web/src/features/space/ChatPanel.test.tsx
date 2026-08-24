import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ChatPanel } from "./ChatPanel";
import { api } from "../../api/client";
import type { RagAnswer } from "../../api/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function answer(overrides: Partial<RagAnswer> = {}): RagAnswer {
  return {
    answered: true,
    answer: "Kovaryans küçülür.",
    citations: [],
    searched_sources: 3,
    reason: null,
    ...overrides,
  };
}

/**
 * `act` ile sariliyor: gonderim asenkron bir istekten sonra durum guncelliyor
 * ve sarilmadiginda React "act(...) disinda guncelleme" uyarisi basiyor.
 * Uyariyi gormezden gelmek, GERCEK bir yaris kosulunu da gizlerdi.
 */
async function ask(question = "kovaryans") {
  await act(async () => {
    fireEvent.change(screen.getByLabelText("Soru"), { target: { value: question } });
    fireEvent.click(screen.getByRole("button", { name: "Sor" }));
  });
}

describe("ChatPanel", () => {
  it("videonun tam saniyesine giden bir alinti baglantisi gosterir", async () => {
    vi.spyOn(api, "ask").mockResolvedValue(
      answer({
        citations: [
          {
            source_id: "video:abc",
            title: "Kalman filtresi",
            url: "https://www.youtube.com/watch?v=abc&t=754s",
            start_sec: 754,
            page: null,
            quote: "Kovaryans matrisi küçülür.",
          },
        ],
      }),
    );

    render(<ChatPanel spaceId="sp" ready />);
    await ask();

    const link = await screen.findByRole("link", { name: /Kalman filtresi · 12:34/ });
    expect(link).toHaveProperty("href", "https://www.youtube.com/watch?v=abc&t=754s");
  });

  it("dokuman alintisini sayfa numarasiyla ve baglantisiz gosterir", async () => {
    vi.spyOn(api, "ask").mockResolvedValue(
      answer({
        citations: [
          {
            source_id: "doc:1",
            title: "ders-notu.pdf",
            url: null,
            start_sec: null,
            page: 4,
            quote: "Tanım.",
          },
        ],
      }),
    );

    render(<ChatPanel spaceId="sp" ready />);
    await ask();

    expect(await screen.findByText(/ders-notu\.pdf · s\. 4/)).toBeDefined();
    expect(screen.queryByRole("link")).toBeNull();
  });

  /**
   * "Bulamadim" bu ozelligin VAADI, bir ariza degil. Kirmizi hata kutusunda
   * gostermek kullaniciya sistemin bozuldugunu dusundururdu; notr kutuda ve
   * taranan kaynak sayisiyla birlikte gosteriliyor.
   */
  it("yanit bulunamadiginda notr bir kutu gosterir, hata kutusu degil", async () => {
    vi.spyOn(api, "ask").mockResolvedValue(
      answer({
        answered: false,
        answer: null,
        reason: "3 kaynakta arandı; bu soruyu karşılayan bir bölüm bulunamadı.",
      }),
    );

    render(<ChatPanel spaceId="sp" ready />);
    await ask("bugün hava nasıl");

    const box = await screen.findByRole("status");
    expect(box.textContent).toContain("3 kaynakta arandı");
    expect(box.className).toBe("alert");
    expect(box.className).not.toContain("alert--error");
  });

  it("aranabilir icerik yokken soru sorulamaz", () => {
    const spy = vi.spyOn(api, "ask");
    render(<ChatPanel spaceId="sp" ready={false} />);

    expect(screen.getByLabelText("Soru")).toHaveProperty("disabled", true);
    expect(screen.getByText(/aranabilir içerik olmadan/)).toBeDefined();
    expect(spy).not.toHaveBeenCalled();
  });

  it("bos soru gonderilmez", () => {
    const spy = vi.spyOn(api, "ask");
    render(<ChatPanel spaceId="sp" ready />);

    fireEvent.change(screen.getByLabelText("Soru"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "Sor" }));

    expect(spy).not.toHaveBeenCalled();
  });

  it("onceki yaniti yeni soru gonderilirken temizler", async () => {
    const spy = vi
      .spyOn(api, "ask")
      .mockResolvedValueOnce(answer({ answer: "Ilk cevap." }))
      .mockResolvedValueOnce(answer({ answer: "Ikinci cevap." }));

    render(<ChatPanel spaceId="sp" ready />);
    await ask("bir");
    expect(await screen.findByText("Ilk cevap.")).toBeDefined();

    await ask("iki");
    await waitFor(() => expect(screen.queryByText("Ilk cevap.")).toBeNull());
    expect(await screen.findByText("Ikinci cevap.")).toBeDefined();
    expect(spy).toHaveBeenCalledTimes(2);
  });
});
