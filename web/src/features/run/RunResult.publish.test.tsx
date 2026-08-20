import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import type { PlaylistResult, Recommendation } from "../../api/types";
import { RunResult } from "./RunResult";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  vi.spyOn(api, "youtubeStatus").mockResolvedValue({
    configured: true,
    connected: true,
  } as never);
});

function result(runId: string): PlaylistResult {
  return {
    run_id: runId,
    topic: "konu",
    filters: {},
    subtopics: [],
    warnings: [],
    study_notes: [],
    published_playlist_url: null,
    recommendations: [
      {
        position: 1,
        subtopic: "alt",
        video: {
          video_id: "v1",
          url: "https://youtu.be/v1",
          title: "Video",
          channel: "Kanal",
          duration_sec: 600,
          view_count: 10,
          subscriber_count: null,
        },
        why_selected: "cunku",
        confidence_score: 9,
        transcript_status: "available",
        metadata_score: { title_relevance: 3, rationale: [] },
      } as unknown as Recommendation,
    ],
  } as unknown as PlaylistResult;
}

describe("RunResult / PublishPanel", () => {
  it("baska bir calistirmaya gecince yayinlama hatasi ekranda kalmaz", async () => {
    // Regresyon: `PublishPanel`in `key`i yoktu. React ayni konumdaki bileseni
    // yeniden kullandigi icin A calistirmasinin yayinlama hatasi, gecmisten
    // acilan B calistirmasinin ekraninda asili kaliyordu.
    vi.spyOn(api, "publish").mockRejectedValue(new Error("Kota doldu"));

    const view = render(<RunResult result={result("kosu-1")} />);
    fireEvent.click(await screen.findByRole("button", { name: "Playlist olarak yayınla" }));
    await screen.findByText("Kota doldu");

    view.rerender(<RunResult result={result("kosu-2")} />);

    await waitFor(() => expect(screen.queryByText("Kota doldu")).toBeNull());
  });
});
