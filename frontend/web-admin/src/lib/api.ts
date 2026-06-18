import type {
  AdGenerationJob,
  AdGenerationJobAccepted,
  Campaign,
  CopyDraft,
  CreativeAsset,
  LandingPageSnapshot,
  ReviewTask,
  Topic,
  VideoAsset,
  VideoStoryboardResponse,
  WorkOrder,
  ReviewedDeliveryFields,
  WorkOrderDeliveryExtraction,
} from "../types/domain";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8001/api/v1";
const ACCESS_TOKEN_STORAGE_KEY = "ai_ads_access_token";

type JsonBody = Record<string, unknown> | unknown[];
export type TopicStreamEvent =
  | { type: "start"; limit: number }
  | { type: "slot"; index: number }
  | { type: "topic"; index: number; topic: Topic }
  | { type: "error"; index?: number; message: string }
  | { type: "done"; generated?: number };
export type CreativeStreamEvent =
  | { type: "start"; limit: number; indices?: number[] }
  | { type: "slot"; index: number }
  | { type: "asset"; index: number; asset: CreativeAsset }
  | { type: "error"; index?: number; message: string }
  | { type: "done"; generated?: number };
export type VideoStoryboardTextStreamEvent =
  | { type: "start"; duration_seconds: number; aspect_ratio: string }
  | { type: "delta"; text: string }
  | { type: "error"; message: string }
  | { type: "done"; duration_seconds: number; aspect_ratio: string; text?: string };

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
    headers: requestHeaders(options.headers),
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

async function streamNdjson<TEvent>(
  path: string,
  body: JsonBody,
  onEvent: (event: TEvent) => void,
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: requestHeaders(),
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, text || response.statusText);
  }
  if (!response.body) {
    throw new ApiError(response.status, "Streaming response is not available.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  function processLine(line: string) {
    const trimmed = line.trim();
    if (!trimmed) return;
    onEvent(JSON.parse(trimmed) as TEvent);
  }

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newlineIndex = buffer.indexOf("\n");
    while (newlineIndex >= 0) {
      const line = buffer.slice(0, newlineIndex);
      buffer = buffer.slice(newlineIndex + 1);
      processLine(line);
      newlineIndex = buffer.indexOf("\n");
    }
  }

  buffer += decoder.decode();
  processLine(buffer);
}

async function streamSse<TEvent>(
  path: string,
  body: JsonBody,
  onEvent: (event: TEvent) => void,
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: requestHeaders({ Accept: "text/event-stream" }),
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, text || response.statusText);
  }
  if (!response.body) {
    throw new ApiError(response.status, "Streaming response is not available.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  function processBlock(block: string) {
    const lines = block.split("\n");
    const dataLines: string[] = [];
    for (const line of lines) {
      if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    }
    const data = dataLines.join("\n").trim();
    if (!data) return;
    onEvent(JSON.parse(data) as TEvent);
  }

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    let boundaryIndex = buffer.indexOf("\n\n");
    while (boundaryIndex >= 0) {
      const block = buffer.slice(0, boundaryIndex);
      buffer = buffer.slice(boundaryIndex + 2);
      processBlock(block);
      boundaryIndex = buffer.indexOf("\n\n");
    }
  }

  buffer += decoder.decode().replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  if (buffer.trim()) processBlock(buffer);
}

