import type {
  AdGenerationJob,
  AdGenerationJobAccepted,
  AdPerformanceAIAnalysis,
  AdPerformanceAnalysis,
  Campaign,
  CopyDraft,
  CreativeAsset,
  LandingPageSnapshot,
  ModelOptions,
  OperatorUser,
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
const OPERATOR_ID_STORAGE_KEY = "ai_ads_operator_id";
const EXTERNAL_AI_TRANSIENT_MESSAGE =
  "外部 AI 服务短暂波动，任务可能仍在处理中，请稍后查看结果或重试。";

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
  | { type: "heartbeat"; stage: string; pending_indices?: number[]; interval_seconds?: number }
  | { type: "asset"; index: number; asset: CreativeAsset }
  | { type: "error"; index?: number; message: string }
  | { type: "done"; generated?: number };
export type VideoStoryboardTextStreamEvent =
  | { type: "start"; duration_seconds: number; aspect_ratio: string }
  | { type: "heartbeat"; stage: string; interval_seconds?: number }
  | { type: "delta"; text: string }
  | { type: "error"; message: string }
  | { type: "done"; duration_seconds: number; aspect_ratio: string; text?: string };
export type AdPerformanceAnalysisStreamEvent =
  | { type: "start"; analysis_id: string }
  | { type: "delta"; analysis_id: string; text: string }
  | { type: "error"; analysis_id?: string; message: string }
  | { type: "done"; analysis_id: string; ai_analysis: AdPerformanceAIAnalysis };

class ApiError extends Error {
  status: number;
  rawMessage: string;
  isTransient: boolean;

  constructor(status: number, message: string) {
    super(normalizeApiErrorMessage(status, message));
    this.name = "ApiError";
    this.status = status;
    this.rawMessage = message;
    this.isTransient = isTransientApiProblem(status, message);
  }
}

async function safeFetch(url: string, options: RequestInit): Promise<Response> {
  try {
    return await fetch(url, options);
  } catch (caught) {
    const message =
      caught instanceof Error ? caught.message : "Network request failed.";
    throw new ApiError(0, message);
  }
}

async function readErrorMessage(response: Response): Promise<string> {
  const text = await response.text();
  let message = text || response.statusText || `HTTP ${response.status}`;
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (parsed.detail) {
      message =
        typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail);
    }
  } catch {
    // Keep raw text.
  }
  return message;
}

function normalizeApiErrorMessage(status: number, message: string): string {
  if (isTransientApiProblem(status, message)) return EXTERNAL_AI_TRANSIENT_MESSAGE;
  return message || (status ? `HTTP ${status}` : "网络请求失败");
}

function isTransientApiProblem(status: number, message: string): boolean {
  const normalized = (message || "").toLowerCase();
  return (
    [408, 502, 503, 504].includes(status) ||
    normalized.includes("bad gateway") ||
    normalized.includes("gateway timeout") ||
    normalized.includes("timeout") ||
    normalized.includes("timed out") ||
    normalized.includes("temporarily unavailable") ||
    normalized.includes("service unavailable") ||
    normalized.includes("failed to fetch") ||
    normalized.includes("networkerror") ||
    normalized.includes("network request failed") ||
    normalized.includes("load failed")
  );
}

function isTransientApiError(caught: unknown): boolean {
  if (caught instanceof ApiError) return caught.isTransient;
  if (caught instanceof Error) return isTransientApiProblem(0, caught.message);
  return typeof caught === "string" && isTransientApiProblem(0, caught);
}

