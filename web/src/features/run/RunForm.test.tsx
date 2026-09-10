import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup } from "@testing-library/react";

import type { Capabilities, CreateRunRequest } from "../../api/types";
import { renderWithLanguage as render } from "../../test/render";
import { RunForm } from "./RunForm";

afterEach(cleanup);

function submitButton(): HTMLButtonElement {
  return screen.getByRole("button", { name: /Ders planımı oluştur|Oluşturuluyor/ }) as HTMLButtonElement;
}

function fillTopic(text: string) {
  fireEvent.change(screen.getByLabelText("Ne öğrenmek istiyorsun?"), { target: { value: text } });
}

describe("RunForm", () => {
  it("boş konuyla gönder düğmesini devre dışı bırakır", () => {
    render(<RunForm capabilities={null} busy={false} onSubmit={vi.fn()} />);

    expect(submitButton().disabled).toBe(true);
  });

  it("konu girilince gönder düğmesi etkinleşir", () => {
    render(<RunForm capabilities={null} busy={false} onSubmit={vi.fn()} />);

    fillTopic("Kalman filtresi");

    expect(submitButton().disabled).toBe(false);
  });

  it("yalnızca boşluktan oluşan konuyu kabul etmez", () => {
    // Regresyon riski: `disabled={!topic.trim()}` ile `if (!topic.trim()) return`
    // AYRI iki kontrol -- biri unutulursa buton aktif ama submit sessizce
    // hicbir sey yapmaz, ya da tam tersi.
    const onSubmit = vi.fn();
    render(<RunForm capabilities={null} busy={false} onSubmit={onSubmit} />);

    fillTopic("   ");
    expect(submitButton().disabled).toBe(true);

    fireEvent.submit(screen.getByRole("button", { name: /Ders planımı oluştur/ }).closest("form")!);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("konudaki baştaki/sondaki boşlukları kırpar", () => {
    const onSubmit = vi.fn();
    render(<RunForm capabilities={null} busy={false} onSubmit={onSubmit} />);

    fillTopic("  Kalman filtresi  ");
    fireEvent.click(submitButton());

    const payload = onSubmit.mock.calls[0][0] as CreateRunRequest;
    expect(payload.topic).toBe("Kalman filtresi");
  });

  it("varsayılan değerlerle doğru sözleşmeyi üretir", () => {
    const onSubmit = vi.fn();
    render(<RunForm capabilities={null} busy={false} onSubmit={onSubmit} />);

    fillTopic("Konu");
    fireEvent.click(submitButton());

    const payload = onSubmit.mock.calls[0][0] as CreateRunRequest;
    expect(payload.filters).toEqual({
      language: "tr",
      difficulty: "mixed",
      max_duration_minutes: 60,
      freshness_preference: "balanced",
      include_english: true,
    });
    expect(payload.options).toEqual({
      enable_asr_fallback: false,
      enable_study_notes: false,
    });
  });

  it("ASR onay kutusu YALNIZCA enable_asr_fallback alanına yazar", () => {
    // Iki checkbox'i AYRI AYRI sinamak sart: ikisini birden true yapan bir test
    // alanlar TAKAS EDILSE bile gecerdi (payload ayni gorunurdu). Bu testin
    // amaci tam da o takasi yakalamak.
    const onSubmit = vi.fn();
    render(<RunForm capabilities={null} busy={false} onSubmit={onSubmit} />);

    fillTopic("Konu");
    fireEvent.click(screen.getByLabelText("Altyazısı olmayan videoların sesini yazıya dök"));
    fireEvent.click(submitButton());

    const payload = onSubmit.mock.calls[0][0] as CreateRunRequest;
    expect(payload.options).toEqual({
      enable_asr_fallback: true,
      enable_study_notes: false,
    });
  });

  it("çalışma notu onay kutusu YALNIZCA enable_study_notes alanına yazar", () => {
    const onSubmit = vi.fn();
    render(<RunForm capabilities={null} busy={false} onSubmit={onSubmit} />);

    fillTopic("Konu");
    fireEvent.click(screen.getByLabelText("Her ders için çalışma notu çıkar"));
    fireEvent.click(submitButton());

    const payload = onSubmit.mock.calls[0][0] as CreateRunRequest;
    expect(payload.options).toEqual({
      enable_asr_fallback: false,
      enable_study_notes: true,
    });
  });

  it("dil İngilizce iken İngilizce genişletme seçeneğini gizler ve kapalı gönderir", () => {
    // "Ingilizce genisletme yalnizca ana dil Ingilizce degilken anlamli" --
    // secenek gorunmese bile ONCEKI true durumu payload'a sizmemeli.
    const onSubmit = vi.fn();
    render(<RunForm capabilities={null} busy={false} onSubmit={onSubmit} />);

    expect(screen.queryByLabelText("İngilizce videoları da değerlendir")).not.toBeNull();

    fireEvent.change(screen.getByLabelText("Dil"), { target: { value: "en" } });
    expect(screen.queryByLabelText("İngilizce videoları da değerlendir")).toBeNull();

    fillTopic("Konu");
    fireEvent.click(submitButton());

    const payload = onSubmit.mock.calls[0][0] as CreateRunRequest;
    expect(payload.filters.include_english).toBe(false);
    expect(payload.filters.language).toBe("en");
  });

  it("süre kaydırıcısı değeri hem etikette hem payload'da günceller", () => {
    const onSubmit = vi.fn();
    render(<RunForm capabilities={null} busy={false} onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText(/en fazla süre/i), { target: { value: "90" } });
    expect(screen.getByText("Ders başına en fazla süre: 90 dk")).toBeTruthy();

    fillTopic("Konu");
    fireEvent.click(submitButton());

    const payload = onSubmit.mock.calls[0][0] as CreateRunRequest;
    expect(payload.filters.max_duration_minutes).toBe(90);
  });

  it("busy iken düğmeyi devre dışı bırakır ve etiketi değiştirir", () => {
    render(<RunForm capabilities={null} busy={true} onSubmit={vi.fn()} />);

    const button = submitButton();
    expect(button.disabled).toBe(true);
    expect(button.textContent).toBe("Oluşturuluyor…");
  });

  it("güzergâh önizlemesi yalnızca gönderilmeden ÖNCE görünür", () => {
    // Onizleme "gonderdikten sonra su olacak" diyor; calistirma basladiginda
    // ayni yolun CANLISI asagida aciliyor. Ikisi ayni anda ekranda olursa
    // onizleme yalan soyler ve ayni yol iki kez cizilir.
    const { rerender } = render(<RunForm capabilities={null} busy={false} onSubmit={vi.fn()} />);

    expect(screen.queryByText("Gönderdikten sonra:")).not.toBeNull();
    expect(screen.queryByText("Konu alt başlıklara ayrılır")).not.toBeNull();
    expect(screen.queryByText("Videolar sıralı bir ders planına dizilir")).not.toBeNull();

    rerender(<RunForm capabilities={null} busy={true} onSubmit={vi.fn()} />);

    expect(screen.queryByText("Gönderdikten sonra:")).toBeNull();
    expect(screen.queryByText("Konu alt başlıklara ayrılır")).toBeNull();
  });

  it("YouTube anahtarı yoksa uyarı gösterir, varsa göstermez", () => {
    const configured: Capabilities = {
      gemini_configured: true,
      llm_configured: true,
      youtube_search_configured: false,
      youtube_publish_configured: false,
      asr_available: false,
      cookies_configured: false,
      runs_remaining_today: null,
      service_capacity_reached: false,
    rag_available: false,
      defaults: {},
    };
    const { rerender } = render(<RunForm capabilities={configured} busy={false} onSubmit={vi.fn()} />);
    expect(screen.queryByText(/YouTube Data API anahtarı tanımlı değil/)).not.toBeNull();

    rerender(
      <RunForm
        capabilities={{ ...configured, youtube_search_configured: true }}
        busy={false}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.queryByText(/YouTube Data API anahtarı tanımlı değil/)).toBeNull();
  });

  describe("kalan çalıştırma hakkı", () => {
    const base: Capabilities = {
      gemini_configured: true,
      llm_configured: true,
      youtube_search_configured: true,
      youtube_publish_configured: false,
      asr_available: false,
      cookies_configured: false,
      runs_remaining_today: null,
      service_capacity_reached: false,
    rag_available: false,
      defaults: {},
    };

    it("sınır yoksa (null) hiçbir şey göstermez", () => {
      // `null` "sunucuda sınır tanımlı değil" demek; 0 ile karıştırılırsa
      // sınırsız kurulumda kullanıcıya "hakkın doldu" denirdi.
      render(<RunForm capabilities={base} busy={false} onSubmit={vi.fn()} />);

      expect(screen.queryByText(/hakkın kaldı/i)).toBeNull();
      expect(screen.queryByText(/hakkın doldu/i)).toBeNull();
      fillTopic("kuantum");
      expect(submitButton().disabled).toBe(false);
    });

    it("kalan hakkı gösterir", () => {
      render(
        <RunForm
          capabilities={{ ...base, runs_remaining_today: 2 }}
          busy={false}
          onSubmit={vi.fn()}
        />,
      );

      expect(screen.queryByText(/Bugün 2 ders planı hakkın kaldı/)).not.toBeNull();
      fillTopic("kuantum");
      expect(submitButton().disabled).toBe(false);
    });

    it("servisin ortak kapasitesi dolduysa kullanicinin hakki olsa BILE engeller", () => {
      // Kota tum kullanicilar icin ortak: kisisel hak tek basina yetmiyor.
      render(
        <RunForm
          capabilities={{ ...base, runs_remaining_today: 3, service_capacity_reached: true }}
          busy={false}
          onSubmit={vi.fn()}
        />,
      );

      expect(screen.queryByText(/Servisin bugünkü kapasitesi doldu/)).not.toBeNull();
      fillTopic("kuantum");
      expect(submitButton().disabled).toBe(true);
      // Iki mesaj birden cikmasin: ortak kapasite kisisel haktan onceliklidir.
      expect(screen.queryByText(/hakkın kaldı/i)).toBeNull();
    });

    it("hak bittiğinde uyarır ve göndermeyi engeller", () => {
      // Sunucu zaten 429 dönecek; kullaniciya tiklamadan ONCE soylemek daha durust.
      const onSubmit = vi.fn();
      render(
        <RunForm
          capabilities={{ ...base, runs_remaining_today: 0 }}
          busy={false}
          onSubmit={onSubmit}
        />,
      );

      expect(screen.queryByText(/Bugünlük hakkın doldu/)).not.toBeNull();
      fillTopic("kuantum");
      expect(submitButton().disabled).toBe(true);
    });
  });

  it("capabilities null iken uyarı göstermez", () => {
    render(<RunForm capabilities={null} busy={false} onSubmit={vi.fn()} />);
    expect(screen.queryByText(/YouTube Data API anahtarı tanımlı değil/)).toBeNull();
  });
});
