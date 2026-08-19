import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import type { CredentialsResponse } from "../../api/client";
import { CredentialsPanel } from "./CredentialsPanel";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function response(overrides: Partial<CredentialsResponse> = {}): CredentialsResponse {
  return {
    editable: true,
    items: [
      { name: "gemini_api_key", label: "Gemini API anahtarı", required: false, configured: false },
      { name: "together_api_key", label: "Together.ai API anahtarı", required: false, configured: false },
    ],
    ...overrides,
  };
}

describe("CredentialsPanel", () => {
  it("yüklenene kadar hiçbir şey göstermez", async () => {
    let resolve!: (value: CredentialsResponse) => void;
    vi.spyOn(api, "credentials").mockReturnValue(new Promise((r) => (resolve = r)));

    const { container } = render(<CredentialsPanel />);
    expect(container.firstChild).toBeNull();

    await act(async () => resolve(response()));
  });

  it("tek kullanıcılı kurulumda salt-okunur mesaj gösterir, giriş alanı yok", async () => {
    vi.spyOn(api, "credentials").mockResolvedValue(response({ editable: false }));

    render(<CredentialsPanel />);

    await waitFor(() => expect(screen.getByText(/tek kullanıcılı/)).toBeTruthy());
    expect(screen.queryByPlaceholderText("Anahtarı yapıştırın")).toBeNull();
    expect(screen.queryByText("Kaydet")).toBeNull();
  });

  it("değeri hiçbir zaman geri göstermez -- girilmiş anahtarın alanı BOŞ başlar", async () => {
    // Sunucu deger DONDURMUYOR, yalnizca "girilmis mi" bilgisi. Alan mevcut
    // degeri gostermeye kalksaydi, gostercek bir sey olmadigi icin ya bos
    // kalirdi ya da yanlislikla baska bir seyi (ad, placeholder) deger sanip
    // gosterirdi -- bu test o riski kilitliyor.
    vi.spyOn(api, "credentials").mockResolvedValue(
      response({
        items: [
          { name: "gemini_api_key", label: "Gemini API anahtarı", required: false, configured: true },
        ],
      }),
    );

    render(<CredentialsPanel />);

    const input = (await waitFor(() =>
      screen.getByPlaceholderText("Değiştirmek için yeni değer"),
    )) as HTMLInputElement;
    expect(input.value).toBe("");
    expect(screen.getByText("girildi")).toBeTruthy();
  });

  it("boş veya boşluktan oluşan taslakla Kaydet düğmesi devre dışı kalır", async () => {
    vi.spyOn(api, "credentials").mockResolvedValue(response({
      items: [
        { name: "gemini_api_key", label: "Gemini API anahtarı", required: false, configured: false },
      ],
    }));

    render(<CredentialsPanel />);

    const input = (await waitFor(() =>
      screen.getByPlaceholderText("Anahtarı yapıştırın"),
    )) as HTMLInputElement;
    const button = screen.getAllByText("Kaydet")[0] as HTMLButtonElement;
    expect(button.disabled).toBe(true);

    fireEvent.change(input, { target: { value: "   " } });
    expect(button.disabled).toBe(true);

    fireEvent.change(input, { target: { value: "gizli-anahtar" } });
    expect(button.disabled).toBe(false);
  });

  it("kaydet başarılı olunca taslağı temizler, yeniden yükler ve onChange çağırır", async () => {
    const credentials = vi
      .spyOn(api, "credentials")
      .mockResolvedValueOnce(
        response({
          items: [
            { name: "gemini_api_key", label: "Gemini API anahtarı", required: false, configured: false },
          ],
        }),
      )
      .mockResolvedValueOnce(
        response({
          items: [
            { name: "gemini_api_key", label: "Gemini API anahtarı", required: false, configured: true },
          ],
        }),
      );
    const saveCredential = vi.spyOn(api, "saveCredential").mockResolvedValue(undefined);
    const onChange = vi.fn();

    render(<CredentialsPanel onChange={onChange} />);

    const input = (await waitFor(() =>
      screen.getByPlaceholderText("Anahtarı yapıştırın"),
    )) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "  gizli-anahtar  " } });
    fireEvent.click(screen.getAllByText("Kaydet")[0]);

    await waitFor(() => expect(onChange).toHaveBeenCalled());

    // Sunucuya GONDERILEN deger kirpilmis olmali.
    expect(saveCredential).toHaveBeenCalledWith("gemini_api_key", "gizli-anahtar");
    // Basari sonrasi liste yeniden cekilmis olmali (ikinci `credentials()` cagrisi).
    expect(credentials).toHaveBeenCalledTimes(2);
    // Taslak temizlenmis olmali: ayni alan tekrar bos gorunmeli.
    await waitFor(() => {
      const refreshed = screen.getByPlaceholderText("Değiştirmek için yeni değer") as HTMLInputElement;
      expect(refreshed.value).toBe("");
    });
  });

  it("kaydetme başarısız olursa hatayı gösterir, taslağı SİLMEZ", async () => {
    // Basarisiz kayitta taslagi silmek kullanicinin yazdigini kaybettirirdi.
    vi.spyOn(api, "credentials").mockResolvedValue(response({
      items: [
        { name: "gemini_api_key", label: "Gemini API anahtarı", required: false, configured: false },
      ],
    }));
    vi.spyOn(api, "saveCredential").mockRejectedValue(new Error("500 sunucu hatası"));
    const onChange = vi.fn();

    render(<CredentialsPanel onChange={onChange} />);

    const input = (await waitFor(() =>
      screen.getByPlaceholderText("Anahtarı yapıştırın"),
    )) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "gizli-anahtar" } });
    fireEvent.click(screen.getAllByText("Kaydet")[0]);

    await waitFor(() => expect(screen.getByText("500 sunucu hatası")).toBeTruthy());
    expect(input.value).toBe("gizli-anahtar");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("yalnızca girilmiş anahtarlar için Sil düğmesi gösterir", async () => {
    vi.spyOn(api, "credentials").mockResolvedValue(
      response({
        items: [
          { name: "gemini_api_key", label: "Gemini API anahtarı", required: false, configured: true },
          { name: "together_api_key", label: "Together.ai API anahtarı", required: false, configured: false },
        ],
      }),
    );

    render(<CredentialsPanel />);

    await waitFor(() => expect(screen.getAllByText("Kaydet")).toHaveLength(2));
    expect(screen.getAllByText("Sil")).toHaveLength(1);
  });

  it("silme başarılı olunca yeniden yükler ve onChange çağırır", async () => {
    const credentials = vi.spyOn(api, "credentials").mockResolvedValue(
      response({
        items: [
          { name: "gemini_api_key", label: "Gemini API anahtarı", required: false, configured: true },
        ],
      }),
    );
    const deleteCredential = vi.spyOn(api, "deleteCredential").mockResolvedValue(undefined);
    const onChange = vi.fn();

    render(<CredentialsPanel onChange={onChange} />);

    await waitFor(() => expect(screen.getByText("Sil")).toBeTruthy());
    fireEvent.click(screen.getByText("Sil"));

    await waitFor(() => expect(onChange).toHaveBeenCalled());
    expect(deleteCredential).toHaveBeenCalledWith("gemini_api_key");
    expect(credentials).toHaveBeenCalledTimes(2);
  });

  it("yüklemede hata olursa hatayı gösterir, çökmez", async () => {
    vi.spyOn(api, "credentials").mockRejectedValue(new Error("bağlantı hatası"));

    render(<CredentialsPanel />);

    await waitFor(() => expect(screen.getByText("bağlantı hatası")).toBeTruthy());
  });
});
