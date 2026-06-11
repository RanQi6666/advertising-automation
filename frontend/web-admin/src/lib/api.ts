import type {
  AdCreativeDraft,
  AdsPlanDraft,
  Campaign,
  CopyDraft,
  CreativeAsset,
  FacebookPublishConfig,
  LandingPageSnapshot,
  MetaAdsDraftCreateResult,
  PublishJob,
  ReviewTask,
  Topic,
  VideoAsset,
  VideoStoryboardResponse,
  WorkOrder,
} from "../types/domain";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000/api/v1";

type JsonBody = Record<string, unknown> | unknown[];

class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers ?? {}),
    },
  });

  if (!response.ok) {
    const text = await response.text();
    let message = text || response.statusText;
    try {
      const parsed = JSON.parse(text) as { detail?: unknown };
      if (parsed.detail) {
        message = typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail);
      }
    } catch {
      // Keep raw text.
    }
    throw new ApiError(response.status, message);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

function post<T>(path: string, body?: JsonBody): Promise<T> {
  return request<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export const api = {
  baseUrl: API_BASE_URL,

  listWorkOrders: (limit = 50) => request<WorkOrder[]>(`/work-orders?limit=${limit}`),
  createWorkOrder: (rawContent: string) => post<WorkOrder>("/work-orders", { raw_content: rawContent }),
  createCampaignFromWorkOrder: (workOrderId: string) =>
    post<Campaign>(`/work-orders/${workOrderId}/campaign`, {}),

  listCampaigns: (limit = 50) => request<Campaign[]>(`/campaigns?limit=${limit}`),
  getCampaign: (campaignId: string) => request<Campaign>(`/campaigns/${campaignId}`),

  analyzeLandingPage: (campaignId: string, forceRefresh = false, url?: string) =>
    post<LandingPageSnapshot>(`/campaigns/${campaignId}/landing-page/analyze`, {
      force_refresh: forceRefresh,
      ...(url ? { url } : {}),
    }),
  listLandingPageSnapshots: (campaignId: string) =>
    request<LandingPageSnapshot[]>(`/campaigns/${campaignId}/landing-page/snapshots?limit=10`),

  generateTopics: (campaignId: string, limit = 3) =>
    post<Topic[]>("/topics/generate", {
      campaign_id: campaignId,
      limit,
      signals: {},
    }),
  listTopics: (campaignId: string) => request<Topic[]>(`/campaigns/${campaignId}/topics?limit=20`),
  selectTopic: (topicId: string) => post<Topic>(`/topics/${topicId}/select`),
  rejectTopic: (topicId: string) => post<Topic>(`/topics/${topicId}/reject`),

  generateCopy: (topicId: string, cta = "Learn More") =>
    post<CopyDraft>("/copywriting/generate", {
      topic_id: topicId,
      constraints: { cta },
    }),
  reviseCopy: (draftId: string, feedback: string) =>
    post<CopyDraft>(`/copywriting/${draftId}/revise`, {
      feedback,
      constraints: {},
    }),
  listDrafts: (campaignId: string) => request<CopyDraft[]>(`/campaigns/${campaignId}/drafts?limit=20`),

  generateCreatives: (draftId: string, count = 3, size = "1:1") =>
    post<CreativeAsset[]>("/creatives/generate", {
      draft_id: draftId,
      count,
      size,
    }),
  listCreatives: (campaignId: string) =>
    request<CreativeAsset[]>(`/campaigns/${campaignId}/creatives?limit=20`),

  generateVideoStoryboard: (
    campaignId: string,
    creativeAssetIds: string[],
    draftId: string | null,
    durationSeconds: number,
    aspectRatio: string,
    instructions?: string,
  ) =>
    post<VideoStoryboardResponse>("/videos/storyboard", {
      campaign_id: campaignId,
      creative_asset_ids: creativeAssetIds,
      draft_id: draftId,
      duration_seconds: durationSeconds,
      aspect_ratio: aspectRatio,
      ...(instructions?.trim() ? { instructions } : {}),
    }),
  createVideoFromImages: (payload: {
    campaignId: string;
    creativeAssetIds: string[];
    draftId?: string | null;
    prompt?: string;
    durationSeconds?: number;
    aspectRatio?: string;
    storyboard?: Record<string, unknown>[];
  }) =>
    post<VideoAsset>("/videos/from-images", {
      campaign_id: payload.campaignId,
      creative_asset_ids: payload.creativeAssetIds,
      draft_id: payload.draftId,
      prompt: payload.prompt ?? "Create a short ad video from approved images",
      duration_seconds: payload.durationSeconds ?? 12,
      aspect_ratio: payload.aspectRatio ?? "9:16",
      storyboard: payload.storyboard ?? [],
    }),
  listVideos: (campaignId: string) => request<VideoAsset[]>(`/campaigns/${campaignId}/videos?limit=20`),
  startVideoGeneration: (videoId: string) => post<VideoAsset>(`/videos/${videoId}/generate`),
  refreshVideoGeneration: (videoId: string) => post<VideoAsset>(`/videos/${videoId}/refresh`),

  submitReview: (
    entityType: string,
    entityId: string,
    decision: "approved" | "rejected" | "needs_revision",
    campaignId?: string,
    feedback?: string,
  ) =>
    post<ReviewTask>("/reviews", {
      entity_type: entityType,
      entity_id: entityId,
      campaign_id: campaignId,
      decision,
      feedback,
    }),
  listPendingReviews: () => request<ReviewTask[]>("/reviews/pending?limit=100"),

  listPublishJobs: (campaignId?: string) =>
    request<PublishJob[]>(`/publishing/jobs?limit=20${campaignId ? `&campaign_id=${campaignId}` : ""}`),
  getMetaPublishConfig: () => request<FacebookPublishConfig>("/publishing/meta-config"),
  buildAdCreativeDraft: (payload: {
    campaignId: string;
    draftId?: string | null;
    topicId?: string | null;
    creativeAssetId?: string | null;
    videoAssetId?: string | null;
    facebookVideoId?: string | null;
    pageId?: string | null;
    adAccountId?: string | null;
    destinationUrl?: string | null;
    ctaType?: string;
  }) =>
    post<AdCreativeDraft>("/publishing/ad-creative-draft", {
      campaign_id: payload.campaignId,
      draft_id: payload.draftId ?? null,
      topic_id: payload.topicId ?? null,
      creative_asset_id: payload.creativeAssetId ?? null,
      video_asset_id: payload.videoAssetId ?? null,
      facebook_video_id: payload.facebookVideoId ?? null,
      page_id: payload.pageId ?? null,
      ad_account_id: payload.adAccountId ?? null,
      destination_url: payload.destinationUrl ?? null,
      cta_type: payload.ctaType ?? "LEARN_MORE",
    }),
  buildAdsPlanDraft: (payload: {
    campaignId: string;
    draftId?: string | null;
    topicId?: string | null;
    creativeAssetId?: string | null;
    videoAssetId?: string | null;
    facebookVideoId?: string | null;
    pageId?: string | null;
    adAccountId?: string | null;
    destinationUrl?: string | null;
    ctaType?: string;
    dailyBudget?: number | null;
    pixelId?: string | null;
  }) =>
    post<AdsPlanDraft>("/publishing/ads-plan-draft", {
      campaign_id: payload.campaignId,
      draft_id: payload.draftId ?? null,
      topic_id: payload.topicId ?? null,
      creative_asset_id: payload.creativeAssetId ?? null,
      video_asset_id: payload.videoAssetId ?? null,
      facebook_video_id: payload.facebookVideoId ?? null,
      page_id: payload.pageId ?? null,
      ad_account_id: payload.adAccountId ?? null,
      destination_url: payload.destinationUrl ?? null,
      cta_type: payload.ctaType ?? "LEARN_MORE",
      daily_budget: payload.dailyBudget ?? null,
      pixel_id: payload.pixelId ?? null,
    }),
  createMetaAdsDraft: (payload: {
    campaignId: string;
    draftId: string;
    topicId?: string | null;
    creativeAssetId?: string | null;
    videoAssetId?: string | null;
    facebookVideoId?: string | null;
    pageId?: string | null;
    adAccountId?: string | null;
    destinationUrl?: string | null;
    ctaType?: string;
    dailyBudget: number;
    pixelId?: string | null;
  }) =>
    post<MetaAdsDraftCreateResult>("/publishing/meta-ads-draft", {
      campaign_id: payload.campaignId,
      draft_id: payload.draftId,
      topic_id: payload.topicId ?? null,
      creative_asset_id: payload.creativeAssetId ?? null,
      video_asset_id: payload.videoAssetId ?? null,
      facebook_video_id: payload.facebookVideoId ?? null,
      page_id: payload.pageId ?? null,
      ad_account_id: payload.adAccountId ?? null,
      destination_url: payload.destinationUrl ?? null,
      cta_type: payload.ctaType ?? "LEARN_MORE",
      daily_budget: payload.dailyBudget,
      pixel_id: payload.pixelId ?? null,
      confirm_create_paused: true,
    }),
  prepareMetaAdsPackage: (payload: {
    campaignId: string;
    draftId: string;
    topicId?: string | null;
    creativeAssetId?: string | null;
    videoAssetId?: string | null;
    facebookVideoId?: string | null;
    pageId?: string | null;
    adAccountId?: string | null;
    destinationUrl?: string | null;
    ctaType?: string;
    dailyBudget: number;
    pixelId?: string | null;
  }) =>
    post<PublishJob>("/publishing/meta-ads-package", {
      campaign_id: payload.campaignId,
      draft_id: payload.draftId,
      topic_id: payload.topicId ?? null,
      creative_asset_id: payload.creativeAssetId ?? null,
      video_asset_id: payload.videoAssetId ?? null,
      facebook_video_id: payload.facebookVideoId ?? null,
      page_id: payload.pageId ?? null,
      ad_account_id: payload.adAccountId ?? null,
      destination_url: payload.destinationUrl ?? null,
      cta_type: payload.ctaType ?? "LEARN_MORE",
      daily_budget: payload.dailyBudget,
      pixel_id: payload.pixelId ?? null,
      confirm_prepare: true,
    }),
  createPublishJob: (payload: {
    campaignId: string;
    draftId: string | null;
    channel: "facebook_page" | "facebook_ad";
    message: string;
    pageId?: string;
    adAccountId?: string;
    imageUrl?: string;
    videoAssetId?: string;
    mediaType?: "text" | "image" | "video";
    accessTokenRef?: string;
  }) =>
    post<PublishJob>("/publishing/jobs", {
      campaign_id: payload.campaignId,
      draft_id: payload.draftId,
      channel: payload.channel,
      payload: {
        media_type: payload.mediaType ?? "text",
        page_id: payload.pageId || "dry-run-page",
        ad_account_id: payload.adAccountId || "dry-run-ad-account",
        message: payload.message,
        ...(payload.accessTokenRef ? { access_token_ref: payload.accessTokenRef } : {}),
        ...(payload.imageUrl ? { image_url: payload.imageUrl } : {}),
        ...(payload.videoAssetId ? { video_asset_id: payload.videoAssetId } : {}),
      },
    }),
  publishJob: (jobId: string) => post<PublishJob>(`/publishing/jobs/${jobId}/publish`),
};

export { ApiError };
