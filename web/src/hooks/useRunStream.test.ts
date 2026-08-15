import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type { PlaylistResult } from "../api/types";
import { FakeEventSource, installFakeEventSource } from "../test/fake-event-source";
import { useRunStream } from "./useRunStream";

let uninstall: () => void;

beforeEach(() => {
  uninstall = installFakeEventSource();
});

afterEach(() => {
  uninstall();
  vi.restoreAllMocks();
});

/** `done` olayinin govdesi; alanlar `RunSnapshotBody` sozlesmesiyle ayni. */
function snapshot(overrides: Record<string, unknown> = {}) {
  return {
    run_id: "kosu-1",
    state: "done",
    created_at: "2026-01-01T00:00:00Z",
    progress: 1,
    stage: null,
    message: null,
    error: null,
    ...overrides,
  };
}

describe("useRunStream", () => {
  it("akisi calistirmanin ucuna baglar ve durumu 'running' yapar", () => {
    const { result } = renderHook(() => useRunStream());

    act(() => result.current.watch("kosu-1"));

    expect(FakeEventSource.last.url).toBe("/api/runs/kosu-1/events");
    expect(result.current.runId).toBe("kosu-1");
    expect(result.current.state).toBe("running");
  });

  it("`progress` olaylarini duruma yansitir", () => {
    const { result } = renderHook(() => useRunStream());
    act(() => result.current.watch("kosu-1"));

    act(() =>
      FakeEventSource.last.emit("progress", {
        stage: "discovery",
        message: "Adaylar araniyor",
        progress: 0.4,
        current: 2,
        total: 5,
      }),
    );

    expect(result.current.progress).toBe(0.4);
    expect(result.current.stage).toBe("discovery");
    expect(result.current.message).toBe("Adaylar araniyor");
  });

  it("`done` gelince akisi kapatip sonucu ayri uctan ceker", async () => {
    const playlist = { run_id: "kosu-1", topic: "Konu" } as unknown as PlaylistResult;
    const getRun = vi
      .spyOn(api, "getRun")
      .mockResolvedValue({ run_id: "kosu-1", state: "done", result: playlist });

    const { result } = renderHook(() => useRunStream());
    act(() => result.current.watch("kosu-1"));
    const source = FakeEventSource.last;

    act(() => source.emit("done", snapshot()));

    // SSE yalnizca ilerleme tasiyor; sonuc gelmeden akis kapanmali.
    expect(source.closed).toBe(true);
    expect(getRun).toHaveBeenCalledWith("kosu-1");
    await waitFor(() => expect(result.current.state).toBe("done"));
    expect(result.current.result).toBe(playlist);
    expect(result.current.progress).toBe(1);
  });

  it("basarisiz biten calistirmada sunucunun hata metnini kullanir", () => {
    const getRun = vi.spyOn(api, "getRun");
    const { result } = renderHook(() => useRunStream());
    act(() => result.current.watch("kosu-1"));

    act(() =>
      FakeEventSource.last.emit("done", snapshot({ state: "failed", error: "Gemini kotasi doldu" })),
    );

    expect(result.current.state).toBe("failed");
    expect(result.current.error).toBe("Gemini kotasi doldu");
    expect(getRun).not.toHaveBeenCalled();
  });

  it("hata metni olmadan biterse yerine anlasilir bir mesaj koyar", () => {
    const { result } = renderHook(() => useRunStream());
    act(() => result.current.watch("kosu-1"));

    act(() => FakeEventSource.last.emit("done", snapshot({ state: "cancelled", error: null })));

    expect(result.current.state).toBe("cancelled");
    expect(result.current.error).toBe("Çalıştırma tamamlanamadı");
  });

  it("sonuc cekilemezse durumu 'failed' yapar", async () => {
    vi.spyOn(api, "getRun").mockRejectedValue(new Error("500 Internal Server Error"));

    const { result } = renderHook(() => useRunStream());
    act(() => result.current.watch("kosu-1"));
    act(() => FakeEventSource.last.emit("done", snapshot()));

    await waitFor(() => expect(result.current.state).toBe("failed"));
    expect(result.current.error).toBe("500 Internal Server Error");
  });

  it("govdeli `error` olayini hata olarak isler", () => {
    const { result } = renderHook(() => useRunStream());
    act(() => result.current.watch("kosu-1"));
    const source = FakeEventSource.last;

    act(() => source.emit("error", { run_id: "kosu-1", detail: "Bilinmeyen çalıştırma" }));

    expect(result.current.state).toBe("failed");
    expect(result.current.error).toBe("Bilinmeyen çalıştırma");
    expect(source.closed).toBe(true);
  });

  it("govdesiz `error` olayini YOK SAYAR", () => {
    // Regresyon koruması: govdesiz `error` baglanti kopmasidir ve `EventSource`
    // kendisi yeniden baglanir. Hata sayilirsa gecici bir kesinti calisan bir
    // isi kullaniciya "basarisiz" gosterir ve akis da kapatilmis olur -- yani
    // is aslinda devam ederken arayuz onu bir daha hic gormez.
    const { result } = renderHook(() => useRunStream());
    act(() => result.current.watch("kosu-1"));
    const source = FakeEventSource.last;

    act(() => source.emit("error"));

    expect(result.current.state).toBe("running");
    expect(result.current.error).toBeNull();
    expect(source.closed).toBe(false);
  });

  it("kesintiden sonra gelen ilerleme islenmeye devam eder", () => {
    const { result } = renderHook(() => useRunStream());
    act(() => result.current.watch("kosu-1"));
    const source = FakeEventSource.last;

    act(() => source.emit("progress", { stage: "a", message: "m", progress: 0.2, current: null, total: null }));
    act(() => source.emit("error"));
    act(() => source.emit("progress", { stage: "b", message: "n", progress: 0.6, current: null, total: null }));

    expect(result.current.progress).toBe(0.6);
    expect(result.current.stage).toBe("b");
  });

  it("yeniden izlemede onceki akisi kapatir", () => {
    const { result } = renderHook(() => useRunStream());

    act(() => result.current.watch("kosu-1"));
    const first = FakeEventSource.last;
    act(() => result.current.watch("kosu-2"));
    const second = FakeEventSource.last;

    expect(first.closed).toBe(true);
    expect(second.closed).toBe(false);
    expect(second.url).toBe("/api/runs/kosu-2/events");
    expect(result.current.runId).toBe("kosu-2");
  });

  it("onceki calistirmanin olaylari yeni duruma sizmaz", () => {
    // Guvence tarayicidan geliyor: kapatilmis bir `EventSource` olay teslim
    // etmiyor. Onemli olmasinin sebebi, `done` isleyicisinin `runId`yi kapanista
    // TASIMASI ve modul duzeyindeki `close()`u cagirmasi -- kapali akistan gec
    // bir `done` gelebilseydi YENI akisi kapatir ve durumu ele gecirirdi.
    // Sahte bu sozlesmeyi taklit ediyor, dolayisiyla test hem sizintiyi hem de
    // dayandigimiz varsayimi kayda geciriyor.
    const { result } = renderHook(() => useRunStream());
    act(() => result.current.watch("kosu-1"));
    const first = FakeEventSource.last;
    act(() => result.current.watch("kosu-2"));
    const second = FakeEventSource.last;

    act(() => first.emit("done", snapshot({ run_id: "kosu-1" })));

    expect(result.current.runId).toBe("kosu-2");
    expect(result.current.state).toBe("running");
    expect(second.closed).toBe(false);
  });

  it("bilesen soekuldugunde akisi kapatir", () => {
    const { result, unmount } = renderHook(() => useRunStream());
    act(() => result.current.watch("kosu-1"));
    const source = FakeEventSource.last;

    unmount();

    expect(source.closed).toBe(true);
  });

  it("`cancel` sunucuya iptal gonderir ve akisi ACIK BIRAKIR", async () => {
    // Akisi burada kapatmak cazip ama yanlis olurdu: sunucu isi iptal edince
    // `done` olayini `state: "cancelled"` ile gonderiyor. Erken kapatilirsa o
    // son durum hic gorulmez ve arayuz "calisiyor"da asili kalir.
    const cancelRun = vi.spyOn(api, "cancelRun").mockResolvedValue(undefined);

    const { result } = renderHook(() => useRunStream());
    act(() => result.current.watch("kosu-1"));
    const source = FakeEventSource.last;

    await act(() => result.current.cancel());

    expect(cancelRun).toHaveBeenCalledWith("kosu-1");
    expect(source.closed).toBe(false);
    expect(result.current.state).toBe("running");

    // Durum gecisini sunucunun `done` olayi yapiyor.
    act(() => source.emit("done", snapshot({ state: "cancelled", error: null })));
    expect(result.current.state).toBe("cancelled");
  });

  it("izlenen calistirma yokken `cancel` istek atmaz", async () => {
    const cancelRun = vi.spyOn(api, "cancelRun");
    const { result } = renderHook(() => useRunStream());

    await act(() => result.current.cancel());

    expect(cancelRun).not.toHaveBeenCalled();
  });

  it("iptal istegi basarisiz olursa hatayi gosterir", async () => {
    vi.spyOn(api, "cancelRun").mockRejectedValue(new Error("404 Bilinmeyen çalıştırma"));

    const { result } = renderHook(() => useRunStream());
    act(() => result.current.watch("kosu-1"));

    await act(() => result.current.cancel());

    expect(result.current.error).toBe("404 Bilinmeyen çalıştırma");
  });

  it("`reset` durumu bosaltir ve akisi kapatir", () => {
    const { result } = renderHook(() => useRunStream());
    act(() => result.current.watch("kosu-1"));
    const source = FakeEventSource.last;

    act(() => result.current.reset());

    expect(source.closed).toBe(true);
    expect(result.current.runId).toBeNull();
    expect(result.current.state).toBe("idle");
  });
});
