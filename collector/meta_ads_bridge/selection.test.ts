import assert from "node:assert/strict";
import test from "node:test";

import { buildSearchParams, normalizeEligibleAd } from "./selection";

const baseAd = {
  id: "video-1",
  status: "ACTIVE",
  advertiser_name: "Example advertiser",
  body_variants: [],
  media_urls: ["https://cdn.example/cover.jpg"],
  video_urls: ["https://cdn.example/creative.mp4"],
  carousel_cards: [],
  platforms: ["FACEBOOK"],
  category: "ALL",
  media_type: "video",
  demographic_distribution: [],
  region_distribution: [],
  saved: false,
  scraped_at: "2026-07-22T00:00:00Z",
};

test("buildSearchParams uses broad retrieval and keeps the ACTIVE source constraint", () => {
  assert.deepEqual(buildSearchParams({ keyword: "Aviator game", country: "IN", limit: 25 }), {
    keyword: "Aviator game",
    country: "IN",
    status: "ACTIVE",
    ad_type: "all",
    limit: 25,
    fetch_details: false,
  });
});

test("normalizeEligibleAd keeps an active ad with a playable video and image cover", () => {
  const eligible = normalizeEligibleAd(baseAd);

  assert.equal(eligible?.ad_library_id, "video-1");
  assert.equal(eligible?.video_url, "https://cdn.example/creative.mp4");
  assert.equal(eligible?.thumbnail_url, "https://cdn.example/cover.jpg");
});

test("normalizeEligibleAd rejects an inactive ad", () => {
  assert.equal(normalizeEligibleAd({ ...baseAd, status: "INACTIVE" }), null);
});

test("normalizeEligibleAd rejects an ad without a playable video", () => {
  assert.equal(normalizeEligibleAd({ ...baseAd, video_urls: [] }), null);
});

test("normalizeEligibleAd rejects an ad without an image cover", () => {
  assert.equal(normalizeEligibleAd({ ...baseAd, media_urls: [] }), null);
});