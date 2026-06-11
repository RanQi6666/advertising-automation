export type EntityStatus = "proposed" | "selected" | "approved" | "rejected" | string;

export interface Timestamped {
  id: string;
  created_at: string;
  updated_at: string;
}

export interface WorkOrder extends Timestamped {
  raw_content: string;
  parsed_fields: Record<string, unknown>;
  project_name: string | null;
  country: string | null;
  media: string | null;
  event_name: string | null;
  product_name: string | null;
  audience_description: string | null;
  landing_url: string | null;
  report_timezone: string | null;
  status: string;
  metadata_json: Record<string, unknown>;
}

export interface Campaign extends Timestamped {
  client_id: string | null;
  brand_id: string | null;
  work_order_id: string | null;
  name: string;
  objective: string | null;
  product_name: string | null;
  audience_description: string | null;
  budget_notes: string | null;
  status: string;
  metadata_json: Record<string, unknown>;
}

export interface LandingPageSnapshot extends Timestamped {
  campaign_id: string;
  work_order_id: string | null;
  url: string;
  status: string;
  http_status: number | null;
  title: string | null;
  description: string | null;
  text_content: string | null;
  extracted_data: Record<string, unknown>;
  error_message: string | null;
  fetched_at: string;
  metadata_json: Record<string, unknown>;
}

export interface Topic extends Timestamped {
  campaign_id: string;
  title: string;
  angle: string;
  audience: string | null;
  selling_points: string[];
  risk_notes: string | null;
  rationale: string | null;
  status: string;
  score: number | null;
  source_data: Record<string, unknown>;
}

export interface CopyDraft extends Timestamped {
  campaign_id: string;
  topic_id: string;
  body: string;
  primary_text: string | null;
  headline: string | null;
  description: string | null;
  cta: string | null;
  status: string;
  version: number;
  model_name: string | null;
  prompt_version: string | null;
  metadata_json: Record<string, unknown>;
}

export interface CreativeAsset extends Timestamped {
  campaign_id: string;
  draft_id: string;
  kind: string;
  url: string | null;
  storage_key: string | null;
  prompt: string;
  alt_text: string | null;
  size: string;
  status: string;
  version: number;
  metadata_json: Record<string, unknown>;
}

export interface VideoAsset extends Timestamped {
  campaign_id: string;
  draft_id: string | null;
  source_asset_ids: string[];
  url: string | null;
  storage_key: string | null;
  prompt: string | null;
  storyboard: unknown[];
  duration_seconds: number | null;
  aspect_ratio: string;
  status: string;
  provider_job_id: string | null;
  error_message: string | null;
  version: number;
  metadata_json: Record<string, unknown>;
}

export interface VideoStoryboardResponse {
  campaign_id: string;
  draft_id: string | null;
  creative_asset_ids: string[];
  duration_seconds: number;
  aspect_ratio: string;
  storyboard: Record<string, unknown>[];
  prompt: string;
  metadata_json: Record<string, unknown>;
}

export interface PublishJob extends Timestamped {
  campaign_id: string;
  draft_id: string | null;
  channel: string;
  payload: Record<string, unknown>;
  status: string;
  external_id: string | null;
  error_message: string | null;
  scheduled_for: string | null;
  published_at: string | null;
  metadata_json: Record<string, unknown>;
}

export interface AdCreativeDraft {
  campaign_id: string;
  draft_id: string | null;
  topic_id: string | null;
  destination_url: string | null;
  headline: string;
  primary_text: string;
  description: string | null;
  media_type: string;
  creative_asset_id: string | null;
  image_url: string | null;
  video_asset_id: string | null;
  facebook_video_id: string | null;
  page_id: string | null;
  ad_account_id: string | null;
  cta_type: string;
  meta_payload: Record<string, unknown>;
  source_mapping: Record<string, unknown>;
}

export interface AdsPlanDraft {
  campaign_id: string;
  draft_id: string | null;
  topic_id: string | null;
  destination_url: string | null;
  headline: string;
  primary_text: string;
  media_type: string;
  page_id: string | null;
  ad_account_id: string | null;
  ad_creative_id: string | null;
  campaign_payload: Record<string, unknown>;
  adset_payload: Record<string, unknown>;
  creative_payload: Record<string, unknown>;
  ad_payload: Record<string, unknown>;
  meta_payload: Record<string, unknown>;
  targeting_summary: Record<string, unknown>;
  source_mapping: Record<string, unknown>;
  warnings: string[];
}

export interface MetaAdsDraftCreateResult {
  job_id: string;
  status: string;
  dry_run: boolean;
  campaign_id: string;
  draft_id: string | null;
  meta_campaign_id: string | null;
  meta_adset_id: string | null;
  meta_ad_creative_id: string | null;
  meta_ad_id: string | null;
  error_message: string | null;
  ids: Record<string, unknown>;
  responses: Record<string, unknown>;
  plan: Record<string, unknown>;
  warnings: string[];
}

export interface FacebookPublishConfig {
  dry_run: boolean;
  graph_api_version: string;
  app: {
    app_id: string | null;
    app_id_configured: boolean;
    app_secret_configured: boolean;
  };
  page: {
    id: string | null;
    id_configured: boolean;
    access_token_configured: boolean;
    access_token_ref: string;
  };
  ads: {
    ad_account_id: string | null;
    ad_account_configured: boolean;
    access_token_configured: boolean;
    access_token_ref: string;
  };
  missing_fields: string[];
}

export interface ReviewTask extends Timestamped {
  campaign_id: string | null;
  entity_type: string;
  entity_id: string;
  reviewer_id: string | null;
  status: string;
  decision: string | null;
  feedback: string | null;
  metadata_json: Record<string, unknown>;
}