export const api = {
  baseUrl: API_BASE_URL,

  listWorkOrders: (limit = 50) => request<WorkOrder[]>(`/work-orders?limit=${limit}`),
  listAdGenerationJobs: (limit = 50) =>
    request<AdGenerationJob[]>(`/integrations/publishing/ad-generation/jobs?limit=${limit}`),
  getAdGenerationJob: (jobId: string) =>
    request<AdGenerationJob>(`/integrations/publishing/ad-generation/jobs/${jobId}`),
  getAdGenerationResult: (jobId: string) =>
    request<Record<string, unknown>>(
      `/integrations/publishing/ad-generation/jobs/${jobId}/result`,
    ),
  deleteAdGenerationJob: (jobId: string) =>
    request<void>(`/integrations/publishing/ad-generation/jobs/${jobId}`, {
      method: "DELETE",
    }),
  createAdGenerationJob: (payload: {
    rawContent: string;
    structuredFields: Record<string, unknown>;
    deliveryExtraction?: WorkOrderDeliveryExtraction | null;
    externalOrderId?: string | null;
    returnUrl?: string | null;
    callbackUrl?: string | null;
    creativeType?: "image" | "video" | "carousel";
    imageCount?: number;
    dailyBudget?: number | null;
  }) =>
    post<AdGenerationJobAccepted>("/integrations/publishing/ad-generation/jobs", {
      ...(payload.externalOrderId ? { external_order_id: payload.externalOrderId } : {}),
      ...(payload.returnUrl ? { return_url: payload.returnUrl } : {}),
      ...(payload.callbackUrl ? { callback_url: payload.callbackUrl } : {}),
      work_order: {
        raw_content: payload.rawContent,
        structured_fields: payload.structuredFields,
        delivery_extraction: payload.deliveryExtraction ?? null,
      },
      preferences: {
        creative_type: payload.creativeType ?? "image",
        image_count: payload.imageCount ?? 1,
        daily_budget: payload.dailyBudget ?? 5000,
      },
    }),
  updateAdGenerationReview: (
    jobId: string,
    resultPayload: Record<string, unknown>,
    reviewNotes?: string,
  ) =>
    request<AdGenerationJob>(`/integrations/publishing/ad-generation/jobs/${jobId}/review`, {
      method: "PATCH",
      body: JSON.stringify({
        result_payload: resultPayload,
        review_notes: reviewNotes ?? null,
      }),
    }),
  confirmAdGenerationReview: (
    jobId: string,
    resultPayload?: Record<string, unknown>,
    reviewNotes?: string,
  ) =>
    post<AdGenerationJob>(`/integrations/publishing/ad-generation/jobs/${jobId}/confirm`, {
      result_payload: resultPayload ?? null,
      review_notes: reviewNotes ?? null,
    }),
  extractWorkOrderDeliveryFields: (rawContent: string) =>
    post<WorkOrderDeliveryExtraction>("/work-orders/extract-delivery-fields", {
      raw_content: rawContent,
    }),
  createWorkOrder: (
    rawContent: string,
    reviewedDeliveryFields?: ReviewedDeliveryFields,
    llmDeliveryFields?: WorkOrderDeliveryExtraction,
  ) =>
    post<WorkOrder>("/work-orders", {
      raw_content: rawContent,
      reviewed_delivery_fields: reviewedDeliveryFields ?? {},
      llm_delivery_fields: llmDeliveryFields ?? {},
    }),
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

  generateTopics: (campaignId: string, limit = 3, signals: Record<string, unknown> = {}) =>
    post<Topic[]>("/topics/generate", {
      campaign_id: campaignId,
      limit,
      signals,
    }),
  generateTopicsStream: (
    campaignId: string,
    limit = 3,
    signals: Record<string, unknown> = {},
    onEvent: (event: TopicStreamEvent) => void,
  ) =>
    streamNdjson(
      "/topics/generate/stream",
      {
        campaign_id: campaignId,
        limit,
        signals,
      },
      onEvent,
    ),
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
  generateCreativesStream: (
    draftId: string,
    count = 3,
    size = "1:1",
    onEvent: (event: CreativeStreamEvent) => void,
    targetIndex?: number,
  ) =>
    streamNdjson<CreativeStreamEvent>(
      "/creatives/generate/stream",
      {
        draft_id: draftId,
        count,
        size,
        ...(targetIndex ? { target_index: targetIndex } : {}),
      },
      onEvent,
    ),
  regenerateCreative: (creativeId: string, feedback: string, size?: string) =>
    post<CreativeAsset>(`/creatives/${creativeId}/regenerate`, {
      feedback,
      ...(size ? { size } : {}),
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
  streamVideoStoryboard: (
    campaignId: string,
    creativeAssetIds: string[],
    draftId: string | null,
    durationSeconds: number,
    aspectRatio: string,
    instructions: string | undefined,
    onEvent: (event: VideoStoryboardTextStreamEvent) => void,
  ) =>
    streamSse<VideoStoryboardTextStreamEvent>(
      "/videos/storyboard/stream",
      {
        campaign_id: campaignId,
        creative_asset_ids: creativeAssetIds,
        draft_id: draftId,
        duration_seconds: durationSeconds,
        aspect_ratio: aspectRatio,
        ...(instructions?.trim() ? { instructions } : {}),
      },
      onEvent,
    ),
  rewriteVideoStoryboard: (payload: {
    campaignId: string;
    creativeAssetIds: string[];
    draftId: string | null;
    durationSeconds: number;
    aspectRatio: string;
    storyboard: Record<string, unknown>[];
    storyboardText: string;
    feedback: string;
  }) =>
    post<VideoStoryboardResponse>("/videos/storyboard/rewrite", {
      campaign_id: payload.campaignId,
      creative_asset_ids: payload.creativeAssetIds,
      draft_id: payload.draftId,
      duration_seconds: payload.durationSeconds,
      aspect_ratio: payload.aspectRatio,
      storyboard: payload.storyboard,
      storyboard_text: payload.storyboardText,
      feedback: payload.feedback,
    }),
  streamRewriteVideoStoryboard: (
    payload: {
      campaignId: string;
      creativeAssetIds: string[];
      draftId: string | null;
      durationSeconds: number;
      aspectRatio: string;
      storyboard: Record<string, unknown>[];
      storyboardText: string;
      feedback: string;
    },
    onEvent: (event: VideoStoryboardTextStreamEvent) => void,
  ) =>
    streamSse<VideoStoryboardTextStreamEvent>(
      "/videos/storyboard/rewrite/stream",
      {
        campaign_id: payload.campaignId,
        creative_asset_ids: payload.creativeAssetIds,
        draft_id: payload.draftId,
        duration_seconds: payload.durationSeconds,
        aspect_ratio: payload.aspectRatio,
        storyboard: payload.storyboard,
        storyboard_text: payload.storyboardText,
        feedback: payload.feedback,
      },
      onEvent,
    ),
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
};

export { ApiError };

function requestHeaders(headers: HeadersInit = {}): HeadersInit {
  const accessToken = getAccessToken();
  return {
    "Content-Type": "application/json",
    ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
    ...(headers as Record<string, string>),
  };
}

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;

  const tokenFromUrl = new URLSearchParams(window.location.search).get("access_token")?.trim();
  if (tokenFromUrl) {
    window.sessionStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, tokenFromUrl);
    return tokenFromUrl;
  }

  return window.sessionStorage.getItem(ACCESS_TOKEN_STORAGE_KEY);
}
