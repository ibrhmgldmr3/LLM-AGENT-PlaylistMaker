import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { api } from "./api/client";
import { installFakeEventSource } from "./test/fake-event-source";

let uninstall: () => void;

beforeEach(() => {
  uninstall = installFakeEventSource();
  vi.spyOn(api, "capabilities").mockResolvedValue({} as never);
  vi.spyOn(api, "me").mockResolvedValue({
    auth_required: false,
    signed_in: true,
    email: null,
  } as never);
});

afterEach(() => {
  vi.unstubAllGlobals();
  uninstall();
  cleanup();
  vi.restoreAllMocks();
});

function submitButton(): HTMLButtonElement {
  return screen.getByRole("button", { name: /Ders planımı oluştur|Oluşturuluyor/ }) as HTMLButtonElement;
}

describe("App", () => {
  it("createRun yaniti gelmeden ikinci gonderimi engeller", async () => {
    // Regresyon: `busy` yalnizca `createRun` DONDUKTEN sonra true oluyordu.
    // Hizli cift tiklama ya da yavas ag iki AYRI sunucu calistirmasi baslatiyor,
    // ikincisi izlenmedigi icin sahipsiz kaliyordu.
    let birak: (value: { run_id: string; state: string; events_url: string; result_url: string }) => void;
    const ucusta = new Promise<{
      run_id: string;
      state: string;
      events_url: string;
      result_url: string;
    }>((resolve) => {
      birak = resolve;
    });
    const createRun = vi.spyOn(api, "createRun").mockReturnValue(ucusta as never);

    render(<App />);
    await waitFor(() => expect(screen.getByLabelText("Ne öğrenmek istiyorsun?")).toBeTruthy());

    fireEvent.change(screen.getByLabelText("Ne öğrenmek istiyorsun?"), { target: { value: "kuantum" } });
    fireEvent.click(submitButton());

    // Yanit HENUZ gelmedi ama dugme kilitlenmis olmali.
    await waitFor(() => expect(submitButton().disabled).toBe(true));
    fireEvent.click(submitButton());
    expect(createRun).toHaveBeenCalledTimes(1);

    await waitFor(async () => {
      birak!({
        run_id: "kosu-1",
        state: "running",
        events_url: "/api/runs/kosu-1/events",
        result_url: "/api/runs/kosu-1",
      });
      await ucusta;
    });

    expect(createRun).toHaveBeenCalledTimes(1);
  });

  it("herhangi bir uctan 401 gelirse giris ekranina doner", async () => {
    // Oturum durumu ACILIRKEN bir kez ogreniliyordu. Cerez sonradan duserse
    // arayuz uygulamayi gostermeye devam ediyor ama her istek 401 aliyordu --
    // giris ekrani gelmiyor, kullanicinin sayfayi elle yenilemesi gerekiyordu.
    //
    // `createRun` BILEREK taklit edilmiyor: 401'in gercek `request()`
    // katmanindan gecip kancayi tetikledigini dogrulamak istiyoruz. `fetch`
    // taklit ediliyor, uzerindeki her sey gercek.
    const me = vi.spyOn(api, "me");
    me.mockResolvedValue({ auth_required: true, signed_in: true, email: null } as never);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 401,
        statusText: "Unauthorized",
        json: async () => ({ detail: "Oturum açmanız gerekiyor" }),
        headers: { get: () => null },
      })),
    );

    render(<App />);
    await waitFor(() => expect(screen.getByLabelText("Ne öğrenmek istiyorsun?")).toBeTruthy());

    // Sunucu oturumu artik tanimiyor.
    me.mockResolvedValue({ auth_required: true, signed_in: false, email: null } as never);

    fireEvent.change(screen.getByLabelText("Ne öğrenmek istiyorsun?"), { target: { value: "kuantum" } });
    fireEvent.click(submitButton());

    await waitFor(() => expect(screen.getByRole("button", { name: /Google ile giriş yap/ })).toBeTruthy());
  });

  it("createRun hata verirse form tekrar kullanilabilir olur", async () => {
    const createRun = vi
      .spyOn(api, "createRun")
      .mockRejectedValue(Object.assign(new Error("Günlük hakkınız doldu"), { retryAfter: 0 }));

    render(<App />);
    await waitFor(() => expect(screen.getByLabelText("Ne öğrenmek istiyorsun?")).toBeTruthy());

    fireEvent.change(screen.getByLabelText("Ne öğrenmek istiyorsun?"), { target: { value: "kuantum" } });
    fireEvent.click(submitButton());

    await screen.findByText(/Günlük hakkınız doldu/);
    await waitFor(() => expect(submitButton().disabled).toBe(false));
    expect(createRun).toHaveBeenCalledTimes(1);
  });
});
