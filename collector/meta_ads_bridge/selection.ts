import type { CollectorAd } from "./contracts";

type RawAd = {
  id: string;
  advertiser_name?: string;
  status?: string;
  days_running?: number;
  body_variants?: string[];
  headline?: string;
  cta_text?: string;
  link_url?: string;
  video_urls?: string[];
  media_urls?: string[];
  platforms?: string[];
  ad_snapshot_url?: string;
  spend_min?: number;
  spend_max?: number;
  spend_currency?: string;
};

type SearchInput = {
  keyword: string;
  country: string;
  limit: number;
};

export function buildSearchParams(input: SearchInput) {
  return {
    keyword: input.keyword,
    country: input.country,
    // Meta's `ad_type=video` search response has been observed to omit valid
    // video creatives. Retrieve broadly, then enforce the video requirement on
    // fields parsed from the actual ad record below.
    ad_type: "all" as const,
    status: "ACTIVE" as const,
    limit: input.limit,
    // Upstream details are transparency metadata only; they do not provide the
    // playable media URL or image cover required by this collector.
    fetch_details: false,
  };
}

export function normalizeEligibleAd(ad: RawAd): CollectorAd | null {
  const video_url = firstText(ad.video_urls);
  const thumbnail_url = firstText(ad.media_urls);
  if ((ad.status || "").toUpperCase() !== "ACTIVE" || !video_url || !thumbnail_url) {
    return null;
  }
  return {
    ad_library_id: ad.id,
    advertiser_name: optionalText(ad.advertiser_name),
    status: "ACTIVE",
    days_running: typeof ad.days_running === "number" ? ad.days_running : null,
    text_variants: Array.isArray(ad.body_variants) ? ad.body_variants : [],
    headline: optionalText(ad.headline),
    cta_text: optionalText(ad.cta_text),
    landing_url: optionalText(ad.link_url),
    video_url,
    thumbnail_url,
    duration_seconds: null,
    platforms: Array.isArray(ad.platforms) ? ad.platforms : [],
    ad_snapshot_url: optionalText(ad.ad_snapshot_url),
    reported_spend_range: {
      min: ad.spend_min ?? null,
      max: ad.spend_max ?? null,
      currency: ad.spend_currency ?? null,
    },
  };
}

function firstText(values: string[] | undefined): string | null {
  const value = values?.find((item) => typeof item === "string" && item.trim());
  return value?.trim() || null;
}

function optionalText(value: string | undefined): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}