function apiErrorMessage(caught: unknown, fallback: string): string {
  if (caught instanceof ApiError) return caught.message || fallback;
  if (caught instanceof Error) {
    if (isTransientApiProblem(0, caught.message)) return EXTERNAL_AI_TRANSIENT_MESSAGE;
    return caught.message || fallback;
  }
  if (typeof caught === "string") {
    return isTransientApiProblem(0, caught) ? EXTERNAL_AI_TRANSIENT_MESSAGE : caught || fallback;
  }
  return fallback;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await safeFetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: requestHeaders(options.headers),
  });

  if (!response.ok) {
    const message = await readErrorMessage(response);
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
  const response = await safeFetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: requestHeaders(),
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    throw new ApiError(response.status, await readErrorMessage(response));
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
  const response = await safeFetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: requestHeaders({ Accept: "text/event-stream" }),
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    throw new ApiError(response.status, await readErrorMessage(response));
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

  listOperators: () => request<OperatorUser[]>("/operators"),
  listWorkOrders: (limit = 50) => request<WorkOrder[]>(`/work-orders?limit=${limit}`),
  getModelOptions: () => request<ModelOptions>("/model-options"),
  listAdGenerationJobs: (limit = 50) =>
    request<AdGenerationJob[]>(`/integrations/publishing/ad-generation/jobs?limit=${limit}`),
  listAdPerformanceAnalyses: (limit = 50) =>
    request<AdPerformanceAnalysis[]>(`/integrations/ad-performance/analyses?limit=${limit}`),
  createAdPerformanceAnalysis: (payload: Record<string, unknown>) =>
    post<AdPerformanceAnalysis>("/integrations/ad-performance/analyses", payload),
  getAdPerformanceAnalysis: (analysisId: string) =>
    request<AdPerformanceAnalysis>(`/integrations/ad-performance/analyses/${analysisId}`),
  claimAdPerformanceAnalysis: (analysisId: string) =>
    post<AdPerformanceAnalysis>(`/integrations/ad-performance/analyses/${analysisId}/claim`, {}),
  deleteAdPerformanceAnalysis: (analysisId: string) =>
    request<void>(`/integrations/ad-performance/analyses/${analysisId}`, {
      method: "DELETE",
    }),
  streamAdPerformanceAnalysis: (
    analysisId: string,
    onEvent: (event: AdPerformanceAnalysisStreamEvent) => void,
  ) =>
    streamSse<AdPerformanceAnalysisStreamEvent>(
      `/integrations/ad-performance/analyses/${analysisId}/ai-analysis/stream`,
      {},
      onEvent,
    ),
  getAdGenerationJob: (jobId: string) =>
    request<AdGenerationJob>(`/integrations/publishing/ad-generation/jobs/${jobId}`),
  claimAdGenerationJob: (jobId: string) =>
    post<AdGenerationJob>(`/integrations/publishing/ad-generation/jobs/${jobId}/claim`, {}),
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
    expectedUpdatedAt?: string | null,
  ) =>
    request<AdGenerationJob>(`/integrations/publishing/ad-generation/jobs/${jobId}/review`, {
      method: "PATCH",
      body: JSON.stringify({
        result_payload: resultPayload,
        review_notes: reviewNotes ?? null,
        expected_updated_at: expectedUpdatedAt ?? null,
      }),
    }),
  confirmAdGenerationReview: (
    jobId: string,
    resultPayload?: Record<string, unknown>,
    reviewNotes?: string,
    expectedUpdatedAt?: string | null,
  ) =>
    post<AdGenerationJob>(`/integrations/publishing/ad-generation/jobs/${jobId}/confirm`, {
      result_payload: resultPayload ?? null,
      review_notes: reviewNotes ?? null,
      expected_updated_at: expectedUpdatedAt ?? null,
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

  generateTopics: (
    campaignId: string,
    limit = 3,
    signals: Record<string, unknown> = {},
    modelId?: string | null,
  ) =>
    post<Topic[]>("/topics/generate", {
      campaign_id: campaignId,
      limit,
      signals,
      ...(modelId ? { model_id: modelId } : {}),
    }),
  generateTopicsStream: (
    campaignId: string,
    limit = 3,
    signals: Record<string, unknown> = {},
    onEvent: (event: TopicStreamEvent) => void,
    modelId?: string | null,
  ) =>
    streamNdjson(
      "/topics/generate/stream",
      {
        campaign_id: campaignId,
        limit,
        signals,
        ...(modelId ? { model_id: modelId } : {}),
      },
      onEvent,
    ),
  listTopics: (campaignId: string) => request<Topic[]>(`/campaigns/${campaignId}/topics?limit=20`),
  selectTopic: (topicId: string) => post<Topic>(`/topics/${topicId}/select`),
  rejectTopic: (topicId: string) => post<Topic>(`/topics/${topicId}/reject`),

  generateCopy: (topicId: string, cta = "Learn More", modelId?: string | null) =>
    post<CopyDraft>("/copywriting/generate", {
      topic_id: topicId,
      constraints: { cta },
      ...(modelId ? { model_id: modelId } : {}),
    }),
  reviseCopy: (draftId: string, feedback: string, modelId?: string | null) =>
    post<CopyDraft>(`/copywriting/${draftId}/revise`, {
      feedback,
      constraints: {},
      ...(modelId ? { model_id: modelId } : {}),
    }),
  listDrafts: (campaignId: string) => request<CopyDraft[]>(`/campaigns/${campaignId}/drafts?limit=20`),

  generateCreatives: (
    draftId: string,
    count = 3,
    size = "1:1",
    options: {
      modelId?: string | null;
      storyboard?: Record<string, unknown>[];
      storyboardText?: string | null;
      generationMode?: "standard" | "video_keyframe_variants";
      variantCount?: number;
      framesPerVariant?: number;
      videoDurationSeconds?: number | null;
    } = {},
  ) =>
    post<CreativeAsset[]>("/creatives/generate", {
      draft_id: draftId,
      count,
      size,
      ...(options.modelId ? { model_id: options.modelId } : {}),
      ...(options.storyboard?.length ? { storyboard: options.storyboard } : {}),
      ...(options.storyboardText?.trim() ? { storyboard_text: options.storyboardText } : {}),
      ...(options.generationMode ? { generation_mode: options.generationMode } : {}),
      ...(options.variantCount ? { variant_count: options.variantCount } : {}),
      ...(options.framesPerVariant ? { frames_per_variant: options.framesPerVariant } : {}),
      ...(options.videoDurationSeconds ? { video_duration_seconds: options.videoDurationSeconds } : {}),
    }),
  generateCreativesStream: (
    draftId: string,
    count = 3,
    size = "1:1",
    onEvent: (event: CreativeStreamEvent) => void,
    targetIndex?: number,
    options: {
      modelId?: string | null;
      storyboard?: Record<string, unknown>[];
      storyboardText?: string | null;
      generationMode?: "standard" | "video_keyframe_variants";
      variantCount?: number;
      framesPerVariant?: number;
      videoDurationSeconds?: number | null;
    } = {},
  ) =>
    streamNdjson<CreativeStreamEvent>(
      "/creatives/generate/stream",
      {
        draft_id: draftId,
        count,
        size,
        ...(targetIndex ? { target_index: targetIndex } : {}),
        ...(options.modelId ? { model_id: options.modelId } : {}),
        ...(options.storyboard?.length ? { storyboard: options.storyboard } : {}),
        ...(options.storyboardText?.trim() ? { storyboard_text: options.storyboardText } : {}),
        ...(options.generationMode ? { generation_mode: options.generationMode } : {}),
        ...(options.variantCount ? { variant_count: options.variantCount } : {}),
        ...(options.framesPerVariant ? { frames_per_variant: options.framesPerVariant } : {}),
        ...(options.videoDurationSeconds ? { video_duration_seconds: options.videoDurationSeconds } : {}),
      },
      onEvent,
    ),
  regenerateCreative: (
    creativeId: string,
    feedback: string,
    size?: string,
    modelId?: string | null,
  ) =>
    post<CreativeAsset>(`/creatives/${creativeId}/regenerate`, {
      feedback,
      ...(size ? { size } : {}),
      ...(modelId ? { model_id: modelId } : {}),
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
    modelId?: string | null,
  ) =>
    post<VideoStoryboardResponse>("/videos/storyboard", {
      campaign_id: campaignId,
      creative_asset_ids: creativeAssetIds,
      draft_id: draftId,
      duration_seconds: durationSeconds,
      aspect_ratio: aspectRatio,
      ...(instructions?.trim() ? { instructions } : {}),
      ...(modelId ? { model_id: modelId } : {}),
    }),
  streamVideoStoryboard: (
    campaignId: string,
    creativeAssetIds: string[],
    draftId: string | null,
    durationSeconds: number,
    aspectRatio: string,
    instructions: string | undefined,
    onEvent: (event: VideoStoryboardTextStreamEvent) => void,
    modelId?: string | null,
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
        ...(modelId ? { model_id: modelId } : {}),
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
    modelId?: string | null;
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
      ...(payload.modelId ? { model_id: payload.modelId } : {}),
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
      modelId?: string | null;
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
        ...(payload.modelId ? { model_id: payload.modelId } : {}),
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

export { ApiError, EXTERNAL_AI_TRANSIENT_MESSAGE, apiErrorMessage, isTransientApiError };

function requestHeaders(headers: HeadersInit = {}): HeadersInit {
  const accessToken = getAccessToken();
  const operatorId = getOperatorId();
  return {
    "Content-Type": "application/json",
    ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
    ...(operatorId ? { "X-Operator-Id": operatorId } : {}),
    ...(headers as Record<string, string>),
  };
}

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;

  const params = new URLSearchParams(window.location.search);
  const tokenFromUrl =
    params.get("access_token")?.trim() || params.get("ai_access_token")?.trim();
  if (tokenFromUrl) {
    window.sessionStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, tokenFromUrl);
    return tokenFromUrl;
  }

  return window.sessionStorage.getItem(ACCESS_TOKEN_STORAGE_KEY);
}

export function getOperatorId(): string | null {
  if (typeof window === "undefined") return null;
  return window.sessionStorage.getItem(OPERATOR_ID_STORAGE_KEY);
}

export function setOperatorId(operatorId: string | null): void {
  if (typeof window === "undefined") return;
  if (operatorId) {
    window.sessionStorage.setItem(OPERATOR_ID_STORAGE_KEY, operatorId);
  } else {
    window.sessionStorage.removeItem(OPERATOR_ID_STORAGE_KEY);
  }
}
