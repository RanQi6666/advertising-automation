export interface CollectRequest {
  request_id: string;
  query: string;
  country: string;
  limit: number;
  active_only: boolean;
  video_only: boolean;
}

export interface CollectorAd {
  ad_library_id: string;
  advertiser_name: string | null;
  status: string | null;
  days_running: number | null;
  text_variants: string[];
  headline: string | null;
  cta_text: string | null;
  landing_url: string | null;
  video_url: string | null;
  thumbnail_url: string | null;
  duration_seconds: number | null;
  platforms: string[];
  ad_snapshot_url: string | null;
  reported_spend_range: { min: number | null; max: number | null; currency: string | null } | null;
}
