import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import type { RunSummary } from "../../api/types";
import { HistoryList } from "./HistoryList";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function runs(count: number, prefix = "k"): RunSummary[] {
  return Array.from({ length: count }, (_value, index) => ({
    run_id: `${prefix}${index}`,
    topic: `${prefix} konu ${index}`,
    created_at: "2026-01-01T00:00:00Z",
    is_complete: true,
  })) as RunSummary[];
}

describe("HistoryList", () => {
  it("sayfanin son ogesi silinince onceki sayfaya duser", async () => {
    // Regresyon: 2. sayfadaki tek oge silininde sunucu bos dizi donuyor,
    // "Henuz calistirma yok" gosteriliyordu. Ayni kosul sayfalama dugmelerini
    // de gizledigi icin 1. sayfaya donus yolu KALMIYORDU.
    const listRuns = vi.spyOn(api, "listRuns");
    listRuns.mockImplementation(async (_limit, offset) => {
      if (offset === 10) return { items: runs(1, "ikinci"), total: 11, offset: 10, limit: 10 };
      return { items: runs(10), total: 11, offset: 0, limit: 10 };
    });
    vi.spyOn(api, "deleteRun").mockResolvedValue(undefined as never);

    render(<HistoryList onOpen={vi.fn()} />);
    await screen.findByText("k konu 0");

    fireEvent.click(screen.getByRole("button", { name: "Sonraki" }));
    await screen.findByText("ikinci konu 0");

    // Sayfadaki TEK ogeyi sil -> 2. sayfa bosaliyor.
    listRuns.mockImplementation(async (_limit, offset) => {
      if (offset === 10) return { items: [], total: 10, offset: 10, limit: 10 };
      return { items: runs(10), total: 10, offset: 0, limit: 10 };
    });
    fireEvent.click(screen.getAllByRole("button", { name: "Sil" })[0]);

    // Bos ekran DEGIL, 1. sayfa gelmeli.
    await screen.findByText("k konu 0");
    expect(screen.queryByText("Henüz bir dersin yok.")).toBeNull();
  });

  it("gercekten bos gecmiste bos mesajini gosterir", async () => {
    vi.spyOn(api, "listRuns").mockResolvedValue({
      items: [],
      total: 0,
      offset: 0,
      limit: 10,
    } as never);

    render(<HistoryList onOpen={vi.fn()} />);

    await waitFor(() => expect(screen.getByText("Henüz bir dersin yok.")).toBeTruthy());
  });
});
