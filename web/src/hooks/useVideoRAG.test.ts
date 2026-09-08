import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type { RagAnswer, SpaceDetail } from "../api/types";
import { useVideoRAG } from "./useVideoRAG";

afterEach(() => {
  vi.restoreAllMocks();
});

function emptySpace(): SpaceDetail {
  return {
    space_id: "sp",
    name: "Defter",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    source_count: 1,
    chunk_count: 4,
    sources: [],
  };
}

function answer(overrides: Partial<RagAnswer> = {}): RagAnswer {
  return {
    answered: true,
    answer: "Kovaryans küçülür.",
    citations: [],
    searched_sources: 3,
    reason: null,
    refusal: null,
    ...overrides,
  };
}

/** `load` her montajda calisiyor; iki uc de sahte olmali yoksa istek kaciyor. */
function stubLoad() {
  vi.spyOn(api, "getSpace").mockResolvedValue(emptySpace());
  vi.spyOn(api, "listRuns").mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
}

async function askOnce(hook: ReturnType<typeof renderHook<ReturnType<typeof useVideoRAG>, unknown>>) {
  await act(async () => {
    await hook.result.current.ask("kovaryans nasıl küçülür");
  });
}

describe("useVideoRAG / ret", () => {
  it("reddin TURUNU mesajda tasir", async () => {
    stubLoad();
    vi.spyOn(api, "ask").mockResolvedValue(
      answer({
        answered: false,
        answer: null,
        reason: "Eşleşen bölümler bulundu ama … doğrulanamadı.",
        refusal: "unverified",
      }),
    );

    const hook = renderHook(() => useVideoRAG("sp"));
    await waitFor(() => expect(hook.result.current.loading).toBe(false));
    await askOnce(hook);

    const last = hook.result.current.messages.at(-1)!;
    expect(last.refusal).toBe("unverified");
    expect(last.streaming).toBe(false);
  });

  /**
   * Ret HARF HARF akmiyor: akan bir "bulamadim" cumlesi sistemin cevabi
   * dusundugunu ima ederdi, oysa karar zaten verilmis. Test bunu tek bir
   * `act` icinde bekleyerek kilitliyor -- akis olsaydi mesaj yarim kalirdi.
   */
  it("reddi yazi animasyonu olmadan aninda koyar", async () => {
    stubLoad();
    const reason = "3 kaynakta arandı; bu soruyu karşılayan bir bölüm bulunamadı.";
    vi.spyOn(api, "ask").mockResolvedValue(
      answer({ answered: false, answer: null, reason, refusal: "not_found" }),
    );

    const hook = renderHook(() => useVideoRAG("sp"));
    await waitFor(() => expect(hook.result.current.loading).toBe(false));
    await askOnce(hook);

    const last = hook.result.current.messages.at(-1)!;
    expect(last.content).toBe(reason);
    expect(last.refusal).toBe("not_found");
  });

  /**
   * `refusal` alani sunucuya SONRADAN eklendi. Alani bilmeyen bir yanit
   * geldiginde arayuz reddi bir cevap balonu gibi cizmemeli; en genel ret
   * varsayiliyor.
   */
  it("sunucu turu soylemezse reddi yine de ret sayar", async () => {
    stubLoad();
    vi.spyOn(api, "ask").mockResolvedValue(
      answer({ answered: false, answer: null, reason: "bulunamadı", refusal: null }),
    );

    const hook = renderHook(() => useVideoRAG("sp"));
    await waitFor(() => expect(hook.result.current.loading).toBe(false));
    await askOnce(hook);

    expect(hook.result.current.messages.at(-1)!.refusal).toBe("not_found");
  });

  it("yanitlanan soruda ret isareti birakmaz", async () => {
    stubLoad();
    vi.spyOn(api, "ask").mockResolvedValue(answer());

    const hook = renderHook(() => useVideoRAG("sp"));
    await waitFor(() => expect(hook.result.current.loading).toBe(false));
    await askOnce(hook);

    const last = hook.result.current.messages.at(-1)!;
    expect(last.refusal).toBeNull();
    expect(last.content).toBe("Kovaryans küçülür.");
  });
});
