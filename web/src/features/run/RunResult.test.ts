import { describe, expect, it } from "vitest";

import type { MetadataScore, Recommendation } from "../../api/types";
import { isWeakMatch } from "./RunResult";

function score(overrides: Partial<MetadataScore> = {}): MetadataScore {
  return {
    total: 8,
    title_relevance: 2.0,
    description_relevance: 1.0,
    channel_quality: 1.5,
    duration_fit: 2.0,
    difficulty_fit: 0.5,
    language_match: 1.5,
    freshness: 1.0,
    engagement: 0.5,
    rationale: [],
    ...overrides,
  };
}

function recommendation(
  confidence: number,
  metadata: Partial<MetadataScore> = {},
): Recommendation {
  return {
    position: 1,
    subtopic: "Alt konu",
    video: {
      video_id: "v1234567890",
      url: "https://youtu.be/v1234567890",
      title: "Video",
      description: "",
      channel: null,
      subscriber_count: null,
      duration_sec: 600,
      view_count: null,
      publish_date: null,
      language: null,
      is_live: false,
      metadata_score: 8,
      discovery_provider: null,
    },
    why_selected: "",
    confidence_score: confidence,
    transcript_status: "unavailable",
    transcript_source: null,
    metadata_score: score(metadata),
  };
}

describe("isWeakMatch", () => {
  it("guveni dusuk oneriyi zayif sayar", () => {
    expect(isWeakMatch(recommendation(5.5))).toBe(true);
  });

  it("hem guveni hem alakasi iyi olani zayif SAYMAZ", () => {
    expect(isWeakMatch(recommendation(8.5, { title_relevance: 2.4 }))).toBe(false);
  });

  it("guven yuksek olsa da baslik alakasi yoksa zayif sayar", () => {
    // Regresyon: eski kural yalnizca `confidence_score < 7` bakiyordu ve guven
    // TOPLAM puandan turuyor. Uzun/guncel/populer bir video, alt basliktan
    // hicbir kelime eslesmese bile 8.6 guven alip UYARISIZ gosteriliyordu.
    // Kayitli kosularda olculdu: baslik alakasi ~0 olan 6 secimin 3'u boyleydi.
    const confidentButOffTopic = recommendation(8.61, { title_relevance: 0.0 });

    expect(confidentButOffTopic.confidence_score).toBeGreaterThan(7);
    expect(isWeakMatch(confidentButOffTopic)).toBe(true);
  });

  it("esigin hemen ustundeki alakayi zayif saymaz", () => {
    expect(isWeakMatch(recommendation(8.0, { title_relevance: 0.6 }))).toBe(false);
  });
});
