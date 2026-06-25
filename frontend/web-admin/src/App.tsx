import {
  BarChart3,
  Check,
  ClipboardList,
  Clock3,
  FileText,
  Film,
  Image,
  Loader2,
  RefreshCw,
  Send,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { type FormEvent, useEffect, useMemo, useState } from "react";

import { ApiError, api, apiErrorMessage, getAccessToken, isTransientApiError } from "./lib/api";
import {
  formatManualAdPerformanceJson,
  parseManualAdPerformanceJson,
  type ManualAdPerformancePayload,
} from "./lib/adPerformanceManualJson";
import {
  brandSafetyBlocksReturn,
  brandSafetyReportFromPayload,
  brandSafetySummaryLabel,
} from "./lib/brandSafety";
import { displayAssetUrl } from "./lib/assetUrls";
import type {
  AdPerformanceAnalysisStreamEvent,
  CreativeStreamEvent,
  TopicStreamEvent,
  VideoStoryboardTextStreamEvent,
} from "./lib/api";
import type {
  AdGenerationJob,
  AdPerformanceAnalysis,
  AdPerformanceDataCompleteness,
  AdPerformanceOptimizationFieldAdvice,
  AdPerformanceOptimizationWorkOrder,
  AdPerformanceVisualAnalysis,
  Campaign,
  CopyDraft,
  CreativeAsset,
  ReviewedDeliveryFields,
  Topic,
  VideoAsset,
  VideoStoryboardResponse,
  WorkOrderDeliveryExtraction,
  WorkOrderDeliveryField,
} from "./types/domain";

type ViewKey =
  | "dashboard"
  | "work-orders"
  | "workflow"
  | "topics"
  | "copy"
  | "creatives"
  | "videos"
  | "performance";
type ErrorScope =
  | "global"
  | "refresh"
  | "work-order"
  | "topic"
  | "copy"
  | "image"
  | "video"
  | "performance"
  | "final";
type ReviewEntityType = "topic" | "copy_draft" | "creative_asset" | "video_asset";
type ReviewDecision = "approved" | "rejected" | "needs_revision";
type ScopedAppError = {
  scope: ErrorScope;
  message: string;
  transient: boolean;
  createdAt: number;
};
type WorkflowArtifact = {
  id: string;
  created_at: string;
  updated_at: string;
};
type WorkflowArtifactSnapshot = {
  topics: Topic[];
  drafts: CopyDraft[];
  creatives: CreativeAsset[];
  videos: VideoAsset[];
};
type WorkflowStepStatus = "done" | "active" | "blocked";
type DeliveryExtractionCacheEntry = {
  key: string;
  extraction: WorkOrderDeliveryExtraction;
  savedAt: string;
};
type TopicGenerationSlot = {
  index: number;
  status: "loading" | "done" | "error";
  topic?: Topic;
  message?: string;
};
type CreativeGenerationSlot = {
  index: number;
  status: "loading" | "done" | "error";
  asset?: CreativeAsset;
  message?: string;
};
type VideoPollWarning = {
  message: string;
  updatedAt: number;
};
type VideoStoryboardDraftCache = {
  campaignId: string;
  aspectRatio: string;
  durationSeconds: number;
  instructions: string;
  selectedCreativeIds: string[];
  storyboard: Record<string, unknown>[];
  storyboardText: string;
  storyboardDirty: boolean;
  storyboardFeedback: string;
  savedAt: string;
};
type AdGenerationIntegrationParams = {
  externalOrderId: string | null;
  returnUrl: string | null;
  callbackUrl: string | null;
};

const VIDEO_MAX_REFERENCE_IMAGES = 2;
const TOPIC_GENERATION_LIMIT = 3;
const CREATIVE_GENERATION_LIMIT = 3;
const VIDEO_STORYBOARD_DRAFT_CACHE_PREFIX = "video_storyboard_draft_v1:";
const VIDEO_STORYBOARD_DRAFT_LAST_CACHE_KEY = "video_storyboard_draft_v1:last";
const DELIVERY_EXTRACTION_CACHE_PREFIX = "ad_delivery_extraction_v1:";
const DELIVERY_EXTRACTION_CACHE_INDEX_KEY = "ad_delivery_extraction_v1:index";
const DELIVERY_EXTRACTION_CACHE_LIMIT = 12;
const DEFAULT_DELIVERY_EVENT_OPTION = "购买 (PURCHASE)";
const DELIVERY_EVENT_OPTIONS = [
  "完成注册 (COMPLETE_REGISTRATION)",
  DEFAULT_DELIVERY_EVENT_OPTION,
  "加入购物车 (ADD_TO_CART)",
  "发起结账 (INITIATED_CHECKOUT)",
  "搜索行为 (SEARCH)",
  "添加支付信息 (ADD_PAYMENT_INFO)",
  "首充 (first_recharge)",
] as const;
const DELIVERY_COUNTRY_OPTIONS = [
  { label: "印度", value: "印度", code: "IN" },
  { label: "美国", value: "美国", code: "US" },
  { label: "印尼", value: "印尼", code: "ID" },
  { label: "菲律宾", value: "菲律宾", code: "PH" },
  { label: "泰国", value: "泰国", code: "TH" },
  { label: "越南", value: "越南", code: "VN" },
  { label: "马来西亚", value: "马来西亚", code: "MY" },
  { label: "新加坡", value: "新加坡", code: "SG" },
  { label: "巴西", value: "巴西", code: "BR" },
  { label: "墨西哥", value: "墨西哥", code: "MX" },
] as const;

const navItems: Array<{ key: ViewKey; label: string; icon: typeof BarChart3 }> = [
  { key: "dashboard", label: "工作台", icon: BarChart3 },
  { key: "work-orders", label: "创建工单", icon: ClipboardList },
  { key: "workflow", label: "AI 生产", icon: Check },
  { key: "topics", label: "选题", icon: Sparkles },
  { key: "copy", label: "文案", icon: FileText },
  { key: "creatives", label: "图片", icon: Image },
  { key: "videos", label: "视频", icon: Film },
  { key: "performance", label: "投放分析", icon: BarChart3 },
];

const viewSubtitles: Record<ViewKey, string> = {
  performance: "查看外部系统回传的广告效果分析和 AI 优化建议",
  dashboard: "查看 AI 工单、生产阶段和回传状态",
  "work-orders": "接收投放系统跳转，创建 AI 工单并确认投放参数",
  workflow: "按参数确认、选题、文案、图片、视频和最终预审推进任务",
  topics: "根据投放链接和工单生成选题，并由运营选择一个方向",
  copy: "根据已选题生成广告文案，并完成人工审核",
  creatives: "根据已审核文案生成图片，并完成人工审核",
  videos: "根据已审核图片生成视频，并完成人工审核",
};

const sampleWorkOrder = `工单

项目名称：印度tv8%
投放国家：印度
投放时间：待定
日报时区：+7
投放媒体：fb
投放事件：购物
投放人群：男，年龄25-45

产品名称：印度tv
打款金额：216（广告过审打款）
服务费：8%
商务：西伯
投放链接：https://www.mensparadise.store/TV.html`;

const manualAdPerformanceJsonExample = formatManualAdPerformanceJson({
  source_type: "manual",
  external_user_id: "manual-test",
  date_start: "2026-06-15",
  date_stop: "2026-06-21",
  campaign: {
    name: "new1",
    fb_id: "120247498350000238",
    objective: "OUTCOME_TRAFFIC",
  },
  adset: {
    name: "new1",
    fb_id: "120247498370850238",
    countries: "US",
    optimization_goal: "LINK_CLICKS",
  },
  creative: {
    name: "new12",
    facebook_ad_id: "120247505013060238",
    message: "My record: 3 minutes. Can you beat it?",
    link: "https://example.com/landing",
    image_url: "https://cdn.example.com/new12.jpg",
  },
  insight: {
    impressions: "1079",
    reach: "937",
    clicks: "83",
    inline_link_clicks: "86",
    spend: "0.24",
    actions: [
      { action_type: "link_click", value: "86" },
      { action_type: "landing_page_view", value: "23" },
    ],
  },
});

function App() {
  const [activeView, setActiveView] = useState<ViewKey>(() => initialViewFromUrl());
  const [jobs, setJobs] = useState<AdGenerationJob[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(() => adGenerationJobIdFromUrl());
  const [performanceAnalyses, setPerformanceAnalyses] = useState<AdPerformanceAnalysis[]>([]);
  const [selectedPerformanceAnalysisId, setSelectedPerformanceAnalysisId] = useState<string | null>(null);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [selectedCampaignId, setSelectedCampaignId] = useState<string | null>(null);
  const [topics, setTopics] = useState<Topic[]>([]);
  const [topicGenerationSlots, setTopicGenerationSlots] = useState<TopicGenerationSlot[]>([]);
  const [selectedTopicId, setSelectedTopicId] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<CopyDraft[]>([]);
  const [selectedDraftId, setSelectedDraftId] = useState<string | null>(null);
  const [creatives, setCreatives] = useState<CreativeAsset[]>([]);
  const [creativeGenerationSlots, setCreativeGenerationSlots] = useState<CreativeGenerationSlot[]>([]);
  const [selectedCreativeIds, setSelectedCreativeIds] = useState<string[]>([]);
  const [creativeRewriteFeedbacks, setCreativeRewriteFeedbacks] = useState<Record<string, string>>({});
  const [videos, setVideos] = useState<VideoAsset[]>([]);
  const [selectedVideoId, setSelectedVideoId] = useState<string | null>(null);
  const [videoPollWarnings, setVideoPollWarnings] = useState<Record<string, VideoPollWarning>>({});

  const [rawWorkOrder, setRawWorkOrder] = useState(() => sampleWorkOrder);
  const [deliveryExtractionCache, setDeliveryExtractionCache] =
    useState<DeliveryExtractionCacheEntry | null>(null);
  const [deliveryExtraction, setDeliveryExtraction] = useState<WorkOrderDeliveryExtraction | null>(null);
  const [deliveryConfirmForm, setDeliveryConfirmForm] = useState<ReviewedDeliveryFields>(() =>
    emptyReviewedDeliveryFields(),
  );
  const [deliveryConfirmOpen, setDeliveryConfirmOpen] = useState(false);
  const [deliveryConfirmRawContent, setDeliveryConfirmRawContent] = useState("");

  const [topicFeedback, setTopicFeedback] = useState("");
  const [copyFeedback, setCopyFeedback] = useState("");
  const [videoAspectRatio, setVideoAspectRatio] = useState("9:16");
  const [videoDurationSeconds, setVideoDurationSeconds] = useState(12);
  const [videoInstructions, setVideoInstructions] = useState("");
  const [videoStoryboard, setVideoStoryboard] = useState<Record<string, unknown>[]>([]);
  const [videoStoryboardText, setVideoStoryboardText] = useState("");
  const [videoStoryboardDirty, setVideoStoryboardDirty] = useState(false);
  const [videoStoryboardFeedback, setVideoStoryboardFeedback] = useState("");
  const [videoStoryboardCacheReadyCampaignId, setVideoStoryboardCacheReadyCampaignId] = useState<string | null>(null);

  const [finalPayloadDraft, setFinalPayloadDraft] = useState("");
  const [finalReviewNotes, setFinalReviewNotes] = useState("");
  const [loading, setLoading] = useState<string | null>(null);
  const [operationElapsedSeconds, setOperationElapsedSeconds] = useState(0);
  const [error, setErrorState] = useState<ScopedAppError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const selectedJob = useMemo(
    () => jobs.find((item) => item.id === selectedJobId) ?? (!selectedJobId ? jobs[0] : null) ?? null,
    [jobs, selectedJobId],
  );
  const selectedPerformanceAnalysis = useMemo(
    () =>
      performanceAnalyses.find((item) => item.id === selectedPerformanceAnalysisId) ??
      (!selectedPerformanceAnalysisId ? performanceAnalyses[0] : null) ??
      null,
    [performanceAnalyses, selectedPerformanceAnalysisId],
  );
  const selectedCampaign = useMemo(
    () => campaigns.find((item) => item.id === selectedCampaignId) ?? campaigns[0] ?? null,
    [campaigns, selectedCampaignId],
  );
  const selectedTopic = useMemo(
    () =>
      topics.find((item) => item.id === selectedTopicId) ??
      topics.find((item) => item.status === "selected") ??
      topics[0] ??
      null,
    [selectedTopicId, topics],
  );
  const selectedDraft = useMemo(
    () => drafts.find((item) => item.id === selectedDraftId) ?? drafts[0] ?? null,
    [drafts, selectedDraftId],
  );
  const approvedDraft = useMemo(
    () => drafts.find((item) => item.status === "approved") ?? null,
    [drafts],
  );
  const approvedCreatives = useMemo(
    () => creatives.filter((item) => item.status === "approved"),
    [creatives],
  );
  const approvedVideos = useMemo(
    () => videos.filter((item) => item.status === "approved"),
    [videos],
  );
  const selectableCreativeIds = useMemo(
    () =>
      selectedCreativeIds.filter((id) =>
        creatives.some((item) => item.id === id && item.status === "approved"),
      ),
    [creatives, selectedCreativeIds],
  );

  useEffect(() => {
    void refreshBaseData();
    const reviewJobId = adGenerationJobIdFromUrl();
    if (reviewJobId) {
      void refreshJob(reviewJobId);
    }
  }, []);

  useEffect(() => {
    setDeliveryExtractionCache(loadDeliveryExtractionCache(rawWorkOrder));
  }, [rawWorkOrder]);

  useEffect(() => {
    if (!selectedJob) return;
    const campaignId = adGenerationCampaignId(selectedJob);
    if (campaignId && campaignId !== selectedCampaignId) {
      setSelectedCampaignId(campaignId);
      void refreshCampaignData(campaignId);
    }
  }, [selectedJob?.id, selectedJob?.updated_at, selectedCampaignId]);

  useEffect(() => {
    if (selectedCampaign?.id) {
      void refreshCampaignData(selectedCampaign.id);
    }
  }, [selectedCampaign?.id]);

  useEffect(() => {
    const campaignId = selectedCampaign?.id;
    if (!campaignId) {
      const latest = activeView === "videos" ? loadLatestVideoStoryboardDraftCache() : null;
      if (latest && !videoStoryboardText.trim()) {
        setVideoAspectRatio(latest.aspectRatio || "9:16");
        setVideoDurationSeconds(latest.durationSeconds || 12);
        setVideoInstructions(latest.instructions || "");
        setSelectedCreativeIds(latest.selectedCreativeIds || []);
        setVideoStoryboard(latest.storyboard || []);
        setVideoStoryboardText(latest.storyboardText || "");
        setVideoStoryboardDirty(latest.storyboardDirty);
        setVideoStoryboardFeedback(latest.storyboardFeedback || "");
      }
      setVideoStoryboardCacheReadyCampaignId(null);
      return;
    }
    const cached = loadVideoStoryboardDraftCache(campaignId);
    if (cached) {
      setVideoAspectRatio(cached.aspectRatio || "9:16");
      setVideoDurationSeconds(cached.durationSeconds || 12);
      setVideoInstructions(cached.instructions || "");
      setSelectedCreativeIds(cached.selectedCreativeIds || []);
      setVideoStoryboard(cached.storyboard || []);
      setVideoStoryboardText(cached.storyboardText || "");
      setVideoStoryboardDirty(cached.storyboardDirty);
      setVideoStoryboardFeedback(cached.storyboardFeedback || "");
    } else {
      setVideoAspectRatio("9:16");
      setVideoDurationSeconds(12);
      setVideoInstructions("");
      setVideoStoryboard([]);
      setVideoStoryboardText("");
      setVideoStoryboardDirty(false);
      setVideoStoryboardFeedback("");
    }
    setVideoStoryboardCacheReadyCampaignId(campaignId);
  }, [activeView, selectedCampaign?.id]);

  useEffect(() => {
    const campaignId = selectedCampaign?.id;
    if (!campaignId || videoStoryboardCacheReadyCampaignId !== campaignId) return;
    saveVideoStoryboardDraftCache(campaignId, {
      campaignId,
      aspectRatio: videoAspectRatio,
      durationSeconds: videoDurationSeconds,
      instructions: videoInstructions,
      selectedCreativeIds,
      storyboard: videoStoryboard,
      storyboardText: videoStoryboardText,
      storyboardDirty: videoStoryboardDirty,
      storyboardFeedback: videoStoryboardFeedback,
      savedAt: new Date().toISOString(),
    });
  }, [
    selectedCampaign?.id,
    videoStoryboardCacheReadyCampaignId,
    videoAspectRatio,
    videoDurationSeconds,
    videoInstructions,
    selectedCreativeIds,
    videoStoryboard,
    videoStoryboardText,
    videoStoryboardDirty,
    videoStoryboardFeedback,
  ]);

  useEffect(() => {
    if (
      activeView !== "workflow" ||
      !selectedJobId ||
      !["queued", "processing"].includes(selectedJob?.status ?? "")
    ) {
      return;
    }
    const timer = window.setInterval(() => {
      void refreshJob(selectedJobId);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [activeView, selectedJobId, selectedJob?.status]);

  useEffect(() => {
    if (!loading) {
      setOperationElapsedSeconds(0);
      return;
    }
    const startedAt = Date.now();
    setOperationElapsedSeconds(0);
    const timer = window.setInterval(() => {
      setOperationElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [loading]);

  useEffect(() => {
    const generatingIds = videos
      .filter((video) => isVideoGeneratingStatus(video.status) && video.provider_job_id)
      .map((video) => video.id);
    if (!generatingIds.length) return;

    let cancelled = false;
    let inFlight = false;
    const refreshGeneratingVideos = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const refreshed = await Promise.all(
          generatingIds.map(async (videoId): Promise<{
            videoId: string;
            video: VideoAsset | null;
            caught: unknown | null;
          }> => {
            try {
              return { videoId, video: await api.refreshVideoGeneration(videoId), caught: null };
            } catch (caught) {
              return { videoId, video: null, caught };
            }
          }),
        );
        if (cancelled) return;
        const refreshedVideos = refreshed
          .map((result) => result.video)
          .filter((video): video is VideoAsset => Boolean(video));
        if (refreshedVideos.length) {
          clearError("video");
          setVideos((current) =>
            current.map((item) => refreshedVideos.find((video) => video.id === item.id) ?? item),
          );
        }
        const authError = refreshed.find((result) => result.caught && isAuthApiError(result.caught));
        if (authError?.caught) {
          setCaughtError("video", authError.caught, "视频状态刷新失败");
        }
        setVideoPollWarnings((current) => {
          let next = current;
          const copy = () => {
            if (next === current) next = { ...current };
            return next;
          };
          for (const result of refreshed) {
            if (result.video) {
              if (next[result.videoId]) {
                delete copy()[result.videoId];
              }
              continue;
            }
            if (!result.caught || isAuthApiError(result.caught)) continue;
            copy()[result.videoId] = {
              message: apiErrorMessage(result.caught, "视频状态刷新暂时失败，系统会继续自动刷新。"),
              updatedAt: Date.now(),
            };
          }
          return next;
        });
      } finally {
        inFlight = false;
      }
    };

    void refreshGeneratingVideos();
    const timer = window.setInterval(refreshGeneratingVideos, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [videos.map((video) => `${video.id}:${video.status}:${video.provider_job_id ?? ""}`).join("|")]);

  function setError(message: string | null, scope: ErrorScope = "global", transient = false) {
    if (!message) {
      clearError();
      return;
    }
    setErrorState({ scope, message, transient, createdAt: Date.now() });
  }

  function clearError(scope?: ErrorScope | ErrorScope[]) {
    if (!scope) {
      setErrorState(null);
      return;
    }
    const scopes = Array.isArray(scope) ? scope : [scope];
    setErrorState((current) => (current && scopes.includes(current.scope) ? null : current));
  }

  function setCaughtError(scope: ErrorScope, caught: unknown, fallback: string): string {
    const effectiveScope =
      caught instanceof ApiError && [401, 403].includes(caught.status) ? "global" : scope;
    const message = apiErrorMessage(caught, fallback);
    const transient = isTransientApiError(caught);
    if (effectiveScope === "refresh" && transient) return message;
    setErrorState({ scope: effectiveScope, message, transient, createdAt: Date.now() });
    return message;
  }

  function clearResolvedTransientError(
    next: WorkflowArtifactSnapshot,
    previous: WorkflowArtifactSnapshot = { topics, drafts, creatives, videos },
  ) {
    setErrorState((current) => {
      if (!current?.transient) return current;
      if (
        current.scope === "topic" &&
        hasNewOrFreshArtifact(next.topics, previous.topics, current.createdAt)
      ) {
        return null;
      }
      if (
        current.scope === "copy" &&
        hasNewOrFreshArtifact(next.drafts, previous.drafts, current.createdAt)
      ) {
        return null;
      }
      if (
        current.scope === "image" &&
        hasNewOrFreshArtifact(next.creatives, previous.creatives, current.createdAt)
      ) {
        return null;
      }
      const resolvedVideos = next.videos.filter(
        (video) => Boolean(video.url) || ["generated", "approved"].includes(video.status),
      );
      const previousResolvedVideos = previous.videos.filter(
        (video) => Boolean(video.url) || ["generated", "approved"].includes(video.status),
      );
      if (
        current.scope === "video" &&
        hasNewOrFreshArtifact(resolvedVideos, previousResolvedVideos, current.createdAt)
      ) {
        return null;
      }
      return current;
    });
  }

  function setVideoPollWarning(videoId: string, message: string) {
    setVideoPollWarnings((current) => ({
      ...current,
      [videoId]: {
        message,
        updatedAt: Date.now(),
      },
    }));
  }

  function clearVideoPollWarning(videoId: string) {
    setVideoPollWarnings((current) => {
      if (!current[videoId]) return current;
      const next = { ...current };
      delete next[videoId];
      return next;
    });
  }

  async function run<T>(key: string, task: () => Promise<T>, success?: string): Promise<T | null> {
    const scope = errorScopeForOperationKey(key);
    setLoading(key);
    clearError(scope);
    setNotice(null);
    try {
      const result = await task();
      clearError(scope);
      if (success) setNotice(success);
      return result;
    } catch (caught) {
      setCaughtError(scope, caught, "操作失败");
      return null;
    } finally {
      setLoading(null);
    }
  }

  function clearCampaignWorkflowState() {
    setTopics([]);
    setTopicGenerationSlots([]);
    setSelectedTopicId(null);
    setDrafts([]);
    setSelectedDraftId(null);
    setCreatives([]);
    setCreativeGenerationSlots([]);
    setSelectedCreativeIds([]);
    setCreativeRewriteFeedbacks({});
    setVideos([]);
    setVideoPollWarnings({});
    setSelectedVideoId(null);
    setVideoStoryboard([]);
    setVideoStoryboardText("");
    setVideoStoryboardDirty(false);
    setVideoStoryboardFeedback("");
    setVideoStoryboardCacheReadyCampaignId(null);
    setFinalPayloadDraft("");
    setFinalReviewNotes("");
  }

  async function refreshBaseData() {
    await run("refresh", async () => {
      const [nextJobs, nextCampaigns, nextPerformanceAnalyses] = await Promise.all([
        api.listAdGenerationJobs(100),
        api.listCampaigns(100),
        api.listAdPerformanceAnalyses(100),
      ]);
      setJobs(nextJobs);
      setCampaigns(nextCampaigns);
      setPerformanceAnalyses(nextPerformanceAnalyses);
      setSelectedJobId((current) =>
        current && nextJobs.some((item) => item.id === current) ? current : nextJobs[0]?.id ?? null,
      );
      setSelectedPerformanceAnalysisId((current) =>
        current && nextPerformanceAnalyses.some((item) => item.id === current)
          ? current
          : nextPerformanceAnalyses[0]?.id ?? null,
      );
      setSelectedCampaignId((current) =>
        current && nextCampaigns.some((item) => item.id === current)
          ? current
          : nextCampaigns[0]?.id ?? null,
      );
      if (!selectedCampaignId || !nextCampaigns.some((item) => item.id === selectedCampaignId)) {
        clearCampaignWorkflowState();
      }
    });
  }

  async function refreshJob(jobId: string) {
    const job = await run("ad-generation-refresh", () => api.getAdGenerationJob(jobId));
    if (!job) return;
    setJobs((current) => upsertById(current, job));
    setSelectedJobId(job.id);
    if (job.status === "failed") {
      setError(adGenerationJobFailureMessage(job), "work-order");
      setNotice(null);
    } else {
      clearError("work-order");
    }
    const campaignId = adGenerationCampaignId(job);
    if (campaignId) {
      try {
        const campaign = await api.getCampaign(campaignId);
        setCampaigns((current) => upsertById(current, campaign));
        setSelectedCampaignId(campaign.id);
        await refreshCampaignData(campaign.id);
      } catch {
        // The background task may still be linking the campaign.
      }
    }
  }

  async function refreshPerformanceAnalyses() {
    const analyses = await run("performance-refresh", () => api.listAdPerformanceAnalyses(100));
    if (!analyses) return;
    setPerformanceAnalyses(analyses);
    setSelectedPerformanceAnalysisId((current) =>
      current && analyses.some((item) => item.id === current) ? current : analyses[0]?.id ?? null,
    );
  }

  async function handleCreatePerformanceAnalysis(
    payload: ManualAdPerformancePayload,
  ): Promise<AdPerformanceAnalysis | null> {
    const analysis = await run(
      "performance-create",
      () => api.createAdPerformanceAnalysis(payload),
      "投放分析记录已创建",
    );
    if (!analysis) return null;

    setPerformanceAnalyses((current) => upsertById(current, analysis));
    setSelectedPerformanceAnalysisId(analysis.id);
    return analysis;
  }

  async function handleDeletePerformanceAnalysis(analysisId: string) {
    const analysis = performanceAnalyses.find((item) => item.id === analysisId);
    const title = analysis ? performanceAnalysisTitle(analysis) : shortId(analysisId);
    const confirmed = window.confirm(
      `确认删除投放分析记录「${title}」吗？删除后该条效果数据和分析结果会从本系统移除。`,
    );
    if (!confirmed) return;

    const deleted = await run(
      `delete-performance-${analysisId}`,
      () => api.deleteAdPerformanceAnalysis(analysisId),
      "投放分析记录已删除",
    );
    if (deleted === null) return;

    setPerformanceAnalyses((current) => {
      const nextAnalyses = current.filter((item) => item.id !== analysisId);
      if (selectedPerformanceAnalysisId === analysisId) {
        setSelectedPerformanceAnalysisId(nextAnalyses[0]?.id ?? null);
      }
      return nextAnalyses;
    });
  }

  async function refreshCampaignData(campaignId: string, options: { silent?: boolean } = {}) {
    const refresh = async () => {
      const [nextTopics, nextDrafts, nextCreatives, nextVideos] = await Promise.all([
        api.listTopics(campaignId),
        api.listDrafts(campaignId),
        api.listCreatives(campaignId),
        api.listVideos(campaignId),
      ]);
      setTopics(nextTopics);
      setDrafts(nextDrafts);
      setCreatives(nextCreatives);
      setCreativeGenerationSlots((current) => syncCreativeSlotsWithAssets(current, nextCreatives));
      setVideos(nextVideos);
      setVideoPollWarnings({});
      setSelectedTopicId((current) =>
        current && nextTopics.some((item) => item.id === current)
          ? current
          : nextTopics.find((item) => item.status === "selected")?.id ?? nextTopics[0]?.id ?? null,
      );
      setSelectedDraftId((current) =>
        current && nextDrafts.some((item) => item.id === current)
          ? current
          : nextDrafts[0]?.id ?? null,
      );
      setSelectedCreativeIds((current) => {
        const valid = current.filter((id) => nextCreatives.some((item) => item.id === id));
        if (valid.length) return valid;
        return nextCreatives.filter((item) => item.status === "approved").slice(0, 2).map((item) => item.id);
      });
      setSelectedVideoId((current) =>
        current && nextVideos.some((item) => item.id === current)
          ? current
          : nextVideos.find((item) => item.status === "approved")?.id ?? nextVideos[0]?.id ?? null,
      );
      clearResolvedTransientError({
        topics: nextTopics,
        drafts: nextDrafts,
        creatives: nextCreatives,
        videos: nextVideos,
      });
    };

    if (options.silent) {
      try {
        await refresh();
        clearError("refresh");
      } catch (caught) {
        setCaughtError("refresh", caught, "Campaign data refresh failed.");
      }
      return;
    }

    await run("campaign-refresh", refresh);
  }

  async function handleCreateWorkOrder() {
    const content = rawWorkOrder.trim();
    if (!content) {
      setError("请先粘贴工单内容。", "work-order");
      return;
    }
    if (deliveryExtraction && deliveryConfirmRawContent === content) {
      openDeliveryConfirmation(deliveryExtraction, content);
      setNotice("已使用上次识别结果，请确认投放参数");
      return;
    }

    const cached = loadDeliveryExtractionCache(content);
    if (cached) {
      setDeliveryExtractionCache(cached);
      openDeliveryConfirmation(cached.extraction, content);
      setNotice("已使用上次识别结果，请确认投放参数");
      return;
    }

    const extracted = await run(
      "extract-work-order",
      () => api.extractWorkOrderDeliveryFields(content),
      "请确认投放参数",
    );
    if (extracted) {
      setDeliveryExtractionCache(saveDeliveryExtractionCache(content, extracted));
      openDeliveryConfirmation(extracted, content);
    }
  }

  function openDeliveryConfirmation(extraction: WorkOrderDeliveryExtraction, rawContent: string) {
    setDeliveryExtraction(extraction);
    setDeliveryConfirmForm(buildDeliveryConfirmForm(extraction));
    setDeliveryConfirmRawContent(rawContent);
    setDeliveryConfirmOpen(true);
  }

  function handleCancelDeliveryConfirm() {
    setDeliveryConfirmOpen(false);
    setNotice("识别结果已保留，再次点击可直接确认");
  }

  async function handleConfirmCreateWorkOrder() {
    if (!deliveryExtraction) return;
    const validationError = validateDeliveryConfirmForm(deliveryConfirmForm);
    if (validationError) {
      setError(validationError, "work-order");
      return;
    }
    const reviewedFields = normalizeReviewedDeliveryFields(deliveryConfirmForm);
    const integrationParams = adGenerationIntegrationParamsFromUrl();
    const accepted = await run(
      "create-ad-generation",
      () =>
        api.createAdGenerationJob({
          rawContent: deliveryConfirmRawContent || rawWorkOrder.trim(),
          structuredFields: { ...reviewedFields },
          deliveryExtraction,
          externalOrderId: integrationParams.externalOrderId,
          returnUrl: integrationParams.returnUrl,
          callbackUrl: integrationParams.callbackUrl,
        }),
      "AI 工单已创建，正在进入 AI 生产",
    );
    if (!accepted) return;
    setSelectedJobId(accepted.job_id);
    setActiveView("workflow");
    setDeliveryConfirmOpen(false);
    setDeliveryExtraction(null);
    setDeliveryConfirmRawContent("");
    setDeliveryExtractionCache(null);
    removeDeliveryExtractionCache(deliveryConfirmRawContent || rawWorkOrder.trim());
    window.history.replaceState(null, "", `/review/ad-generation/${accepted.job_id}`);
    window.setTimeout(() => void refreshJob(accepted.job_id), 900);
  }

  async function handleDeleteJob(jobId: string) {
    const job = jobs.find((item) => item.id === jobId);
    const title = job ? adGenerationJobTitle(job) : shortId(jobId);
    const confirmed = window.confirm(
      `确认删除 AI 工单「${title}」吗？删除后将同时清理该工单生成的活动、文案、图片、视频和生产记录。`,
    );
    if (!confirmed) return;

    const deleted = await run(
      `delete-job-${jobId}`,
      () => api.deleteAdGenerationJob(jobId),
      "AI 工单已删除",
    );
    if (deleted === null) return;

    setJobs((current) => {
      const nextJobs = current.filter((item) => item.id !== jobId);
      if (selectedJobId === jobId) {
        setSelectedJobId(nextJobs[0]?.id ?? null);
      }
      return nextJobs;
    });
    await refreshBaseData();
    setNotice("AI 工单及关联生产数据已删除");
  }

  function buildTopicGenerationSignals(
    feedback?: string,
    mode: "revise_from_operator_feedback" | "retry_failed_topic_slot" = "revise_from_operator_feedback",
  ): Record<string, unknown> {
    const revisionFeedback = feedback?.trim();
    const signals: Record<string, unknown> = {
      integration: "publishing_jump_workflow",
    };
    if (revisionFeedback) {
      signals.topic_revision_feedback = revisionFeedback;
      signals.topic_generation_mode = mode;
    } else if (mode === "retry_failed_topic_slot") {
      signals.topic_generation_mode = mode;
    }
    if (revisionFeedback || mode === "retry_failed_topic_slot") {
      signals.previous_topics = topics.map((topic) => ({
        title: topic.title,
        angle: topic.angle,
        status: topic.status,
        risk_notes: topic.risk_notes,
      }));
    }
    return signals;
  }

  function updateTopicGenerationSlot(index: number, update: Partial<TopicGenerationSlot>) {
    setTopicGenerationSlots((current) => {
      const slots = current.length ? current : initialTopicSlots(TOPIC_GENERATION_LIMIT);
      return slots.map((slot) => (slot.index === index ? { ...slot, ...update } : slot));
    });
  }

  function markLoadingTopicSlotsFailed(message: string, targetIndex?: number) {
    setTopicGenerationSlots((current) =>
      current.map((slot) =>
        slot.status === "loading" && (targetIndex === undefined || slot.index === targetIndex)
          ? { ...slot, status: "error", message }
          : slot,
      ),
    );
  }

  async function handleGenerateTopics(feedback?: string) {
    if (!selectedCampaign) return;
    if (topicGenerationSlots.some((slot) => slot.status === "loading")) return;
    setActiveView("topics");
    setLoading("topics");
    clearError("topic");
    setNotice(null);
    setTopics([]);
    setSelectedTopicId(null);
    setTopicGenerationSlots(initialTopicSlots(TOPIC_GENERATION_LIMIT));

    const revisionFeedback = feedback?.trim();
    const signals = buildTopicGenerationSignals(revisionFeedback);
    const streamedTopics: Topic[] = [];

    try {
      await api.generateTopicsStream(
        selectedCampaign.id,
        TOPIC_GENERATION_LIMIT,
        signals,
        (event: TopicStreamEvent) => {
          if (event.type === "start") {
            setTopicGenerationSlots(initialTopicSlots(event.limit));
            return;
          }
          if (event.type === "slot") {
            updateTopicGenerationSlot(event.index, {
              status: "loading",
              topic: undefined,
              message: undefined,
            });
            return;
          }
          if (event.type === "topic") {
            streamedTopics.push(event.topic);
            clearError("topic");
            setTopics((current) => appendOrReplaceById(current, event.topic));
            setSelectedTopicId((current) => current ?? event.topic.id);
            updateTopicGenerationSlot(event.index, {
              status: "done",
              topic: event.topic,
              message: undefined,
            });
            return;
          }
          if (event.type === "error") {
            markLoadingTopicSlotsFailed(apiErrorMessage(event.message, "此候选生成失败，请重试。"), event.index);
            return;
          }
          if (event.type === "done") {
            setTopicGenerationSlots((current) => {
              const next = current.map((slot) =>
                slot.status === "loading"
                  ? { ...slot, status: "error" as const, message: "模型未返回此候选，请重试此候选。" }
                  : slot,
              );
              return next.every((slot) => slot.status === "done") ? [] : next;
            });
          }
        },
      );

      if (!streamedTopics.length) {
        setError("选题生成失败，请稍后重试。", "topic");
        markLoadingTopicSlotsFailed("模型未返回候选，请重新生成。");
        return;
      }

      if (revisionFeedback) setTopicFeedback("");
      clearError("topic");
      setNotice(
        streamedTopics.length === TOPIC_GENERATION_LIMIT
          ? revisionFeedback
            ? "已按修改意见重新生成选题"
            : "选题已生成"
          : `已生成 ${streamedTopics.length} 个选题，剩余候选可单独重试`,
      );
      void saveWorkflowStage("topic_review");
    } catch (caught) {
      const message = apiErrorMessage(caught, "选题生成失败");
      if (streamedTopics.length) {
        clearError("topic");
      } else {
        setCaughtError("topic", caught, "选题生成失败");
      }
      markLoadingTopicSlotsFailed(message || "选题生成中断，请重试。");
      if (streamedTopics.length) {
        setNotice(`已生成 ${streamedTopics.length} 个选题，剩余候选可单独重试`);
        void saveWorkflowStage("topic_review");
      }
    } finally {
      setLoading((current) => (current === "topics" ? null : current));
    }
  }

  async function handleRetryTopicSlot(slotIndex: number) {
    if (!selectedCampaign) return;
    if (loading?.startsWith("topic-retry-")) return;
    const revisionFeedback = topicFeedback.trim();
    setLoading(`topic-retry-${slotIndex}`);
    clearError("topic");
    setNotice(null);
    updateTopicGenerationSlot(slotIndex, {
      status: "loading",
      topic: undefined,
      message: undefined,
    });

    let retriedTopic: Topic | null = null;
    try {
      await api.generateTopicsStream(
        selectedCampaign.id,
        1,
        buildTopicGenerationSignals(revisionFeedback, "retry_failed_topic_slot"),
        (event: TopicStreamEvent) => {
          if (event.type === "topic") {
            retriedTopic = event.topic;
            setTopics((current) => appendOrReplaceById(current, event.topic));
            setSelectedTopicId((current) => current ?? event.topic.id);
            updateTopicGenerationSlot(slotIndex, {
              status: "done",
              topic: event.topic,
              message: undefined,
            });
            return;
          }
          if (event.type === "error") {
            updateTopicGenerationSlot(slotIndex, {
              status: "error",
              message: apiErrorMessage(event.message, "此候选生成失败，请重试。"),
            });
          }
        },
      );

      if (!retriedTopic) {
        updateTopicGenerationSlot(slotIndex, {
          status: "error",
          message: "模型未返回此候选，请再试一次。",
        });
        return;
      }

      setNotice(`候选 ${slotIndex} 已重新生成`);
      clearError("topic");
      setTopicGenerationSlots((current) =>
        current.length && current.every((slot) => slot.status === "done") ? [] : current,
      );
    } catch (caught) {
      const message = apiErrorMessage(caught, "此候选生成失败");
      if (isAuthApiError(caught)) {
        setCaughtError("topic", caught, "此候选生成失败");
      }
      updateTopicGenerationSlot(slotIndex, {
        status: "error",
        message,
      });
    } finally {
      setLoading((current) => (current === `topic-retry-${slotIndex}` ? null : current));
    }
  }

  async function handleSelectTopic(topicId: string) {
    const topic = await run("select-topic", () => api.selectTopic(topicId), "选题已选择");
    if (topic) {
      setTopics((current) =>
        current.map((item) =>
          item.id === topic.id
            ? topic
            : item.campaign_id === topic.campaign_id && item.status === "selected"
              ? { ...item, status: "proposed" }
              : item,
        ),
      );
      setSelectedTopicId(topic.id);
      setActiveView("copy");
      void saveWorkflowStage("copy_review");
    }
  }

  async function handleGenerateCopy() {
    const topic = topics.find((item) => item.status === "selected") ?? selectedTopic;
    if (!topic) {
      setError("请先选择一个选题。", "copy");
      return;
    }
    const draft = await run("copy", () => api.generateCopy(topic.id, "Learn More"), "文案已生成");
    if (draft) {
      setDrafts((current) => [draft, ...current]);
      setSelectedDraftId(draft.id);
      setActiveView("copy");
      void saveWorkflowStage("copy_review");
    }
  }

  async function handleReviseCopy() {
    if (!selectedDraft || !copyFeedback.trim()) return;
    const draft = await run(
      "revise-copy",
      () => api.reviseCopy(selectedDraft.id, copyFeedback),
      "新版本文案已生成",
    );
    if (draft) {
      setDrafts((current) => [draft, ...current]);
      setSelectedDraftId(draft.id);
      setCopyFeedback("");
    }
  }

  async function handleReview(entityType: ReviewEntityType, entityId: string, decision: ReviewDecision) {
    if (!selectedCampaign) return;
    const rollbackReview = applyOptimisticReview(entityType, entityId, decision);
    const review = await run(
      `review-${entityType}-${decision}`,
      () => api.submitReview(entityType, entityId, decision, selectedCampaign.id, copyFeedback || undefined),
      "审核结果已提交",
    );
    if (!review) {
      rollbackReview();
      return;
    }
    if (review) {
      void refreshCampaignData(selectedCampaign.id, { silent: true });
      if (entityType === "copy_draft" && decision === "approved") void saveWorkflowStage("image_review");
      if (entityType === "creative_asset" && decision === "approved") {
        void saveWorkflowStage(adGenerationRequiresVideo(selectedJob) ? "video_review" : "final_review");
      }
      if (entityType === "video_asset" && decision === "approved") void saveWorkflowStage("final_review");
    }
  }

  function applyOptimisticReview(
    entityType: ReviewEntityType,
    entityId: string,
    decision: ReviewDecision,
  ): () => void {
    const nextStatus = reviewStatusFromDecision(entityType, decision);

    if (entityType === "topic") {
      const previous = topics.find((item) => item.id === entityId);
      setTopics((current) =>
        current.map((item) => (item.id === entityId ? { ...item, status: nextStatus } : item)),
      );
      if (decision === "approved") setSelectedTopicId(entityId);
      return () => {
        if (!previous) return;
        setTopics((current) =>
          current.map((item) => (item.id === entityId ? { ...item, status: previous.status } : item)),
        );
      };
    }

    if (entityType === "copy_draft") {
      const previous = drafts.find((item) => item.id === entityId);
      setDrafts((current) =>
        current.map((item) => (item.id === entityId ? { ...item, status: nextStatus } : item)),
      );
      if (decision === "approved") setSelectedDraftId(entityId);
      return () => {
        if (!previous) return;
        setDrafts((current) =>
          current.map((item) => (item.id === entityId ? { ...item, status: previous.status } : item)),
        );
      };
    }

    if (entityType === "creative_asset") {
      const previous = creatives.find((item) => item.id === entityId);
      setCreatives((current) =>
        current.map((item) => (item.id === entityId ? { ...item, status: nextStatus } : item)),
      );
      setCreativeGenerationSlots((current) =>
        current.map((slot) =>
          slot.asset?.id === entityId
            ? { ...slot, asset: { ...slot.asset, status: nextStatus } }
            : slot,
        ),
      );
      return () => {
        if (!previous) return;
        setCreatives((current) =>
          current.map((item) => (item.id === entityId ? { ...item, status: previous.status } : item)),
        );
        setCreativeGenerationSlots((current) =>
          current.map((slot) =>
            slot.asset?.id === entityId
              ? { ...slot, asset: { ...slot.asset, status: previous.status } }
              : slot,
          ),
        );
      };
    }

    const previous = videos.find((item) => item.id === entityId);
    setVideos((current) =>
      current.map((item) => (item.id === entityId ? { ...item, status: nextStatus } : item)),
    );
    if (decision === "approved") setSelectedVideoId(entityId);
    return () => {
      if (!previous) return;
      setVideos((current) =>
        current.map((item) => (item.id === entityId ? { ...item, status: previous.status } : item)),
      );
    };
  }

  function updateCreativeGenerationSlot(index: number, update: Partial<CreativeGenerationSlot>) {
    setCreativeGenerationSlots((current) => {
      const fallbackSlots = buildCreativeSlots(creatives, []);
      const slots = current.length
        ? current
        : fallbackSlots.length
          ? fallbackSlots
          : initialCreativeSlots(CREATIVE_GENERATION_LIMIT);
      return slots.map((slot) => (slot.index === index ? { ...slot, ...update } : slot));
    });
  }

  function markLoadingCreativeSlotsFailed(message: string, targetIndex?: number) {
    setCreativeGenerationSlots((current) =>
      current.map((slot) =>
        slot.status === "loading" && (targetIndex === undefined || slot.index === targetIndex)
          ? { ...slot, status: "error", message }
          : slot,
      ),
    );
  }

  async function handleGenerateCreatives() {
    const draft = approvedDraft ?? selectedDraft;
    if (!draft) {
      setError("请先生成并审核通过一条文案。", "image");
      return;
    }
    if (creativeGenerationSlots.some((slot) => slot.status === "loading")) return;
    setActiveView("creatives");
    setLoading("creatives");
    clearError("image");
    setNotice(null);
    setCreativeGenerationSlots(initialCreativeSlots(CREATIVE_GENERATION_LIMIT));

    const streamedAssets: CreativeAsset[] = [];
    try {
      await api.generateCreativesStream(
        draft.id,
        CREATIVE_GENERATION_LIMIT,
        "1:1",
        (event: CreativeStreamEvent) => {
          if (event.type === "start") {
            setCreativeGenerationSlots(initialCreativeSlots(CREATIVE_GENERATION_LIMIT));
            return;
          }
          if (event.type === "slot") {
            updateCreativeGenerationSlot(event.index, {
              status: "loading",
              asset: undefined,
              message: undefined,
            });
            return;
          }
          if (event.type === "asset") {
            streamedAssets.push(event.asset);
            clearError("image");
            setCreatives((current) => prependOrReplaceById(current, event.asset));
            setSelectedCreativeIds((current) =>
              current.includes(event.asset.id) ? current : [...current, event.asset.id],
            );
            updateCreativeGenerationSlot(event.index, {
              status: "done",
              asset: event.asset,
              message: undefined,
            });
            return;
          }
          if (event.type === "error") {
            markLoadingCreativeSlotsFailed(apiErrorMessage(event.message, "此图片生成失败，请重试。"), event.index);
            return;
          }
          if (event.type === "done") {
            setCreativeGenerationSlots((current) =>
              current.map((slot) =>
                slot.status === "loading"
                  ? { ...slot, status: "error" as const, message: "此图片未生成完成，请重试此候选。" }
                  : slot,
              ),
            );
          }
        },
      );

      if (!streamedAssets.length) {
        setError("图片生成失败，请稍后重试。", "image");
        markLoadingCreativeSlotsFailed("图片生成失败，请重新生成。");
        return;
      }

      clearError("image");
      setNotice(
        streamedAssets.length === CREATIVE_GENERATION_LIMIT
          ? "3 张图片已生成"
          : `已生成 ${streamedAssets.length} 张图片，剩余候选可单独重试`,
      );
      void saveWorkflowStage("image_review");
    } catch (caught) {
      const message = apiErrorMessage(caught, "图片生成失败");
      if (streamedAssets.length) {
        clearError("image");
      } else {
        setCaughtError("image", caught, "图片生成失败");
      }
      markLoadingCreativeSlotsFailed(message || "图片生成中断，请重试。");
      if (streamedAssets.length) {
        setNotice(`已生成 ${streamedAssets.length} 张图片，剩余候选可单独重试`);
        void saveWorkflowStage("image_review");
      }
    } finally {
      setLoading((current) => (current === "creatives" ? null : current));
    }
  }

  async function handleRetryCreativeSlot(slotIndex: number) {
    const draft = approvedDraft ?? selectedDraft;
    if (!draft) {
      setError("请先生成并审核通过一条文案。", "image");
      return;
    }
    if (loading?.startsWith("creative-retry-")) return;
    setLoading(`creative-retry-${slotIndex}`);
    clearError("image");
    setNotice(null);
    updateCreativeGenerationSlot(slotIndex, {
      status: "loading",
      asset: undefined,
      message: undefined,
    });

    let retriedAsset: CreativeAsset | null = null;
    try {
      await api.generateCreativesStream(
        draft.id,
        1,
        "1:1",
        (event: CreativeStreamEvent) => {
          if (event.type === "asset") {
            retriedAsset = event.asset;
            setCreatives((current) => prependOrReplaceById(current, event.asset));
            setSelectedCreativeIds((current) =>
              current.includes(event.asset.id) ? current : [...current, event.asset.id],
            );
            updateCreativeGenerationSlot(slotIndex, {
              status: "done",
              asset: event.asset,
              message: undefined,
            });
            return;
          }
          if (event.type === "error") {
            updateCreativeGenerationSlot(slotIndex, {
              status: "error",
              message: apiErrorMessage(event.message, "此图片生成失败，请重试。"),
            });
          }
        },
        slotIndex,
      );

      if (!retriedAsset) {
        updateCreativeGenerationSlot(slotIndex, {
          status: "error",
          message: "模型未返回此图片，请再试一次。",
        });
        return;
      }
      setNotice(`图片 ${slotIndex} 已重新生成`);
      clearError("image");
      void saveWorkflowStage("image_review");
    } catch (caught) {
      const message = apiErrorMessage(caught, "此图片生成失败");
      if (isAuthApiError(caught)) {
        setCaughtError("image", caught, "此图片生成失败");
      }
      updateCreativeGenerationSlot(slotIndex, {
        status: "error",
        message,
      });
    } finally {
      setLoading((current) => (current === `creative-retry-${slotIndex}` ? null : current));
    }
  }

  async function handleRegenerateCreative(asset: CreativeAsset) {
    const feedback = creativeRewriteFeedbacks[asset.id]?.trim();
    if (!feedback) {
      setError("请先填写这张图片的改写要求。", "image");
      return;
    }
    const slotIndex = creativeImageIndex(asset, 1);
    setLoading(`creative-regenerate-${asset.id}`);
    clearError("image");
    setNotice(null);
    updateCreativeGenerationSlot(slotIndex, {
      status: "loading",
      asset,
      message: undefined,
    });

    try {
      const regenerated = await api.regenerateCreative(asset.id, feedback, asset.size);
      setCreatives((current) => prependOrReplaceById(current, regenerated));
      setSelectedCreativeIds((current) =>
        current.includes(regenerated.id)
          ? current.filter((id) => id !== asset.id)
          : [...current.filter((id) => id !== asset.id), regenerated.id],
      );
      updateCreativeGenerationSlot(slotIndex, {
        status: "done",
        asset: regenerated,
        message: undefined,
      });
      setCreativeRewriteFeedbacks((current) => {
        const next = { ...current };
        delete next[asset.id];
        return next;
      });
      setNotice(`图片 ${slotIndex} 已生成新版本`);
      clearError("image");
      void saveWorkflowStage("image_review");
    } catch (caught) {
      const message = apiErrorMessage(caught, "图片改写失败");
      if (caught instanceof ApiError && [401, 403].includes(caught.status)) {
        setCaughtError("image", caught, "图片改写失败");
      }
      updateCreativeGenerationSlot(slotIndex, {
        status: "error",
        asset,
        message,
      });
    }
    setLoading((current) => (current === `creative-regenerate-${asset.id}` ? null : current));
  }

  async function handleGenerateVideoStoryboard() {
    if (!selectedCampaign) return;
    const sourceIds = selectedCreativeIdsForVideo();
    if (!sourceIds.length) {
      setError("请先审核通过并选择至少一张图片。", "video");
      return;
    }
    if (sourceIds.length > VIDEO_MAX_REFERENCE_IMAGES) {
      setError(`视频生成最多支持 ${VIDEO_MAX_REFERENCE_IMAGES} 张参考图片，请减少选择。`, "video");
      return;
    }
    const previousText = videoStoryboardText;
    let streamedText = "";
    setLoading("video-storyboard");
    clearError("video");
    setNotice(null);
    setVideoStoryboard([]);
    setVideoStoryboardText("");
    setVideoStoryboardDirty(true);
    try {
      await api.streamVideoStoryboard(
        selectedCampaign.id,
        sourceIds,
        approvedDraft?.id ?? selectedDraft?.id ?? null,
        videoDurationSeconds,
        videoAspectRatio,
        videoInstructions,
        (event: VideoStoryboardTextStreamEvent) => {
          if (event.type === "start") {
            setVideoAspectRatio(event.aspect_ratio);
            setVideoDurationSeconds(event.duration_seconds);
            return;
          }
          if (event.type === "delta") {
            streamedText += event.text;
            setVideoStoryboardText(streamedText);
            return;
          }
          if (event.type === "done") {
            streamedText = event.text ?? streamedText;
            setVideoStoryboardText(streamedText);
            setVideoAspectRatio(event.aspect_ratio);
            setVideoDurationSeconds(event.duration_seconds);
            return;
          }
          if (event.type === "error") {
            throw new Error(event.message);
          }
        },
      );
      if (!streamedText.trim()) {
        throw new Error("模型未返回视频脚本，请重试。");
      }
      setNotice("视频脚本已生成");
    } catch (caught) {
      const message = apiErrorMessage(caught, "视频脚本生成失败");
      if (streamedText.trim() && isTransientApiError(caught)) {
        clearError("video");
        setNotice(`已保留当前脚本内容。${message}`);
      } else {
        setCaughtError("video", caught, "视频脚本生成失败");
      }
      if (!streamedText.trim() && previousText.trim()) {
        setVideoStoryboardText(previousText);
      }
    } finally {
      setLoading((current) => (current === "video-storyboard" ? null : current));
    }
  }

  async function handleRewriteVideoStoryboard() {
    if (!selectedCampaign) return;
    const sourceIds = selectedCreativeIdsForVideo();
    const feedback = videoStoryboardFeedback.trim();
    if (!sourceIds.length) {
      setError("请先审核通过并选择至少一张图片。", "video");
      return;
    }
    if (sourceIds.length > VIDEO_MAX_REFERENCE_IMAGES) {
      setError(`视频生成最多支持 ${VIDEO_MAX_REFERENCE_IMAGES} 张参考图片，请减少选择。`, "video");
      return;
    }
    if (!videoStoryboardText.trim()) {
      setError("请先生成或填写视频脚本。", "video");
      return;
    }
    if (!feedback) {
      setError("请先填写脚本修改意见。", "video");
      return;
    }
    const storyboardPayload = videoStoryboardDirty
      ? storyboardPayloadFromText(videoStoryboardText, videoStoryboard)
      : videoStoryboard;
    const previousText = videoStoryboardText;
    let streamedText = "";
    setLoading("video-storyboard-rewrite");
    clearError("video");
    setNotice(null);
    setVideoStoryboard([]);
    setVideoStoryboardText("");
    setVideoStoryboardDirty(true);
    try {
      await api.streamRewriteVideoStoryboard(
        {
          campaignId: selectedCampaign.id,
          creativeAssetIds: sourceIds,
          draftId: approvedDraft?.id ?? selectedDraft?.id ?? null,
          durationSeconds: videoDurationSeconds,
          aspectRatio: videoAspectRatio,
          storyboard: storyboardPayload,
          storyboardText: previousText,
          feedback,
        },
        (event: VideoStoryboardTextStreamEvent) => {
          if (event.type === "start") {
            setVideoAspectRatio(event.aspect_ratio);
            setVideoDurationSeconds(event.duration_seconds);
            return;
          }
          if (event.type === "delta") {
            streamedText += event.text;
            setVideoStoryboardText(streamedText);
            return;
          }
          if (event.type === "done") {
            streamedText = event.text ?? streamedText;
            setVideoStoryboardText(streamedText);
            setVideoAspectRatio(event.aspect_ratio);
            setVideoDurationSeconds(event.duration_seconds);
            return;
          }
          if (event.type === "error") {
            throw new Error(event.message);
          }
        },
      );
      if (!streamedText.trim()) {
        throw new Error("模型未返回改写脚本，请重试。");
      }
      setVideoStoryboardFeedback("");
      setNotice("脚本已按意见改写");
    } catch (caught) {
      const message = apiErrorMessage(caught, "视频脚本改写失败");
      if (streamedText.trim() && isTransientApiError(caught)) {
        clearError("video");
        setNotice(`已保留当前改写内容。${message}`);
      } else {
        setCaughtError("video", caught, "视频脚本改写失败");
      }
      if (!streamedText.trim()) {
        setVideoStoryboardText(previousText);
      }
    } finally {
      setLoading((current) => (current === "video-storyboard-rewrite" ? null : current));
    }
  }

  async function handleCreateVideo() {
    if (!selectedCampaign) return;
    const sourceIds = selectedCreativeIdsForVideo();
    if (!sourceIds.length) {
      setError("请先选择审核通过的图片。", "video");
      return;
    }
    if (!videoStoryboardText.trim()) {
      setError("请先生成或填写视频脚本。", "video");
      return;
    }
    const storyboardPayload = videoStoryboardDirty
      ? storyboardPayloadFromText(videoStoryboardText, videoStoryboard)
      : videoStoryboard;
    setLoading("video");
    clearError("video");
    setNotice(null);
    let createdVideo: VideoAsset | null = null;
    try {
      const created = await api.createVideoFromImages({
        campaignId: selectedCampaign.id,
        creativeAssetIds: sourceIds,
        draftId: approvedDraft?.id ?? selectedDraft?.id ?? null,
        prompt: videoStoryboardText,
        durationSeconds: videoDurationSeconds,
        aspectRatio: videoAspectRatio,
        storyboard: storyboardPayload,
      });
      createdVideo = created;
      setVideos((current) => prependOrReplaceById(current, created));
      setSelectedVideoId(created.id);
      setActiveView("videos");
      void saveWorkflowStage("video_review");
      const started = await api.startVideoGeneration(created.id);
      setVideos((current) => current.map((item) => (item.id === started.id ? started : item)));
      setSelectedVideoId(started.id);
      clearVideoPollWarning(started.id);
      clearError("video");
      setNotice("视频任务已创建，正在生成成片");
    } catch (caught) {
      const message = apiErrorMessage(caught, "视频任务创建或生成失败");
      if (createdVideo && !isAuthApiError(caught)) {
        setVideoPollWarning(createdVideo.id, message);
        setNotice("视频任务已创建，提交生成时遇到波动，可在任务卡片中重试。");
      } else {
        setCaughtError("video", caught, "视频任务创建或生成失败");
      }
    } finally {
      setLoading((current) => (current === "video" ? null : current));
    }
  }

  async function handleStartVideoGeneration(videoId: string) {
    const video = await run(
      `video-generate-${videoId}`,
      () => api.startVideoGeneration(videoId),
      "视频生成任务已重新提交",
    );
    if (video) {
      setVideos((current) => current.map((item) => (item.id === video.id ? video : item)));
      clearVideoPollWarning(video.id);
    }
  }

  function selectedCreativeIdsForVideo(): string[] {
    const approvedIds = new Set(approvedCreatives.map((item) => item.id));
    const selected = selectedCreativeIds.filter((id) => approvedIds.has(id));
    return (selected.length ? selected : approvedCreatives.slice(0, VIDEO_MAX_REFERENCE_IMAGES).map((item) => item.id)).slice(
      0,
      VIDEO_MAX_REFERENCE_IMAGES,
    );
  }

  function applyStoryboard(generated: VideoStoryboardResponse) {
    setVideoAspectRatio(generated.aspect_ratio);
    setVideoDurationSeconds(generated.duration_seconds);
    setVideoStoryboard(generated.storyboard);
    setVideoStoryboardText(formatStoryboard(generated.storyboard));
    setVideoStoryboardDirty(false);
  }

  function prepareFinalPayload(): Record<string, unknown> | null {
    const result = buildFinalPayload({
      job: selectedJob,
      campaign: selectedCampaign,
      topic: topics.find((item) => item.status === "selected") ?? selectedTopic,
      draft: approvedDraft,
      creatives: approvedCreatives,
      videos: approvedVideos,
      selectedCreativeIds,
      selectedVideoId,
    });
    if (!result.ok) {
      setError(result.message, "final");
      setNotice(null);
      return null;
    }
    setFinalPayloadDraft(JSON.stringify(result.value, null, 2));
    clearError("final");
    setNotice("最终预审包已生成，请检查后确认回传。");
    return result.value;
  }

  async function handleSaveFinalPayload() {
    if (!selectedJob) return;
    const parsed = parseFinalPayload();
    if (!parsed) return;
    const job = await run(
      "save-final-payload",
      () => api.updateAdGenerationReview(selectedJob.id, parsed, finalReviewNotes),
      "最终预审包已保存",
    );
    if (job) setJobs((current) => upsertById(current, job));
  }

  async function handleConfirmReturn() {
    if (!selectedJob) return;
    const parsed = finalPayloadDraft.trim() ? parseFinalPayload() : prepareFinalPayload();
    if (!parsed) return;
    const job = await run(
      "confirm-return",
      () => api.confirmAdGenerationReview(selectedJob.id, parsed, finalReviewNotes),
      "已确认回传",
    );
    if (!job) {
      void refreshJob(selectedJob.id);
      return;
    }
    setJobs((current) => upsertById(current, job));
    if (job.return_url) {
      const target = new URL(job.return_url);
      target.searchParams.set("job_id", job.id);
      target.searchParams.set("status", job.status);
      const accessToken = getAccessToken();
      if (accessToken) {
        target.searchParams.set("access_token", accessToken);
        target.searchParams.set("ai_access_token", accessToken);
      }
      if (job.external_order_id) target.searchParams.set("external_order_id", job.external_order_id);
      window.location.href = target.toString();
    }
  }

  function parseFinalPayload(): Record<string, unknown> | null {
    try {
      const parsed = JSON.parse(finalPayloadDraft || "{}");
      if (!isRecord(parsed)) {
        setError("最终预审包必须是 JSON 对象。", "final");
        return null;
      }
      return parsed;
    } catch {
      setError("最终预审包不是有效 JSON，请检查逗号和引号。", "final");
      return null;
    }
  }

  async function saveWorkflowStage(stage: string) {
    if (!selectedJob) return;
    const current = selectedJob.result_payload ?? {};
    const metadata = isRecord(current.metadata_json) ? current.metadata_json : {};
    const job = await api.updateAdGenerationReview(selectedJob.id, {
      ...current,
      status: stage,
      metadata_json: { ...metadata, workflow_stage: stage },
    });
    setJobs((items) => upsertById(items, job));
  }

  const workflowSummary = buildWorkflowSummary({
    job: selectedJob,
    campaign: selectedCampaign,
    topic: topics.find((item) => item.status === "selected") ?? selectedTopic,
    draft: approvedDraft,
    creatives: approvedCreatives,
    videos: approvedVideos,
  });
  const visibleError = error && shouldShowErrorBanner(error, activeView) ? error : null;
  const bannerTone = visibleError ? (visibleError.transient ? "warning" : "error") : "success";

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-block">
          <div className="brand-mark">AI</div>
          <div>
            <div className="brand-name">AI 投放生产</div>
            <div className="brand-meta">内容审核工作台</div>
          </div>
        </div>

        <nav className="nav-list">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                className={`nav-button ${activeView === item.key ? "active" : ""}`}
                key={item.key}
                onClick={() => setActiveView(item.key)}
              >
                <Icon size={18} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>

        <div className="sidebar-footer">
          <div className="api-label">API</div>
          <div className="api-url">{api.baseUrl}</div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="topbar-title">
            <span className="topbar-eyebrow">Operations Console</span>
            <h1>{viewTitle(activeView)}</h1>
            <p>{viewSubtitles[activeView]}</p>
          </div>
          <div className="topbar-actions">
            <button className="icon-button" onClick={() => void refreshBaseData()} title="刷新">
              {loading === "refresh" ? <Loader2 size={18} className="spin" /> : <RefreshCw size={18} />}
            </button>
          </div>
        </header>

        {(visibleError || notice) && (
          <div className={`banner ${bannerTone}`}>
            {visibleError ? (
              visibleError.transient ? (
                <Clock3 size={18} />
              ) : (
                <X size={18} />
              )
            ) : (
              <Check size={18} />
            )}
            <span>{visibleError?.message || notice}</span>
          </div>
        )}

        {activeView === "dashboard" && (
          <DashboardView
            jobs={jobs}
            selectedJob={selectedJob}
            summary={workflowSummary}
            setSelectedJobId={setSelectedJobId}
            setActiveView={setActiveView}
          />
        )}

        {activeView === "performance" && (
          <PerformanceAnalysisView
            analyses={performanceAnalyses}
            selectedAnalysis={selectedPerformanceAnalysis}
            setSelectedAnalysisId={setSelectedPerformanceAnalysisId}
            onCreateAnalysis={(payload) => handleCreatePerformanceAnalysis(payload)}
            onDeleteAnalysis={(analysisId) => void handleDeletePerformanceAnalysis(analysisId)}
            onRefresh={() => void refreshPerformanceAnalyses()}
            loading={loading}
          />
        )}

        {activeView === "work-orders" && (
          <WorkOrdersView
            rawWorkOrder={rawWorkOrder}
            setRawWorkOrder={setRawWorkOrder}
            jobs={jobs}
            selectedJob={selectedJob}
            setSelectedJobId={setSelectedJobId}
            onCreateWorkOrder={handleCreateWorkOrder}
            onDeleteJob={(jobId) => void handleDeleteJob(jobId)}
            onOpenWorkflow={() => setActiveView("workflow")}
            loading={loading}
            hasCachedDeliveryExtraction={
              Boolean(deliveryExtractionCache) ||
              Boolean(deliveryExtraction && deliveryConfirmRawContent === rawWorkOrder.trim())
            }
          />
        )}

        {activeView === "workflow" && (
          <WorkflowView
            jobs={jobs}
            selectedJob={selectedJob}
            setSelectedJobId={setSelectedJobId}
            campaign={selectedCampaign}
            summary={workflowSummary}
            topics={topics}
            drafts={drafts}
            creatives={creatives}
            videos={videos}
            finalPayloadDraft={finalPayloadDraft}
            setFinalPayloadDraft={setFinalPayloadDraft}
            finalReviewNotes={finalReviewNotes}
            setFinalReviewNotes={setFinalReviewNotes}
            onRefresh={() => (selectedJob ? void refreshJob(selectedJob.id) : void refreshBaseData())}
            onGenerateTopics={() => void handleGenerateTopics()}
            onGenerateCopy={() => void handleGenerateCopy()}
            onGenerateCreatives={() => void handleGenerateCreatives()}
            onGoToView={setActiveView}
            onPrepareFinal={prepareFinalPayload}
            onSaveFinal={() => void handleSaveFinalPayload()}
            onConfirmReturn={() => void handleConfirmReturn()}
            loading={loading}
            operationElapsedSeconds={operationElapsedSeconds}
          />
        )}

        {activeView === "topics" && (
          <TopicsView
            topics={topics}
            topicGenerationSlots={topicGenerationSlots}
            selectedTopic={selectedTopic}
            setSelectedTopicId={setSelectedTopicId}
            topicFeedback={topicFeedback}
            setTopicFeedback={setTopicFeedback}
            onGenerate={(feedback) => void handleGenerateTopics(feedback)}
            onRetryTopicSlot={(index) => void handleRetryTopicSlot(index)}
            onSelect={(id) => void handleSelectTopic(id)}
            loading={loading}
          />
        )}

        {activeView === "copy" && (
          <CopyView
            drafts={drafts}
            selectedDraft={selectedDraft}
            setSelectedDraftId={setSelectedDraftId}
            selectedTopic={topics.find((item) => item.status === "selected") ?? selectedTopic}
            campaign={selectedCampaign}
            creatives={creatives}
            feedback={copyFeedback}
            setFeedback={setCopyFeedback}
            onGenerateCopy={() => void handleGenerateCopy()}
            onReviseCopy={() => void handleReviseCopy()}
            onReview={handleReview}
            onOpenCreatives={() => setActiveView("creatives")}
            loading={loading}
          />
        )}

        {activeView === "creatives" && (
          <CreativesView
            creatives={creatives}
            creativeGenerationSlots={creativeGenerationSlots}
            selectedCreativeIds={selectedCreativeIds}
            setSelectedCreativeIds={setSelectedCreativeIds}
            rewriteFeedbacks={creativeRewriteFeedbacks}
            setRewriteFeedback={(assetId, value) =>
              setCreativeRewriteFeedbacks((current) => ({ ...current, [assetId]: value }))
            }
            onGenerate={() => void handleGenerateCreatives()}
            onRetrySlot={(index) => void handleRetryCreativeSlot(index)}
            onRegenerate={(asset) => void handleRegenerateCreative(asset)}
            onReview={handleReview}
            onCreateVideo={() => setActiveView("videos")}
            loading={loading}
          />
        )}

        {activeView === "videos" && (
          <VideosView
            campaign={selectedCampaign}
            draft={approvedDraft ?? selectedDraft}
            videos={videos}
            videoPollWarnings={videoPollWarnings}
            approvedCreatives={approvedCreatives}
            selectedCreativeIds={selectedCreativeIdsForVideo()}
            setSelectedCreativeIds={setSelectedCreativeIds}
            selectedVideoId={selectedVideoId}
            setSelectedVideoId={setSelectedVideoId}
            aspectRatio={videoAspectRatio}
            setAspectRatio={setVideoAspectRatio}
            durationSeconds={videoDurationSeconds}
            setDurationSeconds={setVideoDurationSeconds}
            instructions={videoInstructions}
            setInstructions={setVideoInstructions}
            storyboardText={videoStoryboardText}
            setStoryboardText={(value) => {
              setVideoStoryboardText(value);
              setVideoStoryboardDirty(true);
            }}
            storyboardFeedback={videoStoryboardFeedback}
            setStoryboardFeedback={setVideoStoryboardFeedback}
            onGenerateStoryboard={() => void handleGenerateVideoStoryboard()}
            onRewriteStoryboard={() => void handleRewriteVideoStoryboard()}
            onCreateVideo={() => void handleCreateVideo()}
            onStartGeneration={(id) => void handleStartVideoGeneration(id)}
            onReview={handleReview}
            loading={loading}
          />
        )}

        <DeliveryConfirmDialog
          open={deliveryConfirmOpen}
          extraction={deliveryExtraction}
          form={deliveryConfirmForm}
          loading={loading === "create-ad-generation"}
          onChange={(key, value) => setDeliveryConfirmForm((current) => ({ ...current, [key]: value }))}
          onConfirm={() => void handleConfirmCreateWorkOrder()}
          onCancel={handleCancelDeliveryConfirm}
        />
      </main>
    </div>
  );
}

function DashboardView({
  jobs,
  selectedJob,
  summary,
  setSelectedJobId,
  setActiveView,
}: {
  jobs: AdGenerationJob[];
  selectedJob: AdGenerationJob | null;
  summary: WorkflowSummary;
  setSelectedJobId: (id: string) => void;
  setActiveView: (view: ViewKey) => void;
}) {
  const pending = jobs.filter((item) => ["queued", "processing"].includes(item.status)).length;
  const inReview = jobs.filter((item) =>
    ["fields_review", "topic_review", "copy_review", "image_review", "video_review", "final_review"].includes(
      item.status,
    ),
  ).length;
  const returned = jobs.filter((item) => item.status === "returned").length;
  const summarySteps = workflowSummarySteps(summary);
  const currentStep = currentWorkflowStep(summary);
  const currentStepIndex = summarySteps.findIndex((step) => step.key === currentStep.key);
  const completedCount = summarySteps.filter((step) => step.done).length;
  const progressPercent = workflowProgressPercent(summary);
  const activeJob = selectedJob ?? jobs[0] ?? null;
  const reviewQueue = jobs
    .filter((item) =>
      ["fields_review", "topic_review", "copy_review", "image_review", "video_review", "final_review"].includes(
        item.status,
      ),
    )
    .slice(0, 5);

  return (
    <section className="view-stack dashboard-view">
      <section className="dashboard-command-center">
        <div className="dashboard-command-copy">
          <span className="section-eyebrow">Current production</span>
          <h2>{activeJob ? adGenerationJobTitle(activeJob) : "等待创建 AI 工单"}</h2>
          <p>
            {activeJob
              ? `${workflowStepStatusLabel(currentStep.status)}：${currentStep.title} / ${currentStep.label}`
              : "粘贴投放工单后，系统会进入参数确认、选题、文案、图片、视频和最终预审。"}
          </p>
          <div className="dashboard-command-actions">
            <button
              className="primary-button"
              onClick={() => setActiveView(activeJob ? "workflow" : "work-orders")}
            >
              <Check size={16} />
              <span>{activeJob ? "继续生产" : "创建工单"}</span>
            </button>
            {activeJob && (
              <button
                className="secondary-button"
                onClick={() => {
                  setSelectedJobId(activeJob.id);
                  setActiveView("workflow");
                }}
              >
                <RefreshCw size={16} />
                <span>查看任务</span>
              </button>
            )}
          </div>
        </div>

        <div className="dashboard-stage-card">
          <div className="stage-meter" style={{ "--progress": `${progressPercent}%` } as React.CSSProperties}>
            <strong>{progressPercent}%</strong>
            <span>完成度</span>
          </div>
          <div className="stage-meter-copy">
            <span>生产轨道</span>
            <strong>{currentStepIndex + 1}. {currentStep.title}</strong>
            <p>{completedCount} / {summarySteps.length} 个步骤已完成</p>
          </div>
        </div>
      </section>

      <div className="metrics-grid">
        <Metric label="AI 工单" value={jobs.length} accent="blue" hint="全部任务" icon={ClipboardList} />
        <Metric label="生成中" value={pending} accent="amber" hint="等待参数识别" icon={Clock3} />
        <Metric label="审核中" value={inReview} accent="violet" hint="人工流程推进" icon={Check} />
        <Metric label="已回传" value={returned} accent="rose" hint="投放系统可拉取" icon={Send} />
      </div>

      <div className="dashboard-grid">
        <section className="panel workflow-panel">
          <div className="panel-header">
            <h2>当前生产进度</h2>
            <button className="primary-button" onClick={() => setActiveView("workflow")} disabled={!activeJob}>
              <Check size={16} />
              <span>进入 AI 生产</span>
            </button>
          </div>
          <WorkflowProgress summary={summary} />
        </section>

        <section className="panel dashboard-queue-panel">
          <div className="panel-header">
            <h2>待处理队列</h2>
            <button className="secondary-button" onClick={() => setActiveView("work-orders")}>
              <Sparkles size={16} />
              <span>创建工单</span>
            </button>
          </div>
          <DataList emptyText="暂无待处理工单">
            {(reviewQueue.length ? reviewQueue : jobs.slice(0, 5)).map((job) => (
              <button
                className="list-row-button dashboard-job-row"
                key={job.id}
                onClick={() => {
                  setSelectedJobId(job.id);
                  setActiveView("workflow");
                }}
              >
                <div>
                  <strong>{adGenerationJobTitle(job)}</strong>
                  <span>{formatDate(job.created_at)} / {job.external_order_id || shortId(job.id)}</span>
                </div>
                <StatusPill status={job.status} />
              </button>
            ))}
          </DataList>
        </section>
      </div>
    </section>
  );
}

function WorkOrdersView({
  rawWorkOrder,
  setRawWorkOrder,
  jobs,
  selectedJob,
  setSelectedJobId,
  onCreateWorkOrder,
  onDeleteJob,
  onOpenWorkflow,
  loading,
  hasCachedDeliveryExtraction,
}: {
  rawWorkOrder: string;
  setRawWorkOrder: (value: string) => void;
  jobs: AdGenerationJob[];
  selectedJob: AdGenerationJob | null;
  setSelectedJobId: (id: string | null) => void;
  onCreateWorkOrder: () => void;
  onDeleteJob: (jobId: string) => void;
  onOpenWorkflow: () => void;
  loading: string | null;
  hasCachedDeliveryExtraction: boolean;
}) {
  const createLoading = loading === "extract-work-order" || loading === "create-ad-generation";
  const createButtonLabel =
    loading === "extract-work-order"
      ? "识别中"
      : hasCachedDeliveryExtraction
        ? "使用上次识别"
        : "识别参数";
  return (
    <section className="two-column work-order-layout">
      <section className="panel wide">
        <div className="panel-header">
          <h2>创建 AI 工单</h2>
          <button className="primary-button" onClick={onCreateWorkOrder} disabled={createLoading}>
            {createLoading ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />}
            <span>{createButtonLabel}</span>
          </button>
        </div>
        <textarea
          className="work-order-input"
          value={rawWorkOrder}
          onChange={(event) => setRawWorkOrder(event.target.value)}
        />
        {hasCachedDeliveryExtraction && (
          <div className="work-order-cache-note">这段工单的识别结果已缓存，确认前不会创建 AI 工单。</div>
        )}
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2>AI 工单列表</h2>
          {selectedJob && (
            <button className="secondary-button" onClick={onOpenWorkflow}>
              <Check size={16} />
              <span>进入生产</span>
            </button>
          )}
        </div>
        <DataList emptyText="暂无 AI 工单">
          {jobs.map((job) => (
            <div className={`job-list-row ${selectedJob?.id === job.id ? "active" : ""}`} key={job.id}>
              <button className="list-button job-select-button" onClick={() => setSelectedJobId(job.id)}>
                <strong>{adGenerationJobTitle(job)}</strong>
                <span>{job.external_order_id || shortId(job.id)}</span>
                <StatusPill status={job.status} />
              </button>
              <button
                className="icon-button danger job-delete-button"
                onClick={() => onDeleteJob(job.id)}
                title="删除 AI 工单及关联素材"
              >
                <Trash2 size={16} />
              </button>
            </div>
          ))}
        </DataList>
      </section>
    </section>
  );
}

function WorkflowView({
  jobs,
  selectedJob,
  setSelectedJobId,
  campaign,
  summary,
  topics,
  drafts,
  creatives,
  videos,
  finalPayloadDraft,
  setFinalPayloadDraft,
  finalReviewNotes,
  setFinalReviewNotes,
  onRefresh,
  onGenerateTopics,
  onGenerateCopy,
  onGenerateCreatives,
  onGoToView,
  onPrepareFinal,
  onSaveFinal,
  onConfirmReturn,
  loading,
  operationElapsedSeconds,
}: {
  jobs: AdGenerationJob[];
  selectedJob: AdGenerationJob | null;
  setSelectedJobId: (id: string) => void;
  campaign: Campaign | null;
  summary: WorkflowSummary;
  topics: Topic[];
  drafts: CopyDraft[];
  creatives: CreativeAsset[];
  videos: VideoAsset[];
  finalPayloadDraft: string;
  setFinalPayloadDraft: (value: string) => void;
  finalReviewNotes: string;
  setFinalReviewNotes: (value: string) => void;
  onRefresh: () => void;
  onGenerateTopics: () => void;
  onGenerateCopy: () => void;
  onGenerateCreatives: () => void;
  onGoToView: (view: ViewKey) => void;
  onPrepareFinal: () => Record<string, unknown> | null;
  onSaveFinal: () => void;
  onConfirmReturn: () => void;
  loading: string | null;
  operationElapsedSeconds: number;
}) {
  const result = selectedJob?.result_payload ?? {};
  const review = isRecord(result.review) ? result.review : {};
  const warnings = Array.isArray(review.warnings) ? review.warnings.map(String) : [];
  const missingFields = Array.isArray(review.missing_fields) ? review.missing_fields.map(String) : [];
  const finalPayloadForBrandSafety = finalPayloadDraft.trim() ? parseJsonRecord(finalPayloadDraft) : result;
  const brandSafetyReport = brandSafetyReportFromPayload(finalPayloadForBrandSafety);
  const brandSafetyBlocked = brandSafetyBlocksReturn(brandSafetyReport);
  const brandSafetyLabel = brandSafetySummaryLabel(brandSafetyReport);
  const hasRisks = warnings.length > 0 || missingFields.length > 0;
  const approvedImageCount = creatives.filter((item) => item.status === "approved").length;
  const approvedVideoCount = videos.filter((item) => item.status === "approved").length;
  const productionOverview = [
    { label: "选题", value: summary.topic.label, status: workflowStepStatusLabel(summary.topic.status) },
    { label: "文案", value: summary.copy.label, status: workflowStepStatusLabel(summary.copy.status) },
    { label: "图片", value: approvedImageCount ? `${approvedImageCount} 张已通过` : "未通过图片", status: workflowStepStatusLabel(summary.image.status) },
    { label: "视频", value: approvedVideoCount ? `${approvedVideoCount} 个已通过` : "未通过视频", status: workflowStepStatusLabel(summary.video.status) },
  ];

  const steps = [
    {
      key: "fields",
      label: "参数确认",
      detail: summary.fields.done ? "投放参数已确认" : "等待 LLM 识别工单参数",
      status: summary.fields.status,
      action: "刷新",
      loadingKey: "ad-generation-refresh",
      onAction: onRefresh,
      disabled: !selectedJob,
    },
    {
      key: "topic",
      label: "人工选题",
      detail: summary.topic.done ? "已选择选题" : topics.length ? "请选择选题" : "根据投放链接生成选题",
      status: summary.topic.status,
      action: topics.length ? "进入选题" : "生成选题",
      loadingKey: topics.length ? null : "topics",
      onAction: topics.length ? () => onGoToView("topics") : onGenerateTopics,
      disabled: !summary.fields.done,
    },
    {
      key: "copy",
      label: "审核文案",
      detail: summary.copy.done ? "文案已通过" : drafts.length ? "请审核文案" : "根据选题生成文案",
      status: summary.copy.status,
      action: drafts.length ? "进入文案" : "生成文案",
      loadingKey: drafts.length ? null : "copy",
      onAction: drafts.length ? () => onGoToView("copy") : onGenerateCopy,
      disabled: !summary.topic.done,
    },
    {
      key: "image",
      label: "审核图片",
      detail: summary.image.done ? "图片已通过" : creatives.length ? "请审核图片" : "根据文案生成图片",
      status: summary.image.status,
      action: creatives.length ? "进入图片" : "生成图片",
      loadingKey: creatives.length ? null : "creatives",
      onAction: creatives.length ? () => onGoToView("creatives") : onGenerateCreatives,
      disabled: !summary.copy.done,
    },
    {
      key: "video",
      label: "审核视频",
      detail: summary.video.done ? "视频已通过" : videos.length ? "请审核视频" : "根据图片创建视频",
      status: summary.video.status,
      action: "进入视频",
      loadingKey: null,
      onAction: () => onGoToView("videos"),
      disabled: !summary.image.done,
    },
    {
      key: "final",
      label: "最终预审",
      detail: summary.final.done ? "可回传投放系统" : "完成前面步骤后生成最终包",
      status: summary.final.status,
      action: "生成预审包",
      loadingKey: null,
      onAction: onPrepareFinal,
      disabled: !summary.final.done,
    },
  ];
  const activeStepIndex = steps.findIndex((step) => step.status === "active");
  const currentStepIndex = activeStepIndex >= 0 ? activeStepIndex : steps.findIndex((step) => step.key === "final");
  const currentStep = steps[currentStepIndex] ?? steps[0];
  const currentStepLoading = Boolean(loading) && currentStep.loadingKey === loading;
  const activeOperation = operationProgressText(loading, operationElapsedSeconds);
  const progressPercent = workflowProgressPercent(summary);
  const completedCount = workflowSummarySteps(summary).filter((step) => step.done).length;

  return (
    <section className="review-layout workflow-command-layout">
      <section className="panel review-task-panel">
        <div className="panel-header">
          <h2>任务</h2>
          <button className="icon-button" onClick={onRefresh} title="刷新">
            {loading === "ad-generation-refresh" ? <Loader2 size={18} className="spin" /> : <RefreshCw size={18} />}
          </button>
        </div>
        <DataList emptyText="暂无任务">
          {jobs.map((job) => (
            <button
              className={`list-button ${selectedJob?.id === job.id ? "active" : ""}`}
              key={job.id}
              onClick={() => setSelectedJobId(job.id)}
            >
              <strong>{adGenerationJobTitle(job)}</strong>
              <span>{job.external_order_id || shortId(job.id)}</span>
              <StatusPill status={job.status} />
            </button>
          ))}
        </DataList>
      </section>

      <section className="panel review-editor-panel">
        <div className="panel-header">
          <h2>AI 生产流程</h2>
          {selectedJob && <StatusPill status={selectedJob.status} />}
        </div>
        {selectedJob ? (
          <>
            <div className="workflow-brief">
              <div>
                <span className="section-eyebrow">Production brief</span>
                <h3>{adGenerationJobTitle(selectedJob)}</h3>
                <p>{campaign?.name || selectedJob.external_order_id || shortId(selectedJob.id)}</p>
              </div>
              <div className="workflow-brief-stat">
                <strong>{progressPercent}%</strong>
                <span>{completedCount} 个步骤完成</span>
              </div>
            </div>

            <div className={`current-production-card ${currentStep.status}`}>
              <div className="current-production-index">{currentStepIndex + 1}</div>
              <div className="current-production-body">
                <span>当前步骤</span>
                <strong>{currentStep.label}</strong>
                <p>{currentStep.detail}</p>
                {activeOperation && (
                  <div className="operation-progress">
                    <Loader2 size={14} className="spin" />
                    <span>{activeOperation.title}</span>
                    <em>
                      {activeOperation.estimate} / 已等待 {formatDuration(operationElapsedSeconds)}
                    </em>
                  </div>
                )}
              </div>
              <button
                className="primary-button"
                onClick={currentStep.onAction}
                disabled={currentStep.disabled || Boolean(loading)}
              >
                {currentStepLoading ? (
                  <Loader2 size={16} className="spin" />
                ) : currentStep.key === "fields" ? (
                  <RefreshCw size={16} />
                ) : (
                  <Sparkles size={16} />
                )}
                <span>{currentStep.action}</span>
              </button>
            </div>

            <div className="workflow-signal-grid">
              <div>
                <span>选题</span>
                <strong>{summary.topic.label}</strong>
              </div>
              <div>
                <span>已审素材</span>
                <strong>{approvedImageCount} 图 / {approvedVideoCount} 视频</strong>
              </div>
              <div className={hasRisks ? "warning" : "ready"}>
                <span>预审状态</span>
                <strong>{hasRisks ? "需要核对" : "暂无风险"}</strong>
              </div>
            </div>

            {hasRisks && (
              <div className="review-warning-box">
                <strong>风险提示</strong>
                <ul>
                  {missingFields.map((item) => (
                    <li key={`missing-${item}`}>缺失字段：{item}</li>
                  ))}
                  {warnings.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            )}

            <div className="workflow-steps">
              {steps.map((step, index) => (
                <div className={`workflow-step ${step.status}`} key={step.key}>
                  <div className="workflow-step-index">{index + 1}</div>
                  <div className="workflow-step-body">
                    <div className="workflow-step-title">
                      <strong>{step.label}</strong>
                      <span>{workflowStepStatusLabel(step.status)}</span>
                    </div>
                    <p>{step.detail}</p>
                  </div>
                  <button
                    className="secondary-button"
                    onClick={step.onAction}
                    disabled={step.disabled || Boolean(loading)}
                  >
                    {loading && loading === step.loadingKey ? (
                      <Loader2 size={16} className="spin" />
                    ) : (
                      <Sparkles size={16} />
                    )}
                    <span>{step.action}</span>
                  </button>
                </div>
              ))}
            </div>

            <details className="final-package-panel" open={summary.final.done || Boolean(finalPayloadDraft.trim())}>
              <summary>
                <span>最终预审包</span>
                <em>{finalPayloadDraft.trim() ? "已生成" : "待生成"}</em>
              </summary>
              <label className="editor-label" htmlFor="final-payload">
                JSON
              </label>
              <textarea
                id="final-payload"
                className="json-editor"
                value={finalPayloadDraft}
                onChange={(event) => setFinalPayloadDraft(event.target.value)}
                spellCheck={false}
              />
              <div className={`brand-safety-card ${brandSafetyBlocked ? "blocked" : brandSafetyReport ? "passed" : "pending"}`}>
                <div className="brand-safety-card-head">
                  <strong>{brandSafetyLabel}</strong>
                  <span>{brandSafetyReport ? `${brandSafetyReport.findings.length} 项命中` : "确认回传时后端会重新检查"}</span>
                </div>
                {brandSafetyReport?.findings.length ? (
                  <ul>
                    {brandSafetyReport.findings.slice(0, 6).map((finding, index) => (
                      <li key={`${finding.field_path}-${finding.matched_text}-${index}`}>
                        <span>{finding.category}</span>
                        <strong>{finding.matched_text}</strong>
                        <em>{finding.field_path}</em>
                        {finding.suggestion && <p>{finding.suggestion}</p>}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p>{brandSafetyReport ? "未发现赌博、博彩、药品、金钱或价格优势表达。" : "生成或确认后会显示品牌安全检查结果。"}</p>
                )}
              </div>
              <label className="editor-label" htmlFor="final-notes">
                预审备注
              </label>
              <textarea
                id="final-notes"
                className="review-notes-input"
                value={finalReviewNotes}
                onChange={(event) => setFinalReviewNotes(event.target.value)}
                placeholder="记录人工审核意见、素材选择和需要投放系统注意的事项"
              />
              <div className="button-row">
                <button className="secondary-button" onClick={onPrepareFinal} disabled={!summary.final.done}>
                  <Sparkles size={16} />
                  <span>生成预审包</span>
                </button>
                <button className="secondary-button" onClick={onSaveFinal} disabled={!finalPayloadDraft.trim()}>
                  <Check size={16} />
                  <span>保存预审包</span>
                </button>
                <button className="primary-button" onClick={onConfirmReturn} disabled={!summary.final.done || brandSafetyBlocked}>
                  <Send size={16} />
                  <span>确认并回传</span>
                </button>
              </div>
            </details>
          </>
        ) : (
          <EmptyState text="请选择或创建一个 AI 工单" />
        )}
      </section>

      <section className="panel review-preview-panel">
        <div className="panel-header">
          <h2>任务上下文</h2>
        </div>
        {selectedJob ? (
          <div className="review-preview-stack">
            <section className="context-section">
              <h3>投放参数</h3>
              <KeyValueTable data={adGenerationJobFields(selectedJob, campaign)} />
            </section>
            <section className="context-section">
              <h3>生产结果</h3>
              <div className="production-overview-list">
                {productionOverview.map((item) => (
                  <div className="production-overview-row" key={item.label}>
                    <span>{item.label}</span>
                    <strong>{item.value}</strong>
                    <em>{item.status}</em>
                  </div>
                ))}
              </div>
            </section>
            <details className="raw-work-order-details">
              <summary>工单原文</summary>
              <pre className="raw-work-order-preview">{adGenerationRawContent(selectedJob.request_payload)}</pre>
            </details>
          </div>
        ) : (
          <EmptyState text="暂无预览" />
        )}
      </section>
    </section>
  );
}

function TopicsView({
  topics,
  topicGenerationSlots,
  selectedTopic,
  setSelectedTopicId,
  topicFeedback,
  setTopicFeedback,
  onGenerate,
  onRetryTopicSlot,
  onSelect,
  loading,
}: {
  topics: Topic[];
  topicGenerationSlots: TopicGenerationSlot[];
  selectedTopic: Topic | null;
  setSelectedTopicId: (id: string) => void;
  topicFeedback: string;
  setTopicFeedback: (value: string) => void;
  onGenerate: (feedback?: string) => void;
  onRetryTopicSlot: (index: number) => void;
  onSelect: (topicId: string) => void;
  loading: string | null;
}) {
  const activeTopic = selectedTopic ?? topics[0] ?? null;
  const visibleSlots =
    topicGenerationSlots.length > 0
      ? topicGenerationSlots
      : topics.map((topic, index) => ({
          index: index + 1,
          status: "done" as const,
          topic,
        }));
  const activeIndex = activeTopic
    ? visibleSlots.findIndex((slot) => slot.topic?.id === activeTopic.id)
    : -1;
  const generatedSlotCount = visibleSlots.filter((slot) => slot.status === "done").length;
  const hasGeneratingSlots = topicGenerationSlots.some((slot) => slot.status === "loading");
  const isGeneratingTopics = loading === "topics" || hasGeneratingSlots;
  const canRegenerateWithFeedback = topicFeedback.trim().length > 0 && !isGeneratingTopics;
  const panelNote = visibleSlots.length
    ? isGeneratingTopics
      ? `${generatedSlotCount}/${visibleSlots.length} 个方向已生成`
      : `${generatedSlotCount} 个方向待选择`
    : "还没有生成选题";

  return (
    <section className="topic-review-layout">
      <section className="panel topic-list-panel">
        <div className="panel-header">
          <div>
            <h2>候选选题</h2>
            <span className="panel-note">{panelNote}</span>
          </div>
          {!topics.length && !topicGenerationSlots.length && (
            <button className="primary-button" onClick={() => onGenerate()} disabled={isGeneratingTopics}>
              {isGeneratingTopics ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />}
              <span>生成选题</span>
            </button>
          )}
        </div>
        {topics.length > 0 && !hasGeneratingSlots && (
          <section className="topic-feedback-panel topic-batch-regenerate">
            <div className="topic-feedback-head">
              <div>
                <strong>整体调整选题方向</strong>
                <span>这里会重新生成 3 个候选方向，并参考当前这组选题避开不满意的表达。</span>
              </div>
            </div>
            <textarea
              className="topic-feedback-input"
              value={topicFeedback}
              onChange={(event) => setTopicFeedback(event.target.value)}
              disabled={isGeneratingTopics}
              placeholder="例如：减少价格卖点，突出印度家庭客厅观影和上门安装，不要体育赛事素材。"
            />
            <div className="topic-feedback-actions">
              <button
                className="secondary-button"
                onClick={() => onGenerate(topicFeedback)}
                disabled={!canRegenerateWithFeedback}
              >
                {isGeneratingTopics ? <Loader2 size={16} className="spin" /> : <RefreshCw size={16} />}
                <span>重新生成 3 个候选</span>
              </button>
            </div>
          </section>
        )}
        {visibleSlots.length ? (
          <div className="topic-option-list">
            {visibleSlots.map((slot) => {
              if (slot.status === "done" && slot.topic) {
                const topic = slot.topic;
                return (
                  <button
                    className={`topic-option ${activeTopic?.id === topic.id ? "active" : ""}`}
                    key={topic.id}
                    onClick={() => setSelectedTopicId(topic.id)}
                  >
                    <span className="topic-option-index">{slot.index}</span>
                    <span className="topic-option-main">
                      <strong>{topic.title}</strong>
                      <em>{topic.angle}</em>
                    </span>
                    <StatusPill status={topic.status} />
                  </button>
                );
              }

              if (slot.status === "error") {
                const isRetrying = loading === `topic-retry-${slot.index}`;
                return (
                  <div className="topic-option error" key={`topic-slot-${slot.index}`}>
                    <span className="topic-option-index">{slot.index}</span>
                    <span className="topic-option-main">
                      <strong>候选 {slot.index} 生成失败</strong>
                      <em>{slot.message || "模型没有返回此候选。"}</em>
                    </span>
                    <button
                      className="secondary-button topic-slot-retry"
                      onClick={() => onRetryTopicSlot(slot.index)}
                      disabled={Boolean(loading)}
                    >
                      {isRetrying ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />}
                      <span>重试</span>
                    </button>
                  </div>
                );
              }

              return (
                <div className="topic-option loading" key={`topic-slot-${slot.index}`} aria-live="polite">
                  <span className="topic-option-index">{slot.index}</span>
                  <span className="topic-option-main">
                    <strong>候选 {slot.index} 生成中</strong>
                    <em>完成后会自动显示在这里，可先采用已出现的选题。</em>
                  </span>
                  <span className="topic-slot-state">
                    <Loader2 size={14} className="spin" />
                    生成中
                  </span>
                </div>
              );
            })}
          </div>
        ) : (
          <EmptyState text="暂无选题，请先生成" />
        )}
      </section>

      <section className="panel topic-detail-panel">
        <div className="panel-header">
          <div>
            <h2>选题详情</h2>
            <span className="panel-note">
              {activeIndex >= 0 ? `候选方向 ${activeIndex + 1}` : "等待选择一个方向"}
            </span>
          </div>
          {activeTopic && <StatusPill status={activeTopic.status} />}
        </div>
        {activeTopic ? (
          <article className="topic-detail-card">
            <div className="topic-detail-hero">
              <span className="topic-kicker">Topic Direction</span>
              <h3>{activeTopic.title}</h3>
              <p>{activeTopic.angle}</p>
            </div>

            <div className="topic-insight-grid">
              <section>
                <span>目标人群</span>
                <strong>{activeTopic.audience || "未单独标注"}</strong>
              </section>
              <section>
                <span>评分</span>
                <strong>{activeTopic.score == null ? "未评分" : `${Math.round(activeTopic.score * 100)}%`}</strong>
              </section>
            </div>

            <section className="topic-selling-section">
              <h4>核心卖点</h4>
              <div className="topic-selling-list">
                {activeTopic.selling_points.length ? (
                  activeTopic.selling_points.map((item) => (
                    <span key={item}>{item}</span>
                  ))
                ) : (
                  <em>暂无卖点摘要</em>
                )}
              </div>
            </section>

            {activeTopic.rationale && (
              <section className="topic-rationale">
                <h4>推荐理由</h4>
                <p>{activeTopic.rationale}</p>
              </section>
            )}

            <div className="topic-detail-actions">
              <button className="primary-button" onClick={() => onSelect(activeTopic.id)}>
                <Check size={16} />
                <span>{activeTopic.status === "selected" ? "已采用此选题" : "采用此选题"}</span>
              </button>
            </div>
          </article>
        ) : (
          <EmptyState text="请选择或生成一个选题" />
        )}
      </section>
    </section>
  );
}

function CopyView({
  drafts,
  selectedDraft,
  setSelectedDraftId,
  selectedTopic,
  campaign,
  creatives,
  feedback,
  setFeedback,
  onGenerateCopy,
  onReviseCopy,
  onReview,
  onOpenCreatives,
  loading,
}: {
  drafts: CopyDraft[];
  selectedDraft: CopyDraft | null;
  setSelectedDraftId: (id: string) => void;
  selectedTopic: Topic | null;
  campaign: Campaign | null;
  creatives: CreativeAsset[];
  feedback: string;
  setFeedback: (value: string) => void;
  onGenerateCopy: () => void;
  onReviseCopy: () => void;
  onReview: (
    entityType: "topic" | "copy_draft" | "creative_asset" | "video_asset",
    entityId: string,
    decision: "approved" | "rejected" | "needs_revision",
  ) => void;
  onOpenCreatives: () => void;
  loading: string | null;
}) {
  const [selectedPreviewCreativeId, setSelectedPreviewCreativeId] = useState<string | null>(null);
  const approved = selectedDraft?.status === "approved";
  const targetLanguage = selectedDraft ? draftTargetLanguageLabel(selectedDraft) : "-";
  const landingUrl = draftLandingUrl(selectedDraft, campaign);
  const previewCreatives = useMemo(
    () => copyPreviewCreatives(creatives, selectedDraft),
    [creatives, selectedDraft],
  );
  const selectedPreviewCreative =
    previewCreatives.find((asset) => asset.id === selectedPreviewCreativeId) ??
    previewCreatives[0] ??
    null;
  const selectedPreviewAspectClass = mediaPreviewAspectClass(selectedPreviewCreative?.size);
  const feedbackTags = ["更短", "更本地化", "少用符号", "突出优惠", "更合规"];
  const appendFeedback = (value: string) => {
    setFeedback(feedback.trim() ? `${feedback.trim()}，${value}` : value);
  };

  useEffect(() => {
    if (!previewCreatives.length) {
      if (selectedPreviewCreativeId) setSelectedPreviewCreativeId(null);
      return;
    }
    if (!selectedPreviewCreativeId || !previewCreatives.some((asset) => asset.id === selectedPreviewCreativeId)) {
      setSelectedPreviewCreativeId(previewCreatives[0].id);
    }
  }, [previewCreatives, selectedPreviewCreativeId]);

  return (
    <section className="copy-layout two-column">
      <section className="panel copy-list-panel">
        <div className="panel-header">
          <div>
            <h2>文案版本</h2>
            <span className="panel-note">
              {drafts.length ? `${drafts.length} 个版本可审核` : "等待生成第一版文案"}
            </span>
          </div>
          <button className="primary-button" onClick={onGenerateCopy} disabled={!selectedTopic || loading === "copy"}>
            {loading === "copy" ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />}
            <span>生成文案</span>
          </button>
        </div>
        <DataList emptyText="暂无文案">
          {drafts.map((draft) => (
            <button
              className={`list-button copy-version-button ${selectedDraft?.id === draft.id ? "active" : ""}`}
              key={draft.id}
              onClick={() => setSelectedDraftId(draft.id)}
            >
              <div className="copy-version-head">
                <strong>{draft.headline || `文案 v${draft.version}`}</strong>
                <StatusPill status={draft.status} />
              </div>
              <span>v{draft.version} / {draftTargetLanguageLabel(draft)} / {formatDate(draft.created_at)}</span>
              <em>{copySnippet(draft)}</em>
            </button>
          ))}
        </DataList>
      </section>

      <section className="copy-workbench">
        {selectedDraft ? (
          <div className="copy-main-grid">
            <section className="panel copy-editor-panel">
              <div className="panel-header">
                <div>
                  <h2>文案审核</h2>
                  <span className="panel-note">按广告字段检查内容，确认后进入图片生成</span>
                </div>
                <StatusPill status={selectedDraft.status} />
              </div>

              <div className="copy-current">
                <div className="copy-field-grid">
                  <div>
                    <span>语言</span>
                    <strong>{targetLanguage}</strong>
                  </div>
                  <div>
                    <span>CTA</span>
                    <strong>{selectedDraft.cta || "Learn More"}</strong>
                  </div>
                  <div>
                    <span>选题</span>
                    <strong>{selectedTopic?.title || "未关联选题"}</strong>
                  </div>
                </div>

                <section className="copy-body-card">
                  <div className="copy-body-head">
                    <span>Primary Text</span>
                    <strong>{copyLengthLabel(selectedDraft.primary_text || selectedDraft.body)}</strong>
                  </div>
                  <pre className="copy-body-main">{selectedDraft.primary_text || selectedDraft.body}</pre>
                  {selectedDraft.body !== selectedDraft.primary_text && (
                    <details className="copy-source-details">
                      <summary>查看完整生成原文</summary>
                      <pre className="copy-source-text">{selectedDraft.body}</pre>
                    </details>
                  )}
                </section>

                <div className="copy-field-grid">
                  <div>
                    <span>Headline</span>
                    <strong>{selectedDraft.headline || "-"}</strong>
                  </div>
                  <div>
                    <span>Description</span>
                    <strong>{selectedDraft.description || "-"}</strong>
                  </div>
                  <div>
                    <span>落地页</span>
                    <strong>{landingUrl || "-"}</strong>
                  </div>
                </div>

                <section className="copy-feedback-card">
                  <div className="copy-section-title">
                    <strong>不满意？写修改意见再生成</strong>
                    <span>会基于当前版本重写，并保留新旧版本供比较</span>
                  </div>
                  <div className="copy-feedback-tags">
                    {feedbackTags.map((tag) => (
                      <button className="copy-feedback-tag" key={tag} onClick={() => appendFeedback(tag)}>
                        {tag}
                      </button>
                    ))}
                  </div>
                  <textarea
                    className="feedback-input copy-feedback-input"
                    value={feedback}
                    onChange={(event) => setFeedback(event.target.value)}
                    placeholder="例如：文案更短一些，少用符号，更像印度本地用户会看到的广告。"
                  />
                  <div className="copy-actions">
                    {!approved ? (
                      <button
                        className="primary-button"
                        onClick={() => onReview("copy_draft", selectedDraft.id, "approved")}
                      >
                        <Check size={16} />
                        <span>通过文案</span>
                      </button>
                    ) : (
                      <div className="review-complete copy-review-complete">
                        <Check size={16} />
                        <span>文案已通过</span>
                      </div>
                    )}
                    <button
                      className="secondary-button"
                      onClick={onReviseCopy}
                      disabled={!feedback.trim() || loading === "revise-copy"}
                    >
                      {loading === "revise-copy" ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />}
                      <span>按意见重写</span>
                    </button>
                  </div>
                </section>

                {approved && (
                  <div className="next-step">
                    <strong>文案已通过，可以进入图片生成</strong>
                    <button className="primary-button" onClick={onOpenCreatives}>
                      <Image size={16} />
                      <span>进入图片生成</span>
                    </button>
                  </div>
                )}
              </div>
            </section>

            <section className="panel copy-preview-panel">
              <div className="panel-header">
                <div>
                  <h2>广告预览</h2>
                  <span className="panel-note">模拟 Facebook 信息流展示</span>
                </div>
              </div>
              <div className="facebook-preview-shell">
                {previewCreatives.length > 0 && (
                  <div className="preview-asset-picker">
                    <div className="preview-asset-picker-head">
                      <span>预览图片</span>
                      <strong>{selectedPreviewCreative ? imagePromptTitle(selectedPreviewCreative.prompt) : "-"}</strong>
                    </div>
                    <div className="preview-asset-options">
                      {previewCreatives.map((asset, index) => (
                        <button
                          className={`preview-asset-option ${selectedPreviewCreative?.id === asset.id ? "active" : ""}`}
                          key={asset.id}
                          onClick={() => setSelectedPreviewCreativeId(asset.id)}
                          title={asset.alt_text || imagePromptTitle(asset.prompt)}
                          type="button"
                        >
                          <img src={displayAssetUrl(asset.url)} alt={asset.alt_text || `预览图片 ${index + 1}`} />
                          <span>{asset.status === "approved" ? "已通过" : `图 ${index + 1}`}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                <div className="facebook-preview-card">
                  <div className="facebook-preview-head">
                    <div className="facebook-page-avatar">AD</div>
                    <div>
                      <strong>{campaign?.name || "Campaign"}</strong>
                      <span>Sponsored</span>
                    </div>
                  </div>
                  <pre className="facebook-preview-text">{selectedDraft.primary_text || selectedDraft.body}</pre>
                  {selectedPreviewCreative?.url ? (
                    <img
                      className={`facebook-preview-image ${selectedPreviewAspectClass}`}
                      src={displayAssetUrl(selectedPreviewCreative.url)}
                      alt={selectedPreviewCreative.alt_text || "广告预览图片"}
                    />
                  ) : (
                    <div className={`facebook-preview-empty ${selectedPreviewAspectClass}`}>
                      <Image size={28} />
                      <span>图片生成后显示素材预览</span>
                    </div>
                  )}
                  <div className="facebook-preview-footer">
                    <div>
                      <span>{domainFromUrl(landingUrl) || "landing page"}</span>
                      <strong>{selectedDraft.headline || selectedTopic?.title || "Ad headline"}</strong>
                      <p>{selectedDraft.description || "Ad description"}</p>
                    </div>
                    <button>{selectedDraft.cta || "Learn More"}</button>
                  </div>
                </div>
              </div>

              <section className="context-section copy-context-section">
                <h3>投放上下文</h3>
                <KeyValueTable
                  data={{
                    项目: campaign?.name,
                    语言: targetLanguage,
                    落地页: landingUrl,
                    选题: selectedTopic?.title,
                    版本: `v${selectedDraft.version}`,
                  }}
                />
              </section>
            </section>
          </div>
        ) : (
          <section className="panel wide">
            <EmptyState text="请选择或生成一条文案" />
          </section>
        )}
      </section>
    </section>
  );
}

function CreativesView({
  creatives,
  creativeGenerationSlots,
  selectedCreativeIds,
  setSelectedCreativeIds,
  rewriteFeedbacks,
  setRewriteFeedback,
  onGenerate,
  onRetrySlot,
  onRegenerate,
  onReview,
  onCreateVideo,
  loading,
}: {
  creatives: CreativeAsset[];
  creativeGenerationSlots: CreativeGenerationSlot[];
  selectedCreativeIds: string[];
  setSelectedCreativeIds: (ids: string[]) => void;
  rewriteFeedbacks: Record<string, string>;
  setRewriteFeedback: (assetId: string, value: string) => void;
  onGenerate: () => void;
  onRetrySlot: (index: number) => void;
  onRegenerate: (asset: CreativeAsset) => void;
  onReview: (
    entityType: "topic" | "copy_draft" | "creative_asset" | "video_asset",
    entityId: string,
    decision: "approved" | "rejected" | "needs_revision",
  ) => void;
  onCreateVideo: () => void;
  loading: string | null;
}) {
  const visibleSlots = buildCreativeSlots(creatives, creativeGenerationSlots);
  const visibleAssetIds = new Set(
    visibleSlots.map((slot) => slot.asset?.id).filter((id): id is string => Boolean(id)),
  );
  const historyCreatives = creatives.filter((asset) => !visibleAssetIds.has(asset.id));
  const completedCount = visibleSlots.filter((slot) => slot.status === "done" && slot.asset).length;
  const hasSlotErrors = visibleSlots.some((slot) => slot.status === "error");
  const isGenerating =
    loading === "creatives" || creativeGenerationSlots.some((slot) => slot.status === "loading");
  const panelNote = visibleSlots.length
    ? isGenerating
      ? `${completedCount}/${CREATIVE_GENERATION_LIMIT} 张已生成`
      : `${completedCount} 张当前候选`
    : "还没有生成图片";

  return (
    <section className="view-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>图片审核</h2>
            <span className="panel-note">{panelNote}</span>
          </div>
          <div className="button-row">
            <button className="secondary-button" onClick={onGenerate} disabled={isGenerating}>
              {isGenerating ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />}
              <span>生成 3 张图片</span>
            </button>
            <button className="primary-button" onClick={onCreateVideo} disabled={!creatives.some((item) => item.status === "approved")}>
              <Film size={16} />
              <span>进入视频</span>
            </button>
          </div>
        </div>
        {visibleSlots.length > 0 && (
          <div className={`creative-progress ${completedCount === CREATIVE_GENERATION_LIMIT ? "done" : hasSlotErrors ? "failed" : ""}`}>
            <div className="creative-progress-head">
              <div className="creative-progress-icon">
                {isGenerating ? <Loader2 size={18} className="spin" /> : <Image size={18} />}
              </div>
              <div>
                <strong>{isGenerating ? "正在逐张生成图片" : "当前图片候选"}</strong>
                <span>完成的图片可立即审核、选择或继续改写。</span>
              </div>
              <StatusPill status={isGenerating ? "generating" : "generated"} />
            </div>
            <div className="creative-progress-steps">
              {visibleSlots.map((slot) => (
                <div className={`creative-progress-step ${slot.status}`} key={`creative-step-${slot.index}`}>
                  <span>{slot.index}</span>
                  <strong>
                    {slot.status === "done"
                      ? "已完成"
                      : slot.status === "error"
                        ? "需重试"
                        : "生成中"}
                  </strong>
                </div>
              ))}
            </div>
          </div>
        )}

        {visibleSlots.length ? (
          <div className="asset-grid creative-slot-grid">
            {visibleSlots.map((slot) => (
              <CreativeSlotCard
                key={`creative-slot-${slot.index}-${slot.asset?.id ?? slot.status}`}
                slot={slot}
                selectedCreativeIds={selectedCreativeIds}
                setSelectedCreativeIds={setSelectedCreativeIds}
                rewriteFeedbacks={rewriteFeedbacks}
                setRewriteFeedback={setRewriteFeedback}
                onRetrySlot={onRetrySlot}
                onRegenerate={onRegenerate}
                onReview={onReview}
                loading={loading}
              />
            ))}
          </div>
        ) : (
          <EmptyState text="暂无图片，请先生成" />
        )}

        {historyCreatives.length > 0 && (
          <details className="creative-history-section">
            <summary>
              <div>
                <strong>历史版本</strong>
                <span>保留旧图片，方便回看和对比。</span>
              </div>
              <span>{historyCreatives.length} 张</span>
            </summary>
            <div className="asset-grid history-grid">
              {historyCreatives.map((asset) => (
                <CreativeAssetMiniCard
                  key={asset.id}
                  asset={asset}
                  selectedCreativeIds={selectedCreativeIds}
                  setSelectedCreativeIds={setSelectedCreativeIds}
                  onReview={onReview}
                />
              ))}
            </div>
          </details>
        )}
      </section>
    </section>
  );
}

function CreativeSlotCard({
  slot,
  selectedCreativeIds,
  setSelectedCreativeIds,
  rewriteFeedbacks,
  setRewriteFeedback,
  onRetrySlot,
  onRegenerate,
  onReview,
  loading,
}: {
  slot: CreativeGenerationSlot;
  selectedCreativeIds: string[];
  setSelectedCreativeIds: (ids: string[]) => void;
  rewriteFeedbacks: Record<string, string>;
  setRewriteFeedback: (assetId: string, value: string) => void;
  onRetrySlot: (index: number) => void;
  onRegenerate: (asset: CreativeAsset) => void;
  onReview: (
    entityType: "topic" | "copy_draft" | "creative_asset" | "video_asset",
    entityId: string,
    decision: "approved" | "rejected" | "needs_revision",
  ) => void;
  loading: string | null;
}) {
  const asset = slot.asset;
  const isLoading = slot.status === "loading";
  const isRetrying = loading === `creative-retry-${slot.index}`;
  const isRegenerating = asset ? loading === `creative-regenerate-${asset.id}` : false;

  if (isLoading && !asset) {
    return (
      <article className="asset-card creative-skeleton-card" aria-live="polite">
        <span className="creative-skeleton-label">候选 {slot.index}</span>
        <div className="creative-skeleton-image">
          <Loader2 size={24} className="spin" />
        </div>
        <div className="creative-skeleton-lines">
          <span />
          <span />
          <span />
        </div>
      </article>
    );
  }

  if (!asset) {
    return (
      <article className="asset-card creative-slot-error">
        <div className="image-placeholder">
          <Image size={28} />
          <span>{slot.message || "图片生成失败"}</span>
        </div>
        <div className="asset-card-body">
          <strong>候选 {slot.index} 生成失败</strong>
          <button className="secondary-button" onClick={() => onRetrySlot(slot.index)} disabled={Boolean(loading)}>
            {isRetrying ? <Loader2 size={16} className="spin" /> : <RefreshCw size={16} />}
            <span>重试此候选</span>
          </button>
        </div>
      </article>
    );
  }

  const selected = selectedCreativeIds.includes(asset.id);
  const feedback = rewriteFeedbacks[asset.id] ?? "";

  return (
    <article className={`asset-card creative-slot-card ${selected ? "selected" : ""} ${slot.status === "error" ? "error" : ""}`}>
      <div className={isLoading || isRegenerating ? "creative-image-busy" : ""}>
        <ImagePreview asset={asset} />
        {(isLoading || isRegenerating) && (
          <div className="creative-image-overlay">
            <Loader2 size={22} className="spin" />
            <span>正在生成新版本</span>
          </div>
        )}
      </div>
      <div className="asset-card-body">
        <div className="asset-card-head">
          <strong>{asset.alt_text || imagePromptTitle(asset.prompt)}</strong>
          <StatusPill status={asset.status} />
        </div>
        <div className="creative-version-row">
          <span>候选 {creativeImageIndex(asset, slot.index)}</span>
          <span>版本 {asset.version}</span>
        </div>
        {slot.status === "error" && <p className="creative-error-text">{slot.message}</p>}
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={selected}
            onChange={(event) => {
              setSelectedCreativeIds(
                event.target.checked
                  ? [...selectedCreativeIds.filter((id) => id !== asset.id), asset.id]
                  : selectedCreativeIds.filter((id) => id !== asset.id),
              );
            }}
          />
          <span>用于视频</span>
        </label>
        <div className="creative-rewrite-box">
          <label htmlFor={`creative-feedback-${asset.id}`}>改写要求</label>
          <textarea
            id={`creative-feedback-${asset.id}`}
            value={feedback}
            onChange={(event) => setRewriteFeedback(asset.id, event.target.value)}
            placeholder="例如：主体更大，减少文字，背景换成家庭客厅，不要蓝色调。"
            disabled={Boolean(loading)}
          />
          <button
            className="secondary-button"
            onClick={() => onRegenerate(asset)}
            disabled={!feedback.trim() || Boolean(loading)}
          >
            {isRegenerating ? <Loader2 size={16} className="spin" /> : <RefreshCw size={16} />}
            <span>按意见重生</span>
          </button>
        </div>
        {asset.status === "approved" ? (
          <div className="review-complete">
            <Check size={16} />
            <span>图片已通过</span>
          </div>
        ) : (
          <div className="button-row">
            <button className="secondary-button" onClick={() => onReview("creative_asset", asset.id, "approved")}>
              <Check size={16} />
              <span>通过</span>
            </button>
            <button className="secondary-button danger" onClick={() => onReview("creative_asset", asset.id, "rejected")}>
              <X size={16} />
              <span>拒绝</span>
            </button>
          </div>
        )}
        <details className="asset-details">
          <summary>提示词</summary>
          <div className="asset-details-body">
            <pre className="asset-prompt">{asset.prompt}</pre>
          </div>
        </details>
      </div>
    </article>
  );
}

function CreativeAssetMiniCard({
  asset,
  selectedCreativeIds,
  setSelectedCreativeIds,
  onReview,
}: {
  asset: CreativeAsset;
  selectedCreativeIds: string[];
  setSelectedCreativeIds: (ids: string[]) => void;
  onReview: (
    entityType: "topic" | "copy_draft" | "creative_asset" | "video_asset",
    entityId: string,
    decision: "approved" | "rejected" | "needs_revision",
  ) => void;
}) {
  const selected = selectedCreativeIds.includes(asset.id);
  return (
    <article className={`asset-card history-asset-card ${selected ? "selected" : ""}`}>
      <ImagePreview asset={asset} />
      <div className="asset-card-body">
        <div className="asset-card-head">
          <strong>{asset.alt_text || imagePromptTitle(asset.prompt)}</strong>
          <StatusPill status={asset.status} />
        </div>
        <div className="creative-version-row">
          <span>候选 {creativeImageIndex(asset, 1)}</span>
          <span>版本 {asset.version}</span>
        </div>
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={selected}
            onChange={(event) => {
              setSelectedCreativeIds(
                event.target.checked
                  ? [...selectedCreativeIds.filter((id) => id !== asset.id), asset.id]
                  : selectedCreativeIds.filter((id) => id !== asset.id),
              );
            }}
          />
          <span>用于视频</span>
        </label>
        {asset.status === "approved" ? (
          <div className="review-complete">
            <Check size={16} />
            <span>图片已通过</span>
          </div>
        ) : (
          <div className="button-row">
            <button className="secondary-button" onClick={() => onReview("creative_asset", asset.id, "approved")}>
              <Check size={16} />
              <span>通过</span>
            </button>
            <button className="secondary-button danger" onClick={() => onReview("creative_asset", asset.id, "rejected")}>
              <X size={16} />
              <span>拒绝</span>
            </button>
          </div>
        )}
      </div>
    </article>
  );
}

function VideosView({
  campaign,
  draft,
  videos,
  videoPollWarnings,
  approvedCreatives,
  selectedCreativeIds,
  setSelectedCreativeIds,
  selectedVideoId,
  setSelectedVideoId,
  aspectRatio,
  setAspectRatio,
  durationSeconds,
  setDurationSeconds,
  instructions,
  setInstructions,
  storyboardText,
  setStoryboardText,
  storyboardFeedback,
  setStoryboardFeedback,
  onGenerateStoryboard,
  onRewriteStoryboard,
  onCreateVideo,
  onStartGeneration,
  onReview,
  loading,
}: {
  campaign: Campaign | null;
  draft: CopyDraft | null;
  videos: VideoAsset[];
  videoPollWarnings: Record<string, VideoPollWarning>;
  approvedCreatives: CreativeAsset[];
  selectedCreativeIds: string[];
  setSelectedCreativeIds: (ids: string[]) => void;
  selectedVideoId: string | null;
  setSelectedVideoId: (id: string) => void;
  aspectRatio: string;
  setAspectRatio: (value: string) => void;
  durationSeconds: number;
  setDurationSeconds: (value: number) => void;
  instructions: string;
  setInstructions: (value: string) => void;
  storyboardText: string;
  setStoryboardText: (value: string) => void;
  storyboardFeedback: string;
  setStoryboardFeedback: (value: string) => void;
  onGenerateStoryboard: () => void;
  onRewriteStoryboard: () => void;
  onCreateVideo: () => void;
  onStartGeneration: (videoId: string) => void;
  onReview: (
    entityType: "topic" | "copy_draft" | "creative_asset" | "video_asset",
    entityId: string,
    decision: "approved" | "rejected" | "needs_revision",
  ) => void;
  loading: string | null;
}) {
  const selectedVideo = videos.find((video) => video.id === selectedVideoId) ?? videos[0] ?? null;
  const selectedVideoPollWarning = selectedVideo ? videoPollWarnings[selectedVideo.id] : null;
  const approvedVideoCount = videos.filter((video) => video.status === "approved").length;
  const workingVideoCount = videos.filter((video) => video.status !== "approved").length;
  const storyboardStreaming = loading === "video-storyboard" || loading === "video-storyboard-rewrite";
  const landingUrl = draftLandingUrl(draft, campaign);
  const previewText =
    draft?.primary_text ||
    draft?.body ||
    storyboardText.trim() ||
    "确认脚本并生成视频后，这里会展示成片在广告里的样子。";
  const previewHeadline = draft?.headline || campaign?.product_name || campaign?.name || "Ad headline";
  const previewDescription = draft?.description || campaign?.audience_description || "Ad description";
  const previewCta = draft?.cta || "Learn More";
  const previewAspectClass = mediaPreviewAspectClass(selectedVideo?.aspect_ratio || aspectRatio);
  const previewVideoGenerating = selectedVideo
    ? loading === `video-generate-${selectedVideo.id}` || isVideoGeneratingStatus(selectedVideo.status)
    : loading === "video";
  const previewSourceCreatives = approvedCreatives
    .filter((asset) => selectedCreativeIds.includes(asset.id) && Boolean(asset.url))
    .slice(0, VIDEO_MAX_REFERENCE_IMAGES);
  const previewEmptyTitle = selectedVideo
    ? selectedVideo.status === "failed"
      ? "视频生成失败"
      : previewVideoGenerating
        ? "正在生成成片"
        : "等待成片生成"
    : "暂无视频任务";
  const previewEmptyHint = selectedVideo
    ? selectedVideo.status !== "failed" && selectedVideoPollWarning
      ? selectedVideoPollWarning.message
      : selectedVideo.status === "failed"
      ? selectedVideo.error_message || "可以在右侧任务里重试生成。"
      : previewVideoGenerating
        ? `已等待 ${formatDuration(videoWaitSeconds(selectedVideo))}，完成后会自动切换为视频预览。`
        : "确认脚本并生成视频后，会在这里看到完整广告预览。"
    : "先确认脚本并生成视频，预览会跟随当前任务更新。";

  return (
    <section className="video-workbench">
      <div className="video-overview-strip">
        <div>
          <span className="section-eyebrow">VIDEO REVIEW</span>
          <strong>{selectedVideo ? `当前任务 ${shortId(selectedVideo.id)}` : "等待创建视频任务"}</strong>
        </div>
        <div className="video-overview-metrics" aria-label="视频生产概览">
          <span>{approvedCreatives.length} 张可用图片</span>
          <span>{selectedCreativeIds.length}/{VIDEO_MAX_REFERENCE_IMAGES} 张已选</span>
          <span>{workingVideoCount} 个待处理</span>
          <span>{approvedVideoCount} 个已通过</span>
        </div>
      </div>

      <section className="video-layout">
        <section className="panel video-builder-panel">
          <div className="panel-header video-panel-header">
            <div>
              <span className="section-eyebrow">SETUP</span>
              <h2>视频配置</h2>
            </div>
            <span className="panel-note">最多选择 {VIDEO_MAX_REFERENCE_IMAGES} 张参考图</span>
          </div>
          <div className="video-config">
            <div className="config-group">
              <label>画面比例</label>
              <div className="segmented-control">
                {["9:16", "1:1", "16:9"].map((value) => (
                  <button
                    className={aspectRatio === value ? "active" : ""}
                    key={value}
                    type="button"
                    disabled={storyboardStreaming}
                    onClick={() => setAspectRatio(value)}
                  >
                    {value}
                  </button>
                ))}
              </div>
            </div>
            <div className="config-group">
              <label htmlFor="video-duration">视频时长</label>
              <div className="duration-control">
                <input
                  id="video-duration"
                  className="input"
                  type="number"
                  min={4}
                  max={12}
                  value={durationSeconds}
                  disabled={storyboardStreaming}
                  onChange={(event) => setDurationSeconds(Number(event.target.value))}
                />
                <span>秒</span>
              </div>
            </div>
            <div className="config-group full">
              <div className="config-label-row">
                <label>参考图片</label>
                <span className="field-hint">已审核通过的图片</span>
              </div>
              {approvedCreatives.length ? (
                <div className="source-asset-list video-source-list">
                  {approvedCreatives.map((asset) => {
                    const active = selectedCreativeIds.includes(asset.id);
                    return (
                      <button
                        className={`list-check ${active ? "active" : ""}`}
                        key={asset.id}
                        type="button"
                        disabled={storyboardStreaming}
                        onClick={() => {
                          setSelectedCreativeIds(
                            active
                              ? selectedCreativeIds.filter((id) => id !== asset.id)
                              : [...selectedCreativeIds, asset.id].slice(0, VIDEO_MAX_REFERENCE_IMAGES),
                          );
                        }}
                      >
                        <Image size={16} />
                        <span>{shortId(asset.id)}</span>
                        <StatusPill status={asset.status} />
                      </button>
                    );
                  })}
                </div>
              ) : (
                <div className="video-inline-empty">暂无审核通过的图片</div>
              )}
            </div>
            <div className="config-group full">
              <label htmlFor="video-instructions">风格与镜头要求</label>
              <textarea
                id="video-instructions"
                className="video-instructions"
                value={instructions}
                onChange={(event) => setInstructions(event.target.value)}
                disabled={storyboardStreaming}
                placeholder="视频风格或镜头要求"
              />
            </div>
            <div className="video-action-bar">
              <button
                className="secondary-button"
                type="button"
                onClick={onGenerateStoryboard}
                disabled={loading === "video-storyboard" || loading === "video-storyboard-rewrite"}
              >
                {loading === "video-storyboard" ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />}
                <span>生成分镜脚本</span>
              </button>
              <button
                className="primary-button"
                type="button"
                onClick={onCreateVideo}
                disabled={
                  !storyboardText.trim() ||
                  loading === "video" ||
                  loading === "video-storyboard" ||
                  loading === "video-storyboard-rewrite"
                }
              >
                <Film size={16} />
                <span>确认脚本并生成视频</span>
              </button>
            </div>
          </div>
          <details className="storyboard-editor-details video-storyboard-editor" open>
            <summary>
              <span>视频脚本</span>
              <small>
                {loading === "video-storyboard"
                  ? "生成中"
                  : loading === "video-storyboard-rewrite"
                    ? "改写中"
                    : storyboardText.trim()
                      ? "可编辑"
                      : "未生成"}
              </small>
            </summary>
            <textarea
              className="storyboard-input"
              value={storyboardText}
              onChange={(event) => setStoryboardText(event.target.value)}
              disabled={storyboardStreaming}
              placeholder="暂无脚本内容"
            />
            <div className="storyboard-rewrite-box">
              <label htmlFor="video-storyboard-feedback">脚本修改意见</label>
              <textarea
                id="video-storyboard-feedback"
                className="storyboard-feedback-input"
                value={storyboardFeedback}
                onChange={(event) => setStoryboardFeedback(event.target.value)}
                disabled={storyboardStreaming}
                placeholder="例如：第一幕更强钩子，字幕更短，不要旁白，第三幕改成产品使用场景"
              />
              <div className="storyboard-rewrite-actions">
                <button
                  className="secondary-button"
                  type="button"
                  onClick={onRewriteStoryboard}
                  disabled={
                    !storyboardText.trim() ||
                    !storyboardFeedback.trim() ||
                    loading === "video-storyboard" ||
                    loading === "video-storyboard-rewrite"
                  }
                >
                  {loading === "video-storyboard-rewrite" ? (
                    <Loader2 size={16} className="spin" />
                  ) : (
                    <Sparkles size={16} />
                  )}
                  <span>按意见改写脚本</span>
                </button>
              </div>
            </div>
          </details>
        </section>

        <section className="video-side-stack">
          <section className="panel video-ad-preview-panel">
            <div className="panel-header video-panel-header">
              <div>
                <span className="section-eyebrow">AD PREVIEW</span>
                <h2>广告预览</h2>
              </div>
              <span className="panel-note">
                {selectedVideo ? `当前任务 ${shortId(selectedVideo.id)}` : "等待视频任务"}
              </span>
            </div>
            <div className="facebook-preview-shell video-ad-preview-shell">
              <div className="facebook-preview-card video-ad-preview-card">
                <div className="facebook-preview-head">
                  <div className="facebook-page-avatar">AD</div>
                  <div>
                    <strong>{campaign?.name || "Campaign"}</strong>
                    <span>Sponsored</span>
                  </div>
                </div>
                <pre className="facebook-preview-text">{previewText}</pre>
                {selectedVideo?.url ? (
                  <div className={`video-ad-preview-media ${previewAspectClass}`}>
                    <video controls muted playsInline src={displayAssetUrl(selectedVideo.url)} />
                  </div>
                ) : (
                  <div
                    className={`facebook-preview-empty video-ad-preview-empty ${previewAspectClass} ${
                      previewVideoGenerating ? "generating" : selectedVideo?.status === "failed" ? "failed" : ""
                    }`}
                  >
                    {previewVideoGenerating ? (
                      <Loader2 size={28} className="spin" />
                    ) : selectedVideo?.status === "failed" ? (
                      <X size={28} />
                    ) : (
                      <Film size={28} />
                    )}
                    <strong>{previewEmptyTitle}</strong>
                    <span>{previewEmptyHint}</span>
                    {previewSourceCreatives.length > 0 && (
                      <div className="video-ad-preview-sources" aria-label="视频参考图">
                        {previewSourceCreatives.map((asset, index) => (
                          <img
                            key={asset.id}
                            src={displayAssetUrl(asset.url)}
                            alt={asset.alt_text || `参考图 ${index + 1}`}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                )}
                <div className="facebook-preview-footer">
                  <div>
                    <span>{domainFromUrl(landingUrl) || "landing page"}</span>
                    <strong>{previewHeadline}</strong>
                    <p>{previewDescription}</p>
                  </div>
                  <button type="button">{previewCta}</button>
                </div>
              </div>
            </div>
          </section>

          <section className="panel wide video-review-panel">
          <div className="panel-header video-panel-header">
            <div>
              <span className="section-eyebrow">QUEUE</span>
              <h2>视频审核</h2>
            </div>
            <span className="panel-note">{videos.length ? `${videos.length} 个任务` : "暂无任务"}</span>
          </div>
          {videos.length ? (
            <div className="video-task-stack">
              {videos.map((video) => {
                const selected = selectedVideo?.id === video.id;
                const isStarting = loading === `video-generate-${video.id}`;
                const isGenerating = isStarting || isVideoGeneratingStatus(video.status);
                const canReview = video.status === "generated" && Boolean(video.url);
                const canRetryGeneration = ["requested", "failed", "needs_revision"].includes(video.status);
                const pollWarning = videoPollWarnings[video.id];
                return (
                  <article className={`video-card ${selected ? "selected" : ""}`} key={video.id}>
                    <div className="video-card-head">
                      <div>
                        <span>视频任务 {shortId(video.id)}</span>
                        <strong>{video.aspect_ratio} / {video.duration_seconds || "-"} 秒</strong>
                      </div>
                      <StatusPill status={video.status} />
                    </div>
                    {video.url ? (
                      <VideoPreview url={video.url} />
                    ) : (
                      <div className={`video-preview-placeholder ${isGenerating ? "generating" : video.status === "failed" ? "failed" : ""}`}>
                        {isGenerating ? <Loader2 size={22} className="spin" /> : video.status === "failed" ? <X size={22} /> : <Film size={22} />}
                        <strong>
                          {isGenerating
                            ? "正在生成成片"
                            : video.status === "failed"
                              ? "生成失败"
                              : video.status === "requested"
                                ? "任务待提交"
                                : "等待生成成片"}
                        </strong>
                        <span>
                          {isGenerating
                            ? `已等待 ${formatDuration(videoWaitSeconds(video))}，完成后会自动显示预览`
                            : video.provider_job_id
                              ? `任务号 ${video.provider_job_id}`
                              : video.status === "requested"
                                ? "自动提交未完成，可重试生成"
                                : "确认脚本后会自动提交生成"}
                        </span>
                      </div>
                    )}
                    <div className="video-task-summary">
                      <div>
                        <span>比例</span>
                        <strong>{video.aspect_ratio}</strong>
                      </div>
                      <div>
                        <span>时长</span>
                        <strong>{video.duration_seconds || "-"} 秒</strong>
                      </div>
                      <div>
                        <span>来源图片</span>
                        <strong>{video.source_asset_ids.length || "-"} 张</strong>
                      </div>
                    </div>
                    {video.error_message && <p className="video-error-text">{video.error_message}</p>}
                    {pollWarning && video.status !== "failed" && (
                      <p className="video-error-text">{pollWarning.message}</p>
                    )}
                    <div className="video-task-actions">
                      {selected ? (
                        <div className="video-selection-indicator">
                          <Check size={16} />
                          <span>当前预览</span>
                        </div>
                      ) : (
                        <button className="secondary-button" type="button" onClick={() => setSelectedVideoId(video.id)}>
                          <Check size={16} />
                          <span>设为当前</span>
                        </button>
                      )}
                      {isGenerating && (
                        <button className="secondary-button" type="button" disabled>
                          <Loader2 size={16} className="spin" />
                          <span>生成中</span>
                        </button>
                      )}
                      {!isGenerating && canRetryGeneration && (
                        <button
                          className="secondary-button"
                          type="button"
                          onClick={() => onStartGeneration(video.id)}
                          disabled={isStarting}
                        >
                          {isStarting ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />}
                          <span>重试生成</span>
                        </button>
                      )}
                      {canReview && (
                        <>
                          <button
                            className="primary-button"
                            type="button"
                            onClick={() => onReview("video_asset", video.id, "approved")}
                          >
                            <Check size={16} />
                            <span>通过</span>
                          </button>
                          <button
                            className="secondary-button danger"
                            type="button"
                            onClick={() => onReview("video_asset", video.id, "rejected")}
                          >
                            <X size={16} />
                            <span>拒绝</span>
                          </button>
                        </>
                      )}
                      {video.status === "approved" && (
                        <div className="review-complete video-review-complete">
                          <Check size={16} />
                          <span>视频已通过</span>
                        </div>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>
          ) : (
            <div className="video-empty-state">
              <Film size={26} />
              <strong>暂无视频任务</strong>
              <span>等待创建</span>
            </div>
          )}
          </section>
        </section>
      </section>
    </section>
  );
}

function DeliveryConfirmDialog({
  open,
  extraction,
  form,
  loading,
  onChange,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  extraction: WorkOrderDeliveryExtraction | null;
  form: ReviewedDeliveryFields;
  loading: boolean;
  onChange: (key: keyof ReviewedDeliveryFields, value: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  if (!open || !extraction) return null;
  return (
    <div className="modal-backdrop">
      <section className="modal-card delivery-dialog">
        <div className="modal-head">
          <h2>确认投放参数</h2>
          <button className="icon-button" onClick={onCancel}>
            <X size={18} />
          </button>
        </div>
        <div className="delivery-grid">
          {(["landing_url", "event_name", "country", "age_min", "age_max", "gender", "audience_description_raw"] as Array<
            keyof ReviewedDeliveryFields
          >).map((key) => (
            <label key={key}>
              <span>{deliveryFieldLabel(key)}</span>
              {key === "event_name" ? (
                <select className="select" value={form[key]} onChange={(event) => onChange(key, event.target.value)}>
                  {DELIVERY_EVENT_OPTIONS.map((option) => (
                    <option value={option} key={option}>{option}</option>
                  ))}
                </select>
              ) : key === "country" ? (
                <CountryField value={form.country} onChange={(value) => onChange("country", value)} />
              ) : (
                <input className="input" value={form[key]} onChange={(event) => onChange(key, event.target.value)} />
              )}
              <small>{deliveryExtractionHint(extraction, key)}</small>
            </label>
          ))}
        </div>
        <div className="button-row modal-actions">
          <button className="secondary-button" onClick={onCancel}>取消</button>
          <button className="primary-button" onClick={onConfirm} disabled={loading}>
            {loading ? <Loader2 size={16} className="spin" /> : <Check size={16} />}
            <span>创建 AI 工单</span>
          </button>
        </div>
      </section>
    </div>
  );
}

function CountryField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const option = countryOptionForValue(value);
  const selectValue = option?.value ?? "__other__";
  const isOther = !option;

  return (
    <div className="country-field">
      <select
        className="select"
        value={selectValue}
        onChange={(event) => {
          const nextValue = event.target.value;
          onChange(nextValue === "__other__" ? "" : nextValue);
        }}
      >
        {DELIVERY_COUNTRY_OPTIONS.map((item) => (
          <option value={item.value} key={item.code}>
            {item.label}
          </option>
        ))}
        <option value="__other__">其他国家/地区</option>
      </select>
      {isOther && (
        <input
          className="input"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder="请输入国家或地区"
        />
      )}
    </div>
  );
}

function Metric({
  label,
  value,
  accent,
  hint,
  icon: Icon,
}: {
  label: string;
  value: number;
  accent: "blue" | "amber" | "violet" | "rose";
  hint: string;
  icon: typeof BarChart3;
}) {
  return (
    <article className={`metric ${accent}`}>
      <div className="metric-icon">
        <Icon size={20} />
      </div>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
        <em>{hint}</em>
      </div>
    </article>
  );
}

function WorkflowProgress({ summary }: { summary: WorkflowSummary }) {
  const steps = workflowSummarySteps(summary);
  return (
    <div className="workflow-steps">
      {steps.map((step, index) => (
        <div className={`workflow-step ${step.status}`} key={step.key}>
          <div className="workflow-step-index">{index + 1}</div>
          <div className="workflow-step-body">
            <div className="workflow-step-title">
              <strong>{step.title}</strong>
              <span>{workflowStepStatusLabel(step.status)}</span>
            </div>
            <p>{step.label}</p>
          </div>
        </div>
      ))}
    </div>
  );
}

function workflowSummarySteps(summary: WorkflowSummary): WorkflowSummary["fields"][] {
  return [summary.fields, summary.topic, summary.copy, summary.image, summary.video, summary.final];
}

function currentWorkflowStep(summary: WorkflowSummary): WorkflowSummary["fields"] {
  const steps = workflowSummarySteps(summary);
  return steps.find((step) => step.status === "active") ?? steps.find((step) => step.key === "final") ?? steps[0];
}

function workflowProgressPercent(summary: WorkflowSummary): number {
  const steps = workflowSummarySteps(summary);
  if (!steps.length) return 0;
  return Math.round((steps.filter((step) => step.done).length / steps.length) * 100);
}

function DataList({ children, emptyText }: { children: React.ReactNode; emptyText: string }) {
  const items = Array.isArray(children) ? children.filter(Boolean) : children;
  const empty = Array.isArray(items) ? items.length === 0 : !items;
  return <div className="data-list">{empty ? <EmptyState text={emptyText} /> : items}</div>;
}

function EmptyState({ text }: { text: string }) {
  return <div className="empty-state">{text}</div>;
}

function StatusPill({ status }: { status: string }) {
  return <span className={`status ${status}`}>{statusLabel(status)}</span>;
}

function KeyValueTable({
  data,
  className = "",
  labelForKey,
}: {
  data: Record<string, unknown>;
  className?: string;
  labelForKey?: (key: string) => string;
}) {
  return (
    <div className={`kv-table ${className}`.trim()}>
      {Object.entries(data).map(([key, value]) => (
        <div className="kv-row" key={key}>
          <span title={key}>{labelForKey ? labelForKey(key) : key}</span>
          <strong>{value == null || value === "" ? "-" : stringifyValue(value)}</strong>
        </div>
      ))}
    </div>
  );
}

function JsonBlock({ value }: { value: unknown }) {
  return <pre className="json-block">{JSON.stringify(value ?? {}, null, 2)}</pre>;
}

function PerformanceAnalysisView({
  analyses,
  selectedAnalysis,
  setSelectedAnalysisId,
  onCreateAnalysis,
  onDeleteAnalysis,
  onRefresh,
  loading,
}: {
  analyses: AdPerformanceAnalysis[];
  selectedAnalysis: AdPerformanceAnalysis | null;
  setSelectedAnalysisId: (id: string) => void;
  onCreateAnalysis: (payload: ManualAdPerformancePayload) => Promise<AdPerformanceAnalysis | null>;
  onDeleteAnalysis: (analysisId: string) => void;
  onRefresh: () => void;
  loading: string | null;
}) {
  const result = selectedAnalysis?.analysis_result;
  const aiAnalysis = result?.ai_analysis ?? null;
  const visualAnalysis = aiAnalysis?.visual_analysis ?? null;
  const dataCompleteness = result?.data_completeness ?? null;
  const optimizationWorkOrder = result?.optimization_work_order ?? null;
  const [streamingAnalysisId, setStreamingAnalysisId] = useState<string | null>(null);
  const [performanceStreamAnalysisId, setPerformanceStreamAnalysisId] = useState<string | null>(null);
  const [performanceStreamText, setPerformanceStreamText] = useState("");
  const [performanceStreamError, setPerformanceStreamError] = useState<string | null>(null);
  const [manualJsonText, setManualJsonText] = useState(manualAdPerformanceJsonExample);
  const [manualJsonError, setManualJsonError] = useState<string | null>(null);
  const isStreamingSelected = Boolean(
    selectedAnalysis && streamingAnalysisId === selectedAnalysis.id,
  );
  const hasStreamForSelected = Boolean(
    selectedAnalysis && performanceStreamAnalysisId === selectedAnalysis.id,
  );
  const isCreatingManualAnalysis = loading === "performance-create";

  function handleFormatManualJson() {
    try {
      const payload = parseManualAdPerformanceJson(manualJsonText);
      setManualJsonText(formatManualAdPerformanceJson(payload));
      setManualJsonError(null);
    } catch (caught) {
      setManualJsonError(
        caught instanceof Error ? caught.message : "JSON 格式不正确，请检查后重试。",
      );
    }
  }

  async function handleSubmitManualJson(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    let payload: ManualAdPerformancePayload;
    try {
      payload = parseManualAdPerformanceJson(manualJsonText);
      setManualJsonError(null);
    } catch (caught) {
      setManualJsonError(
        caught instanceof Error ? caught.message : "JSON 格式不正确，请检查后重试。",
      );
      return;
    }

    const analysis = await onCreateAnalysis(payload);
    if (analysis) {
      setManualJsonText(formatManualAdPerformanceJson(analysis.request_payload));
      setManualJsonError(null);
    }
  }

  async function handleStreamAiAnalysis() {
    if (!selectedAnalysis || isStreamingSelected) return;
    const analysisId = selectedAnalysis.id;
    setStreamingAnalysisId(analysisId);
    setPerformanceStreamAnalysisId(analysisId);
    setPerformanceStreamText("");
    setPerformanceStreamError(null);
    try {
      await api.streamAdPerformanceAnalysis(
        analysisId,
        (event: AdPerformanceAnalysisStreamEvent) => {
          if (event.type === "start") {
            setPerformanceStreamText("");
            return;
          }
          if (event.type === "delta") {
            setPerformanceStreamText((current) => current + event.text);
            return;
          }
          if (event.type === "error") {
            throw new Error(event.message);
          }
          if (event.type === "done") {
            onRefresh();
          }
        },
      );
    } catch (caught) {
      setPerformanceStreamError(apiErrorMessage(caught, "流式 AI 分析失败"));
    } finally {
      setStreamingAnalysisId(null);
    }
  }

  return (
    <section className="performance-layout two-column">
      <section className="panel performance-list-panel">
        <div className="panel-header">
          <div>
            <span className="section-eyebrow">PERFORMANCE</span>
            <h2>投放分析记录</h2>
            <span className="panel-note">{analyses.length ? `${analyses.length} 条分析` : "等待外部系统回传数据"}</span>
          </div>
          <button className="icon-button" onClick={onRefresh} title="刷新分析">
            {loading === "performance-refresh" ? <Loader2 size={18} className="spin" /> : <RefreshCw size={18} />}
          </button>
        </div>
        <form className="performance-manual-form" onSubmit={(event) => void handleSubmitManualJson(event)}>
          <div className="performance-manual-head">
            <div>
              <h3>手动创建分析</h3>
              <p>粘贴外部投放系统回传 JSON，立即生成一条 AI 分析记录。</p>
            </div>
            <div className="performance-manual-tools">
              <button
                type="button"
                className="icon-button"
                onClick={() => {
                  setManualJsonText(manualAdPerformanceJsonExample);
                  setManualJsonError(null);
                }}
                title="填入示例 JSON"
              >
                <FileText size={16} />
              </button>
              <button
                type="button"
                className="icon-button"
                onClick={handleFormatManualJson}
                title="格式化 JSON"
              >
                <Check size={16} />
              </button>
            </div>
          </div>
          <textarea
            className="performance-manual-textarea"
            value={manualJsonText}
            onChange={(event) => {
              setManualJsonText(event.target.value);
              if (manualJsonError) setManualJsonError(null);
            }}
            placeholder='{"creative":{"name":"new12"},"insight":{"clicks":"83"}}'
            spellCheck={false}
          />
          {manualJsonError && <p className="performance-manual-error">{manualJsonError}</p>}
          <button className="primary-button" type="submit" disabled={isCreatingManualAnalysis}>
            {isCreatingManualAnalysis ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />}
            {isCreatingManualAnalysis ? "创建中" : "创建分析"}
          </button>
        </form>
        <DataList emptyText="还没有收到广告效果数据">
          {analyses.map((analysis) => (
            <div
              className={`list-button performance-list-item ${selectedAnalysis?.id === analysis.id ? "active" : ""}`}
              key={analysis.id}
              onClick={() => setSelectedAnalysisId(analysis.id)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  setSelectedAnalysisId(analysis.id);
                }
              }}
              role="button"
              tabIndex={0}
            >
              <div className="performance-list-item-main">
                <strong>{performanceAnalysisTitle(analysis)}</strong>
                <span>{performanceAnalysisMeta(analysis)}</span>
                <span>{formatDate(analysis.created_at)}</span>
              </div>
              <button
                aria-label="删除投放分析记录"
                className="icon-button danger performance-delete-button"
                disabled={loading === `delete-performance-${analysis.id}`}
                onClick={(event) => {
                  event.stopPropagation();
                  onDeleteAnalysis(analysis.id);
                }}
                onKeyDown={(event) => event.stopPropagation()}
                title="删除投放分析记录"
              >
                {loading === `delete-performance-${analysis.id}` ? (
                  <Loader2 size={16} className="spin" />
                ) : (
                  <Trash2 size={16} />
                )}
              </button>
            </div>
          ))}
        </DataList>
      </section>

      <section className="panel wide performance-detail-panel">
        <div className="panel-header">
          <div>
            <span className="section-eyebrow">DIAGNOSIS</span>
            <h2>{selectedAnalysis ? performanceAnalysisTitle(selectedAnalysis) : "分析详情"}</h2>
            <span className="panel-note">
              {selectedAnalysis ? performanceAnalysisMeta(selectedAnalysis) : "选择一条分析查看原因和建议"}
            </span>
          </div>
          <div className="performance-detail-actions">
            {selectedAnalysis && (
              <button
                className="secondary-button"
                disabled={isStreamingSelected}
                onClick={() => void handleStreamAiAnalysis()}
                title="流式重新生成 AI 深度分析"
              >
                {isStreamingSelected ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />}
                {isStreamingSelected ? "分析中" : "流式分析"}
              </button>
            )}
            {result && (
              <span className={`status ${confidenceStatusClass(result.confidence)}`}>
                {confidenceLabel(result.confidence)}
              </span>
            )}
          </div>
        </div>

        {selectedAnalysis && result ? (
          <div className="performance-detail-body">
            {optimizationWorkOrder && <OptimizationWorkOrderCard data={optimizationWorkOrder} />}

            <div className="performance-support-stack">
              {(isStreamingSelected ||
                (hasStreamForSelected && (performanceStreamText || performanceStreamError))) && (
                <details className="performance-disclosure performance-ai-stream-disclosure">
                  <summary>
                    <span>AI 流式输出</span>
                    <em>{isStreamingSelected ? "正在生成" : performanceStreamError ? "生成失败" : "已完成"}</em>
                  </summary>
                  <div className="performance-disclosure-content">
                    {performanceStreamError ? (
                      <div className="performance-ai-fallback">{performanceStreamError}</div>
                    ) : (
                      <pre className="performance-stream-output">
                        {performanceStreamText || "等待模型开始输出..."}
                      </pre>
                    )}
                  </div>
                </details>
              )}

              {dataCompleteness && (
                <details className="performance-disclosure performance-completeness-disclosure">
                  <summary>
                    <span>数据完整度</span>
                    <em>
                      {completenessLabel(dataCompleteness.level)} · {dataCompleteness.score}/100
                    </em>
                  </summary>
                  <div className="performance-disclosure-content">
                    <DataCompletenessCard data={dataCompleteness} />
                  </div>
                </details>
              )}

              {aiAnalysis ? (
                <details className="performance-disclosure performance-ai-disclosure">
                  <summary>
                    <span>AI 深度分析</span>
                  </summary>
                  <div className="performance-disclosure-content">
                    {visualAnalysis && <AiVisualAnalysisCard data={visualAnalysis} />}
                    <div className="performance-ai-grid">
                      <AiInsightList title="核心原因" items={aiAnalysis.root_causes} />
                      <AiInsightList title="调整动作" items={aiAnalysis.recommended_actions} />
                      <AiInsightList title="测试方案" items={aiAnalysis.next_tests} />
                      <AiInsightList title="素材判断" items={aiAnalysis.creative_feedback} />
                      <AiInsightList title="人群判断" items={aiAnalysis.audience_feedback} />
                      <AiInsightList title="落地页判断" items={aiAnalysis.landing_page_feedback} />
                      <AiInsightList title="预算与投放" items={aiAnalysis.budget_delivery_feedback} />
                      <AiInsightList title="风险提醒" items={aiAnalysis.risk_notes} />
                    </div>
                  </div>
                </details>
              ) : result.llm_error ? (
                <details className="performance-disclosure performance-ai-disclosure">
                  <summary>
                    <span>AI 深度分析</span>
                    <em>生成失败</em>
                  </summary>
                  <div className="performance-disclosure-content">
                    <div className="performance-ai-fallback">
                      大模型分析暂时失败，请检查模型配置或稍后重试。错误：{result.llm_error}
                    </div>
                  </div>
                </details>
              ) : null}

              <details className="performance-disclosure performance-json-details">
                <summary>
                  <span>原始 JSON</span>
                  <em>请求数据</em>
                </summary>
                <div className="performance-disclosure-content">
                  <JsonBlock value={selectedAnalysis.request_payload} />
                </div>
              </details>
            </div>
          </div>
        ) : (
          <EmptyState text="选择一条分析记录查看详情" />
        )}
      </section>
    </section>
  );
}

function DataCompletenessCard({ data }: { data: AdPerformanceDataCompleteness }) {
  return (
    <section className={`performance-completeness-card ${data.level}`}>
      <div className="completeness-overview">
        <span>数据完整度</span>
        <strong>{completenessLabel(data.level)}</strong>
        <em>{data.score}/100</em>
        {data.notes.length > 0 && <p>{data.notes[0]}</p>}
      </div>
      <div className="completeness-grid">
        <CompletenessList title="已收到" items={data.available} tone="ok" />
        <CompletenessList title="缺少" items={data.missing} tone="missing" />
        <CompletenessList title="可以分析" items={data.can_analyze} tone="ok" />
        <CompletenessList title="暂不能分析" items={data.cannot_analyze} tone="missing" />
      </div>
      {data.notes.length > 1 && (
        <div className="completeness-notes">
          {data.notes.slice(1).map((note) => (
            <span key={note}>{note}</span>
          ))}
        </div>
      )}
    </section>
  );
}

function CompletenessList({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone: "ok" | "missing";
}) {
  return (
    <article className={`completeness-list ${tone}`}>
      <h4>{title}</h4>
      {items.length ? (
        <ul>
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : (
        <p>暂无</p>
      )}
    </article>
  );
}

function OptimizationWorkOrderCard({ data }: { data: AdPerformanceOptimizationWorkOrder }) {
  const sections: Array<{
    key: keyof Pick<AdPerformanceOptimizationWorkOrder, "campaign" | "adset" | "creative">;
    title: string;
    note: string;
  }> = [
    { key: "campaign", title: "广告系列", note: "目标与草稿状态" },
    { key: "adset", title: "广告组", note: "预算、人群与事件" },
    { key: "creative", title: "广告创意", note: "标题、文案、素材与落地页" },
  ];
  const rows = sections.flatMap((section) =>
    data[section.key].map((field) => ({
      field,
      sectionKey: section.key,
      sectionTitle: section.title,
    })),
  );
  const actionRows = rows.filter((row) => optimizationIsActionable(row.field));
  const keptRows = rows.filter((row) => optimizationIsKept(row.field));
  const [activeTab, setActiveTab] = useState<
    "actions" | "campaign" | "adset" | "creative" | "kept"
  >("actions");
  const tabs = [
    { key: "actions", label: "建议处理", count: actionRows.length },
    { key: "campaign", label: "广告系列", count: data.campaign.length },
    { key: "adset", label: "广告组", count: data.adset.length },
    { key: "creative", label: "广告创意", count: data.creative.length },
    { key: "kept", label: "已保留", count: keptRows.length },
  ] as const;
  const visibleRows =
    activeTab === "actions"
      ? actionRows
      : activeTab === "kept"
        ? keptRows
        : rows.filter((row) => row.sectionKey === activeTab);

  return (
    <section className="performance-section optimization-work-order">
      <div className="performance-section-head">
        <h3>AI 优化工单</h3>
        <span>{priorityLabel(data.priority)}</span>
      </div>
      <div className="optimization-work-order-body">
        <div className="optimization-brief">
          <div>
            <span>{optimizationOverallActionLabel(data.overall_action)}</span>
            <strong>{data.operator_summary || "AI 已生成字段级优化建议。"}</strong>
            {data.next_step && <p>{data.next_step}</p>}
          </div>
          <div className="optimization-brief-stats">
            <article>
              <strong>{actionRows.length}</strong>
              <span>需要处理</span>
            </article>
            <article>
              <strong>{data.modules_to_keep.length}</strong>
              <span>建议保留</span>
            </article>
            <article>
              <strong>{data.modules_to_watch.length}</strong>
              <span>继续观察</span>
            </article>
          </div>
        </div>

        <OptimizationModuleChips
          change={data.modules_to_change}
          keep={data.modules_to_keep}
          watch={data.modules_to_watch}
        />

        <div className="optimization-tabs" role="tablist" aria-label="优化工单分类">
          {tabs.map((tab) => (
            <button
              className={activeTab === tab.key ? "active" : ""}
              key={tab.key}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.key}
              onClick={() => setActiveTab(tab.key)}
            >
              <span>{tab.label}</span>
              <em>{tab.count}</em>
            </button>
          ))}
        </div>

        <div className="optimization-action-list">
          {visibleRows.length ? (
            visibleRows.map((row) => (
              <OptimizationFieldAdviceCard
                field={row.field}
                key={`${row.sectionKey}-${row.field.field}`}
                sectionTitle={activeTab === "actions" || activeTab === "kept" ? row.sectionTitle : null}
              />
            ))
          ) : (
            <div className="optimization-empty-state">
              当前分类没有需要展示的字段。
            </div>
          )}
        </div>

        {data.warnings.length > 0 && (
          <div className="optimization-warnings">
            {data.warnings.map((warning) => (
              <span key={warning}>{warning}</span>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function OptimizationModuleChips({
  change,
  keep,
  watch,
}: {
  change: string[];
  keep: string[];
  watch: string[];
}) {
  const groups = [
    { label: "建议处理", items: change, tone: "change" },
    { label: "建议保留", items: keep, tone: "keep" },
    { label: "继续观察", items: watch, tone: "watch" },
  ];
  return (
    <div className="optimization-chip-groups">
      {groups
        .filter((group) => group.items.length > 0)
        .map((group) => (
          <div className={`optimization-chip-group ${group.tone}`} key={group.label}>
            <span>{group.label}</span>
            <div>
              {group.items.slice(0, 6).map((item) => (
                <em key={item}>{item}</em>
              ))}
            </div>
          </div>
        ))}
    </div>
  );
}

function OptimizationFieldAdviceCard({
  field,
  sectionTitle,
}: {
  field: AdPerformanceOptimizationFieldAdvice;
  sectionTitle?: string | null;
}) {
  const suggestedValue = adviceValueText(field.suggested_value);
  const suggestedText = suggestedValue || field.suggested_direction || "";
  const currentText = adviceValueText(field.current_value);
  return (
    <article className={`optimization-field-row action-${optimizationActionClass(field.action)}`}>
      <div className="optimization-field-name">
        {sectionTitle && <span>{sectionTitle}</span>}
        <strong>{field.label}</strong>
      </div>
      <div className="optimization-field-value">
        <span>当前</span>
        <strong title={currentText}>{adviceShortText(field.current_value) || "未收到"}</strong>
      </div>
      <div className="optimization-field-value">
        <span>建议</span>
        <strong title={suggestedText}>
          {adviceShortText(field.suggested_value) ||
            adviceShortText(field.suggested_direction) ||
            "-"}
        </strong>
      </div>
      <span className="optimization-action-pill">{optimizationActionLabel(field.action)}</span>
      <details className="optimization-field-detail">
        <summary>原因与生成方向</summary>
        <p>{field.reason}</p>
        {field.suggested_direction && (
          <div>
            <span>建议方向</span>
            <p>{field.suggested_direction}</p>
          </div>
        )}
        {field.generation_prompt && (
          <div>
            <span>生成提示词</span>
            <p>{field.generation_prompt}</p>
          </div>
        )}
        {field.can_apply_to_generation && <em>可用于重新生成</em>}
      </details>
    </article>
  );
}

function completenessLabel(level: string): string {
  const labels: Record<string, string> = {
    high: "高",
    medium: "中等",
    low: "低",
    unknown: "未知",
  };
  return labels[level] ?? level;
}

function AiVisualAnalysisCard({ data }: { data: AdPerformanceVisualAnalysis }) {
  const sourceUrl = data.source_video_url || data.source_image_url;
  return (
    <article className="performance-visual-card">
      <div className="performance-visual-head">
        <div>
          <h4>素材视觉分析</h4>
          {data.summary && <p>{data.summary}</p>}
        </div>
        {sourceUrl && (
          <a href={sourceUrl} target="_blank" rel="noreferrer">
            查看素材
          </a>
        )}
      </div>
      <div className="performance-ai-grid">
        <AiInsightList title="看到的元素" items={data.observed_elements} />
        <AiInsightList title="画面优点" items={data.strengths} />
        <AiInsightList title="画面问题" items={data.weaknesses} />
        <AiInsightList title="优化建议" items={data.recommendations} />
        <AiInsightList title="风险提醒" items={data.risk_notes} />
      </div>
      {data.confidence_note && <em>{data.confidence_note}</em>}
    </article>
  );
}

function AiInsightList({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <article className="performance-ai-block">
      <h4>{title}</h4>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </article>
  );
}

function analysisModeLabel(mode: string | undefined): string {
  if (mode === "llm_failed") return "大模型分析失败";
  if (mode === "llm_only" || mode === "rules_and_llm") return "大模型分析";
  if (mode === "rules_with_llm_fallback") return "历史分析";
  return "大模型分析";
}

function performanceAnalysisTitle(analysis: AdPerformanceAnalysis): string {
  return (
    analysis.creative_name ||
    analysis.adset_name ||
    analysis.campaign_name ||
    `分析 ${shortId(analysis.id)}`
  );
}

function performanceAnalysisMeta(analysis: AdPerformanceAnalysis): string {
  return [
    analysis.campaign_name,
    analysis.adset_name,
    analysis.date_start && analysis.date_stop ? `${analysis.date_start} 至 ${analysis.date_stop}` : null,
  ]
    .filter(Boolean)
    .join(" / ") || shortId(analysis.id);
}

function confidenceLabel(value: string): string {
  const labels: Record<string, string> = {
    low: "低置信度",
    medium: "中置信度",
    high: "高置信度",
  };
  return labels[value] ?? value;
}

function confidenceStatusClass(value: string): string {
  if (value === "high") return "approved";
  if (value === "low") return "needs_revision";
  return "generated";
}

function severityLabel(value: string): string {
  const labels: Record<string, string> = {
    info: "提示",
    warning: "需关注",
    critical: "优先处理",
  };
  return labels[value] ?? value;
}

function severityStatusClass(value: string): string {
  if (value === "critical") return "failed";
  if (value === "warning") return "needs_revision";
  return "generated";
}

function optimizationOverallActionLabel(value: string): string {
  const labels: Record<string, string> = {
    check_delivery_first: "先查投放",
    check_landing_page_first: "先查落地页",
    continue_testing: "继续测试",
    create_optimized_draft: "生成优化草稿",
    review: "复核建议",
  };
  return labels[value] ?? value;
}

function optimizationActionLabel(value: string): string {
  const labels: Record<string, string> = {
    keep: "保留",
    regenerate: "重新生成",
    rewrite: "重写",
    check: "检查",
    watch: "观察",
    reduce: "降低/保持",
    increase: "提高",
    pause: "暂停",
    create_draft: "草稿",
    missing: "待补充",
  };
  return labels[value] ?? value;
}

function optimizationActionClass(value: string): string {
  if (["regenerate", "rewrite"].includes(value)) return "change";
  if (["check", "missing"].includes(value)) return "check";
  if (["reduce", "increase", "pause"].includes(value)) return "control";
  if (value === "watch") return "watch";
  return "keep";
}

function optimizationIsActionable(field: AdPerformanceOptimizationFieldAdvice): boolean {
  if (field.can_apply_to_generation || field.missing) return true;
  if (["regenerate", "rewrite", "check", "reduce", "increase", "pause", "missing"].includes(field.action)) {
    return true;
  }
  return field.priority === "high" && field.action !== "keep";
}

function optimizationIsKept(field: AdPerformanceOptimizationFieldAdvice): boolean {
  return field.action === "keep" || field.action === "create_draft";
}

function adviceValueText(value: unknown): string {
  if (value == null || value === "") return "";
  return stringifyValue(value);
}

function adviceShortText(value: unknown, maxLength = 58): string {
  const text = adviceValueText(value).replace(/\s+/g, " ").trim();
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength)}...`;
}

function priorityLabel(value: string): string {
  const labels: Record<string, string> = {
    low: "低优先级",
    medium: "中优先级",
    high: "高优先级",
  };
  return labels[value] ?? value;
}

function priorityStatusClass(value: string): string {
  if (value === "high") return "failed";
  if (value === "medium") return "needs_revision";
  return "generated";
}

function areaLabel(value: string): string {
  const labels: Record<string, string> = {
    audience: "人群",
    comparison: "同组对比",
    creative: "素材",
    delivery: "投放",
    landing_page: "落地页",
    objective: "目标",
    overall: "整体",
    video: "视频",
  };
  return labels[value] ?? value;
}

function ImagePreview({ asset }: { asset: CreativeAsset }) {
  if (!asset.url) {
    return (
      <div className="image-placeholder">
        <Image size={28} />
        <span>等待图片 URL</span>
      </div>
    );
  }
  return <img className="asset-image" src={displayAssetUrl(asset.url)} alt={asset.alt_text || "creative"} />;
}

function VideoPreview({ url }: { url: string }) {
  return (
    <div className="video-preview">
      <video controls src={displayAssetUrl(url)} />
    </div>
  );
}

type WorkflowSummary = Record<"fields" | "topic" | "copy" | "image" | "video" | "final", {
  key: string;
  title: string;
  label: string;
  done: boolean;
  status: WorkflowStepStatus;
}>;

function buildWorkflowSummary({
  job,
  campaign,
  topic,
  draft,
  creatives,
  videos,
}: {
  job: AdGenerationJob | null;
  campaign: Campaign | null;
  topic: Topic | null;
  draft: CopyDraft | null;
  creatives: CreativeAsset[];
  videos: VideoAsset[];
}): WorkflowSummary {
  const fieldsDone = Boolean(job && campaign && !["queued", "processing", "failed"].includes(job.status));
  const topicDone = Boolean(topic && topic.status === "selected");
  const copyDone = Boolean(draft && draft.status === "approved");
  const imageDone = creatives.length > 0;
  const videoRequired = adGenerationRequiresVideo(job);
  const videoDone = !videoRequired || videos.length > 0;
  return {
    fields: stepSummary("fields", "参数确认", fieldsDone, fieldsDone ? "参数已确认" : "等待识别", Boolean(job)),
    topic: stepSummary("topic", "人工选题", topicDone, topic?.title || "未选择选题", fieldsDone),
    copy: stepSummary("copy", "审核文案", copyDone, draft?.headline || "未通过文案", topicDone),
    image: stepSummary("image", "审核图片", imageDone, imageDone ? `${creatives.length} 张已通过` : "无通过图片", copyDone),
    video: stepSummary(
      "video",
      "审核视频",
      videoDone,
      videoRequired ? (videos.length ? `${videos.length} 个已通过` : "无通过视频") : "无需视频",
      imageDone && videoRequired,
    ),
    final: stepSummary("final", "最终预审", fieldsDone && topicDone && copyDone && imageDone && videoDone, "确认后回传投放系统", videoDone),
  };
}

function stepSummary(
  key: string,
  title: string,
  done: boolean,
  label: string,
  available: boolean,
): WorkflowSummary["fields"] {
  return {
    key,
    title,
    label,
    done,
    status: done ? "done" : available ? "active" : "blocked",
  };
}

function buildFinalPayload({
  job,
  campaign,
  topic,
  draft,
  creatives,
  videos,
  selectedCreativeIds,
  selectedVideoId,
}: {
  job: AdGenerationJob | null;
  campaign: Campaign | null;
  topic: Topic | null;
  draft: CopyDraft | null;
  creatives: CreativeAsset[];
  videos: VideoAsset[];
  selectedCreativeIds: string[];
  selectedVideoId: string | null;
}): { ok: true; value: Record<string, unknown> } | { ok: false; message: string } {
  if (!job) return { ok: false, message: "请先选择 AI 工单。" };
  if (!campaign) return { ok: false, message: "参数识别尚未完成，请稍后刷新。" };
  if (!topic || topic.status !== "selected") return { ok: false, message: "请先选择选题。" };
  if (!draft || draft.status !== "approved") return { ok: false, message: "请先审核通过文案。" };
  if (!creatives.length) return { ok: false, message: "请先审核通过图片。" };
  const videoRequired = adGenerationRequiresVideo(job);
  if (videoRequired && !videos.length) return { ok: false, message: "请先审核通过视频。" };

  const result = job.result_payload ?? {};
  const campaignPayload = isRecord(result.campaign_payload) ? result.campaign_payload : {};
  const adsetPayload = isRecord(result.adset_payload) ? result.adset_payload : {};
  const review = isRecord(result.review) ? result.review : {};
  const image = creatives.find((item) => selectedCreativeIds.includes(item.id)) ?? creatives[0];
  const video = videos.find((item) => item.id === selectedVideoId) ?? videos[0] ?? null;
  const link = campaignLandingUrl(campaign);
  const creativeType = video ? "video" : "image";
  const imageUrl = image.url;
  const videoUrl = video?.url ?? null;
  const materialUrl = videoUrl || imageUrl;

  return {
    ok: true,
    value: {
      ...result,
      job_id: job.id,
      external_order_id: job.external_order_id,
      status: "final_review",
      campaign_payload: {
        ...campaignPayload,
        name: readText(campaignPayload.name) || campaign.name,
        objective: readText(campaignPayload.objective) || campaign.objective || "OUTCOME_TRAFFIC",
        status: "PAUSED",
        draft: 1,
      },
      adset_payload: {
        ...adsetPayload,
        billing_event: readText(adsetPayload.billing_event) || "IMPRESSIONS",
        optimization_goal: readText(adsetPayload.optimization_goal) || "LINK_CLICKS",
        bid_strategy: readText(adsetPayload.bid_strategy) || "LOWEST_COST_WITHOUT_CAP",
        start_type: readText(adsetPayload.start_type) || "I",
        start_time: Number(adsetPayload.start_time ?? 0),
        status: "PAUSED",
        draft: 1,
      },
      creative_payload: {
        name: `${campaign.name} - ${creativeType}`,
        type: creativeType,
        message: draft.primary_text || draft.body,
        link,
        ads_name: draft.headline || topic.title,
        description: draft.description,
        btn_type: "LEARN_MORE",
        asset_url: materialUrl,
        image_asset_url: imageUrl,
        ...(videoUrl ? { video_asset_url: videoUrl } : {}),
        material_url: materialUrl,
        file_url: materialUrl,
        image_url: imageUrl,
        ...(videoUrl ? { video_url: videoUrl } : {}),
        draft: 1,
      },
      assets: {
        images: creatives.map((asset) => ({
          id: asset.id,
          filename: filenameFromUrl(asset.url),
          url: asset.url,
          asset_url: asset.url,
          material_url: asset.url,
          file_url: asset.url,
          image_url: asset.url,
          image_asset_url: asset.url,
          type: "image",
          size: asset.size,
          prompt: asset.prompt,
          alt_text: asset.alt_text,
        })),
        videos: videos.map((item) => ({
          id: item.id,
          url: item.url,
          asset_url: item.url,
          material_url: item.url,
          file_url: item.url,
          video_url: item.url,
          video_asset_url: item.url,
          type: "video",
          cover_url: null,
          duration_seconds: item.duration_seconds,
          storyboard: item.storyboard,
        })),
      },
      review: {
        ...review,
        final_precheck: {
          topic_reviewed: true,
          copy_reviewed: true,
          image_reviewed: true,
          video_reviewed: !videoRequired || videos.length > 0,
        },
      },
      metadata_json: {
        ...(isRecord(result.metadata_json) ? result.metadata_json : {}),
        workflow_stage: "final_review",
        campaign_id: campaign.id,
        topic_id: topic.id,
        draft_id: draft.id,
        creative_asset_ids: creatives.map((item) => item.id),
        video_asset_ids: videos.map((item) => item.id),
        final_prechecked_at: new Date().toISOString(),
      },
    },
  };
}

function adGenerationRequiresVideo(job: AdGenerationJob | null): boolean {
  if (!job) return false;
  const request = job.request_payload ?? {};
  const preferences = isRecord(request.preferences) ? request.preferences : {};
  if (typeof preferences.video_required === "boolean") return preferences.video_required;
  return readText(preferences.creative_type).toLowerCase() === "video";
}

function loadDeliveryExtractionCache(rawContent: string): DeliveryExtractionCacheEntry | null {
  const key = deliveryExtractionCacheKey(rawContent);
  if (!key) return null;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!isRecord(parsed) || !isRecord(parsed.extraction) || typeof parsed.savedAt !== "string") {
      return null;
    }
    return {
      key,
      extraction: parsed.extraction as unknown as WorkOrderDeliveryExtraction,
      savedAt: parsed.savedAt,
    };
  } catch {
    return null;
  }
}

function saveDeliveryExtractionCache(
  rawContent: string,
  extraction: WorkOrderDeliveryExtraction,
): DeliveryExtractionCacheEntry | null {
  const key = deliveryExtractionCacheKey(rawContent);
  if (!key) return null;
  const entry = { key, extraction, savedAt: new Date().toISOString() };
  try {
    window.localStorage.setItem(key, JSON.stringify(entry));
    pruneDeliveryExtractionCache(key);
    return entry;
  } catch {
    return null;
  }
}

function removeDeliveryExtractionCache(rawContent: string) {
  const key = deliveryExtractionCacheKey(rawContent);
  if (!key) return;
  try {
    window.localStorage.removeItem(key);
    const index = readDeliveryExtractionCacheIndex().filter((item) => item !== key);
    window.localStorage.setItem(DELIVERY_EXTRACTION_CACHE_INDEX_KEY, JSON.stringify(index));
  } catch {
    // Local cache is an optimization only.
  }
}

function pruneDeliveryExtractionCache(latestKey: string) {
  const nextIndex = [
    latestKey,
    ...readDeliveryExtractionCacheIndex().filter((key) => key !== latestKey),
  ];
  const kept = nextIndex.slice(0, DELIVERY_EXTRACTION_CACHE_LIMIT);
  const dropped = nextIndex.slice(DELIVERY_EXTRACTION_CACHE_LIMIT);
  dropped.forEach((key) => window.localStorage.removeItem(key));
  window.localStorage.setItem(DELIVERY_EXTRACTION_CACHE_INDEX_KEY, JSON.stringify(kept));
}

function readDeliveryExtractionCacheIndex(): string[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(DELIVERY_EXTRACTION_CACHE_INDEX_KEY) || "[]");
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

function loadVideoStoryboardDraftCache(campaignId: string): VideoStoryboardDraftCache | null {
  const direct = loadVideoStoryboardDraftCacheFromKey(videoStoryboardDraftCacheKey(campaignId), campaignId);
  if (direct) return direct;

  const latest = loadVideoStoryboardDraftCacheFromKey(VIDEO_STORYBOARD_DRAFT_LAST_CACHE_KEY, campaignId);
  return latest?.campaignId === campaignId ? latest : null;
}

function loadLatestVideoStoryboardDraftCache(): VideoStoryboardDraftCache | null {
  return loadVideoStoryboardDraftCacheFromKey(VIDEO_STORYBOARD_DRAFT_LAST_CACHE_KEY, "");
}

function loadVideoStoryboardDraftCacheFromKey(
  key: string,
  campaignId: string,
): VideoStoryboardDraftCache | null {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!isRecord(parsed)) return null;
    return {
      campaignId: readText(parsed.campaignId) || campaignId,
      aspectRatio: readText(parsed.aspectRatio) || "9:16",
      durationSeconds: Number(parsed.durationSeconds) || 12,
      instructions: readText(parsed.instructions),
      selectedCreativeIds: Array.isArray(parsed.selectedCreativeIds)
        ? parsed.selectedCreativeIds.filter((item): item is string => typeof item === "string")
        : [],
      storyboard: Array.isArray(parsed.storyboard)
        ? parsed.storyboard.filter((item): item is Record<string, unknown> => isRecord(item))
        : [],
      storyboardText: readText(parsed.storyboardText),
      storyboardDirty: typeof parsed.storyboardDirty === "boolean" ? parsed.storyboardDirty : true,
      storyboardFeedback: readText(parsed.storyboardFeedback),
      savedAt: readText(parsed.savedAt) || new Date().toISOString(),
    };
  } catch {
    return null;
  }
}

function saveVideoStoryboardDraftCache(campaignId: string, entry: VideoStoryboardDraftCache) {
  try {
    const hasDraft =
      entry.storyboardText.trim() ||
      entry.instructions.trim() ||
      entry.storyboardFeedback.trim() ||
      entry.storyboard.length;
    if (!hasDraft) {
      return;
    }
    const key = videoStoryboardDraftCacheKey(campaignId);
    window.localStorage.setItem(key, JSON.stringify(entry));
    window.localStorage.setItem(VIDEO_STORYBOARD_DRAFT_LAST_CACHE_KEY, JSON.stringify(entry));
  } catch {
    // Local cache is an optimization only.
  }
}

function videoStoryboardDraftCacheKey(campaignId: string): string {
  return `${VIDEO_STORYBOARD_DRAFT_CACHE_PREFIX}${campaignId}`;
}

function deliveryExtractionCacheKey(rawContent: string): string | null {
  const normalized = normalizeWorkOrderCacheContent(rawContent);
  if (!normalized) return null;
  return `${DELIVERY_EXTRACTION_CACHE_PREFIX}${hashWorkOrderContent(normalized)}`;
}

function normalizeWorkOrderCacheContent(rawContent: string): string {
  return rawContent.trim().replace(/\r\n/g, "\n").replace(/\r/g, "\n");
}

function hashWorkOrderContent(value: string): string {
  let first = 2166136261;
  let second = 2166136261 ^ value.length;
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    first = Math.imul(first ^ code, 16777619);
    second = Math.imul(second ^ (code + index), 16777619);
  }
  return `${value.length}-${(first >>> 0).toString(36)}-${(second >>> 0).toString(36)}`;
}

function emptyReviewedDeliveryFields(): ReviewedDeliveryFields {
  return {
    landing_url: "",
    event_name: DEFAULT_DELIVERY_EVENT_OPTION,
    country: "",
    age_min: "",
    age_max: "",
    gender: "不限",
    audience_description_raw: "",
  };
}

function buildDeliveryConfirmForm(extraction: WorkOrderDeliveryExtraction): ReviewedDeliveryFields {
  const fields = extraction.fields;
  return {
    landing_url: deliveryFieldDisplayText(fields.landing_url),
    event_name: normalizeEventLabel(deliveryFieldDisplayText(fields.event_name)) || DEFAULT_DELIVERY_EVENT_OPTION,
    country: deliveryFieldDisplayText(fields.country),
    age_min: deliveryAgeText(fields.age_min),
    age_max: deliveryAgeText(fields.age_max),
    gender: normalizeGenderLabel(deliveryFieldDisplayText(fields.gender)),
    audience_description_raw: deliveryFieldDisplayText(fields.audience_description_raw),
  };
}

function validateDeliveryConfirmForm(form: ReviewedDeliveryFields): string | null {
  if (!form.landing_url.trim()) return "请补充投放链接。";
  if (!isHttpUrl(form.landing_url.trim())) return "投放链接需要是 http 或 https 开头的完整链接。";
  if (!form.country.trim()) return "请确认投放国家。";
  if (!form.event_name.trim()) return "请选择优化事件。";
  return null;
}

function normalizeReviewedDeliveryFields(form: ReviewedDeliveryFields): ReviewedDeliveryFields {
  return {
    landing_url: form.landing_url.trim(),
    event_name: normalizeEventLabel(form.event_name) || DEFAULT_DELIVERY_EVENT_OPTION,
    country: form.country.trim(),
    age_min: form.age_min.trim() || "不限",
    age_max: form.age_max.trim() || "不限",
    gender: normalizeGenderLabel(form.gender),
    audience_description_raw: form.audience_description_raw.trim(),
  };
}

function countryOptionForValue(value: string) {
  const normalized = value.trim().toLowerCase();
  if (!normalized) return null;
  return (
    DELIVERY_COUNTRY_OPTIONS.find(
      (item) =>
        item.value.toLowerCase() === normalized ||
        item.code.toLowerCase() === normalized ||
        item.label.toLowerCase() === normalized,
    ) ?? null
  );
}

function deliveryFieldDisplayText(field: WorkOrderDeliveryField): string {
  return readText(field.value) || readText(field.normalized_value);
}

function deliveryAgeText(field: WorkOrderDeliveryField): string {
  const value = deliveryFieldDisplayText(field);
  if (!value || ["不限", "all", "none"].includes(value.toLowerCase())) return "";
  return value.replace(/\D/g, "");
}

function deliveryExtractionHint(
  extraction: WorkOrderDeliveryExtraction,
  key: keyof ReviewedDeliveryFields,
): string {
  const field = extraction.fields[key];
  if (!field) return "";
  return `${deliveryStatusLabel(field.status)} / 置信度 ${Math.round((field.confidence || 0) * 100)}%`;
}

function deliveryFieldLabel(key: keyof ReviewedDeliveryFields): string {
  const labels: Record<keyof ReviewedDeliveryFields, string> = {
    landing_url: "投放链接",
    event_name: "优化事件",
    country: "投放国家",
    age_min: "最小年龄",
    age_max: "最大年龄",
    gender: "性别",
    audience_description_raw: "投放人群",
  };
  return labels[key];
}

function deliveryStatusLabel(status: WorkOrderDeliveryField["status"]): string {
  const labels: Record<WorkOrderDeliveryField["status"], string> = {
    extracted: "已识别",
    suggested: "建议值",
    missing: "待补充",
    conflict: "多候选",
  };
  return labels[status] ?? status;
}

function normalizeEventLabel(value: string): string {
  const trimmed = value.trim();
  const normalized = trimmed.toLowerCase().replace(/[^0-9a-z\u4e00-\u9fff]+/g, "");
  if (!normalized) return "";
  if (
    ["completeregistration", "register", "signup", "lead", "leads"].includes(normalized)
    || /注册|线索/.test(trimmed)
  ) return "完成注册 (COMPLETE_REGISTRATION)";
  if (
    ["purchase", "shop", "shopping", "buy", "order", "sales"].includes(normalized)
    || /购物|购买|下单/.test(trimmed)
  ) return DEFAULT_DELIVERY_EVENT_OPTION;
  if (["addtocart", "cart"].includes(normalized) || /加入购物车|加购/.test(trimmed)) {
    return "加入购物车 (ADD_TO_CART)";
  }
  if (["initiatedcheckout", "checkout"].includes(normalized) || /发起结账|结账|结算/.test(trimmed)) {
    return "发起结账 (INITIATED_CHECKOUT)";
  }
  if (normalized === "search" || trimmed.includes("搜索")) return "搜索行为 (SEARCH)";
  if (["addpaymentinfo", "paymentinfo"].includes(normalized) || trimmed.includes("支付信息")) {
    return "添加支付信息 (ADD_PAYMENT_INFO)";
  }
  if (normalized === "firstrecharge" || trimmed.includes("首充")) return "首充 (first_recharge)";
  return DELIVERY_EVENT_OPTIONS.includes(trimmed as (typeof DELIVERY_EVENT_OPTIONS)[number])
    ? trimmed
    : DEFAULT_DELIVERY_EVENT_OPTION;
}

function normalizeGenderLabel(value: string): string {
  const normalized = value.trim().toLowerCase();
  if (!normalized || ["不限", "all", "all genders", "none"].includes(normalized)) return "不限";
  if (["male", "men", "man"].includes(normalized) || value.includes("男")) return "男";
  if (["female", "women", "woman"].includes(normalized) || value.includes("女")) return "女";
  return "不限";
}

function formatStoryboard(storyboard: Record<string, unknown>[]): string {
  if (!storyboard.length) return "";
  return storyboard
    .map((scene, index) => {
      const sceneIndex = sceneValue(scene, "scene_index") || String(index + 1);
      const start = sceneValue(scene, "start_second") || "-";
      const end = sceneValue(scene, "end_second") || "-";
      return [
        `镜头 ${sceneIndex}（${start}-${end} 秒）`,
        `画面：${sceneValue(scene, "visual") || "-"}`,
        `字幕：${sceneValue(scene, "subtitle") || "-"}`,
        `动效：${sceneValue(scene, "motion") || "-"}`,
        `旁白：${sceneValue(scene, "voiceover") || "-"}`,
        `备注：${sceneValue(scene, "notes") || "-"}`,
      ].join("\n");
    })
    .join("\n\n");
}

function storyboardPayloadFromText(
  text: string,
  originalStoryboard: Record<string, unknown>[],
): Record<string, unknown>[] {
  return [
    {
      scene_index: 1,
      visual: text.trim(),
      notes: "manual_storyboard_text",
      original_storyboard: originalStoryboard,
    },
  ];
}

function sceneValue(scene: Record<string, unknown>, key: string): string {
  const value = scene[key];
  if (value == null) return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function upsertById<T extends { id: string }>(items: T[], next: T): T[] {
  return items.some((item) => item.id === next.id)
    ? items.map((item) => (item.id === next.id ? next : item))
    : [next, ...items];
}

function appendOrReplaceById<T extends { id: string }>(items: T[], next: T): T[] {
  return items.some((item) => item.id === next.id)
    ? items.map((item) => (item.id === next.id ? next : item))
    : [...items, next];
}

function prependOrReplaceById<T extends { id: string }>(items: T[], next: T): T[] {
  return items.some((item) => item.id === next.id)
    ? items.map((item) => (item.id === next.id ? next : item))
    : [next, ...items];
}

function hasNewOrFreshArtifact<T extends WorkflowArtifact>(
  nextItems: T[],
  previousItems: T[],
  since: number,
): boolean {
  const previousIds = new Set(previousItems.map((item) => item.id));
  return nextItems.some((item) => !previousIds.has(item.id) || artifactTimestamp(item) >= since - 10000);
}

function artifactTimestamp(item: WorkflowArtifact): number {
  const timestamps = [Date.parse(item.updated_at), Date.parse(item.created_at)].filter(Number.isFinite);
  return timestamps.length ? Math.max(...timestamps) : 0;
}

function initialTopicSlots(limit: number): TopicGenerationSlot[] {
  return Array.from({ length: limit }, (_, index) => ({
    index: index + 1,
    status: "loading" as const,
  }));
}

function initialCreativeSlots(limit: number): CreativeGenerationSlot[] {
  return Array.from({ length: limit }, (_, index) => ({
    index: index + 1,
    status: "loading" as const,
  }));
}

function buildCreativeSlots(
  creatives: CreativeAsset[],
  activeSlots: CreativeGenerationSlot[],
): CreativeGenerationSlot[] {
  if (activeSlots.length) return activeSlots;
  if (!creatives.length) return [];

  const sorted = creatives
    .slice()
    .sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at));
  const assetsByIndex = new Map<number, CreativeAsset>();
  const fallbackAssets: CreativeAsset[] = [];
  for (const asset of sorted) {
    const index = creativeImageIndex(asset, 0);
    if (index >= 1 && index <= CREATIVE_GENERATION_LIMIT && !assetsByIndex.has(index)) {
      assetsByIndex.set(index, asset);
    } else {
      fallbackAssets.push(asset);
    }
  }

  const usedIds = new Set<string>();
  const slots: CreativeGenerationSlot[] = [];

  for (let index = 1; index <= CREATIVE_GENERATION_LIMIT; index += 1) {
    const indexedAsset = assetsByIndex.get(index);
    const fallbackAsset = fallbackAssets.find((asset) => !usedIds.has(asset.id));
    const asset = indexedAsset ?? fallbackAsset;
    if (!asset) continue;
    usedIds.add(asset.id);
    slots.push({
      index,
      status: "done",
      asset,
    });
  }
  return slots;
}

function syncCreativeSlotsWithAssets(
  slots: CreativeGenerationSlot[],
  creatives: CreativeAsset[],
): CreativeGenerationSlot[] {
  if (!slots.length || !creatives.length) return slots;
  return slots.map((slot) => {
    if (!slot.asset) return slot;
    const latestAsset = creatives.find((asset) => asset.id === slot.asset?.id);
    return latestAsset ? { ...slot, asset: latestAsset } : slot;
  });
}

function creativeImageIndex(asset: CreativeAsset, fallback: number): number {
  const metadata = isRecord(asset.metadata_json) ? asset.metadata_json : {};
  const rawIndex = metadata.image_index;
  const index =
    typeof rawIndex === "number"
      ? rawIndex
      : typeof rawIndex === "string"
        ? Number.parseInt(rawIndex, 10)
        : Number.NaN;
  return Number.isFinite(index) && index > 0 ? index : fallback;
}

function adGenerationCampaignId(job: AdGenerationJob): string | null {
  const result = job.result_payload ?? {};
  const metadata = isRecord(result.metadata_json) ? result.metadata_json : {};
  return readText(metadata.campaign_id);
}

function adGenerationJobTitle(job: AdGenerationJob): string {
  const result = job.result_payload ?? {};
  const campaignPayload = isRecord(result.campaign_payload) ? result.campaign_payload : {};
  const request = job.request_payload ?? {};
  const workOrder = isRecord(request.work_order) ? request.work_order : {};
  const structuredFields = isRecord(workOrder.structured_fields) ? workOrder.structured_fields : {};
  return (
    readText(campaignPayload.name) ||
    readText(structuredFields.project_name) ||
    readText(structuredFields.product_name) ||
    job.external_order_id ||
    `AI 工单 ${shortId(job.id)}`
  );
}

function adGenerationJobFailureMessage(job: AdGenerationJob): string {
  return `AI 工单已失败：${apiErrorMessage(job.error_message || "", "请检查任务日志或重新创建。")}`;
}

function adGenerationJobFields(job: AdGenerationJob, campaign: Campaign | null): Record<string, unknown> {
  const result = job.result_payload ?? {};
  const adsetPayload = isRecord(result.adset_payload) ? result.adset_payload : {};
  const creativePayload = isRecord(result.creative_payload) ? result.creative_payload : {};
  return {
    任务状态: statusLabel(job.status),
    外部工单: job.external_order_id,
    项目: campaign?.name || adGenerationJobTitle(job),
    国家: adsetPayload.countries || adsetPayload.country_code,
    年龄: adsetPayload.age_min && adsetPayload.age_max ? `${adsetPayload.age_min}-${adsetPayload.age_max}` : null,
    投放链接: creativePayload.link || campaignLandingUrl(campaign),
    标题: creativePayload.ads_name,
    创建时间: formatDate(job.created_at),
  };
}

function adGenerationRawContent(requestPayload: Record<string, unknown>): string {
  const workOrder = isRecord(requestPayload.work_order) ? requestPayload.work_order : {};
  return readText(workOrder.raw_content) || "暂无工单原文";
}

function campaignLandingUrl(campaign: Campaign | null): string | null {
  if (!campaign) return null;
  const landingPage = isRecord(campaign.metadata_json.landing_page) ? campaign.metadata_json.landing_page : {};
  const workOrder = isRecord(campaign.metadata_json.work_order) ? campaign.metadata_json.work_order : {};
  return readText(landingPage.url) || readText(workOrder.landing_url);
}

function draftTargetLanguageLabel(draft: CopyDraft): string {
  const targetLanguage = isRecord(draft.metadata_json.target_language) ? draft.metadata_json.target_language : {};
  return readText(targetLanguage.label) || readText(targetLanguage.instruction) || "未标注";
}

function draftLandingUrl(draft: CopyDraft | null, campaign: Campaign | null): string | null {
  if (draft) {
    const landingPage = isRecord(draft.metadata_json.landing_page) ? draft.metadata_json.landing_page : {};
    const url = readText(landingPage.url);
    if (url) return url;
  }
  return campaignLandingUrl(campaign);
}

function copySnippet(draft: CopyDraft): string {
  return (draft.primary_text || draft.body || draft.description || "暂无内容").replace(/\s+/g, " ").slice(0, 92);
}

function copyLengthLabel(text: string | null): string {
  return `${Array.from(text || "").length} 字符`;
}

function domainFromUrl(url: string | null): string {
  if (!url) return "";
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function initialViewFromUrl(): ViewKey {
  if (adGenerationJobIdFromUrl()) return "workflow";
  if (window.location.pathname.startsWith("/work-orders/new")) return "work-orders";
  const value = new URLSearchParams(window.location.search).get("view");
  return navItems.some((item) => item.key === value) ? (value as ViewKey) : "dashboard";
}

function adGenerationIntegrationParamsFromUrl(): AdGenerationIntegrationParams {
  const params = new URLSearchParams(window.location.search);
  const returnUrl = queryTextParam(params, "return_url", "returnUrl");
  const callbackUrl = queryTextParam(params, "callback_url", "callbackUrl");
  return {
    externalOrderId: queryTextParam(params, "external_order_id", "externalOrderId"),
    returnUrl: returnUrl && isHttpUrl(returnUrl) ? returnUrl : null,
    callbackUrl: callbackUrl && isHttpUrl(callbackUrl) ? callbackUrl : null,
  };
}

function queryTextParam(params: URLSearchParams, ...keys: string[]): string | null {
  for (const key of keys) {
    const value = params.get(key)?.trim();
    if (value) return value;
  }
  return null;
}

function adGenerationJobIdFromUrl(): string | null {
  const match = window.location.pathname.match(/\/review\/ad-generation\/([^/]+)/);
  if (match?.[1]) return decodeURIComponent(match[1]);
  return new URLSearchParams(window.location.search).get("job_id");
}

function viewTitle(view: ViewKey): string {
  return navItems.find((item) => item.key === view)?.label ?? "工作台";
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    active: "进行中",
    approved: "已通过",
    archived: "已归档",
    copy_review: "文案审核",
    draft: "草稿",
    failed: "失败",
    fields_review: "参数确认",
    final_review: "最终预审",
    generated: "已生成",
    generating: "生成中",
    image_review: "图片审核",
    needs_revision: "需修改",
    processing: "初始化中",
    proposed: "待选择",
    queued: "创建中",
    rejected: "已拒绝",
    returned: "已回传",
    selected: "已选择",
    topic_review: "选题审核",
    video_review: "视频审核",
  };
  return labels[status] ?? status;
}

function reviewStatusFromDecision(entityType: ReviewEntityType, decision: ReviewDecision): string {
  if (entityType === "topic") {
    if (decision === "approved") return "selected";
    if (decision === "rejected") return "rejected";
    return "proposed";
  }
  return decision;
}

function isVideoGeneratingStatus(status: string): boolean {
  return status === "generating";
}

function isAuthApiError(caught: unknown): boolean {
  return caught instanceof ApiError && [401, 403].includes(caught.status);
}

function errorScopeForOperationKey(key: string): ErrorScope {
  if (key.includes("refresh")) return "refresh";
  if (key.includes("topic")) return "topic";
  if (key.includes("copy") || key.includes("draft")) return "copy";
  if (key.includes("creative") || key === "creatives") return "image";
  if (key.includes("video")) return "video";
  if (key.includes("performance")) return "performance";
  if (key.includes("final") || key.includes("confirm-return")) return "final";
  if (key.includes("work-order") || key.includes("ad-generation") || key.startsWith("delete-job")) {
    return "work-order";
  }
  return "global";
}

function shouldShowErrorBanner(error: ScopedAppError, activeView: ViewKey): boolean {
  if (error.scope === "refresh" && error.transient) return false;
  if (error.scope === "global" || error.scope === "refresh") return true;
  if (activeView === "workflow") return true;

  const activeScopes: Record<ViewKey, ErrorScope[]> = {
    dashboard: ["global", "refresh"],
    "work-orders": ["work-order"],
    workflow: ["work-order", "topic", "copy", "image", "video", "final"],
    topics: ["topic"],
    copy: ["copy"],
    creatives: ["image"],
    videos: ["video"],
    performance: ["global", "refresh", "performance"],
  };
  return activeScopes[activeView].includes(error.scope);
}

function mediaPreviewAspectClass(value: string | null | undefined): string {
  const normalized = (value || "").trim().toLowerCase();
  if (!normalized || normalized === "1:1" || normalized === "square") return "square";
  if (normalized === "9:16" || normalized === "portrait" || normalized === "vertical") return "portrait";
  if (normalized === "16:9" || normalized === "landscape" || normalized === "horizontal") return "landscape";

  const match = normalized.match(/^(\d+(?:\.\d+)?)\s*[x:]\s*(\d+(?:\.\d+)?)$/);
  if (match) {
    const width = Number(match[1]);
    const height = Number(match[2]);
    if (Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0) {
      const ratio = width / height;
      if (ratio < 0.8) return "portrait";
      if (ratio > 1.25) return "landscape";
    }
  }

  return "square";
}

function videoWaitSeconds(video: VideoAsset): number {
  const startedAt =
    Date.parse(readText(video.metadata_json?.provider_started_at)) ||
    Date.parse(video.created_at) ||
    Date.parse(video.updated_at);
  if (!Number.isFinite(startedAt)) return 0;
  return Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
}

function workflowStepStatusLabel(status: WorkflowStepStatus): string {
  const labels: Record<WorkflowStepStatus, string> = {
    done: "已完成",
    active: "进行中",
    blocked: "未开始",
  };
  return labels[status];
}

function operationProgressText(
  loading: string | null,
  elapsedSeconds: number,
): { title: string; estimate: string } | null {
  if (!loading) return null;
  const labels: Record<string, { title: string; estimate: string }> = {
    topics: { title: "正在生成选题", estimate: "预计 20-60 秒" },
    copy: { title: "正在生成文案", estimate: "预计 15-45 秒" },
    creatives: { title: "正在生成图片", estimate: "预计 30-90 秒" },
    "video-storyboard": { title: "正在生成视频脚本", estimate: "预计 20-60 秒" },
    "video-storyboard-rewrite": { title: "正在改写视频脚本", estimate: "预计 20-60 秒" },
    video: { title: "正在提交视频生成", estimate: "预计 10-30 秒" },
    "save-final-payload": { title: "正在保存预审包", estimate: "预计几秒" },
    "confirm-return": { title: "正在确认回传", estimate: "预计几秒" },
    "ad-generation-refresh": { title: "正在刷新任务", estimate: "预计几秒" },
    "campaign-refresh": { title: "正在刷新生产数据", estimate: "预计几秒" },
  };
  return labels[loading] ?? { title: "正在处理", estimate: elapsedSeconds > 60 ? "仍在处理中" : "预计稍等片刻" };
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分`;
}

function copyPreviewCreatives(creatives: CreativeAsset[], selectedDraft: CopyDraft | null): CreativeAsset[] {
  const draftId = selectedDraft?.id ?? null;
  return creatives
    .filter((asset) => Boolean(asset.url))
    .slice()
    .sort((left, right) => {
      const leftDraftRank = draftId && left.draft_id === draftId ? 0 : 1;
      const rightDraftRank = draftId && right.draft_id === draftId ? 0 : 1;
      if (leftDraftRank !== rightDraftRank) return leftDraftRank - rightDraftRank;

      const leftStatusRank = creativePreviewStatusRank(left.status);
      const rightStatusRank = creativePreviewStatusRank(right.status);
      if (leftStatusRank !== rightStatusRank) return leftStatusRank - rightStatusRank;

      return Date.parse(right.created_at) - Date.parse(left.created_at);
    });
}

function creativePreviewStatusRank(status: string): number {
  if (status === "approved") return 0;
  if (status === "rejected") return 2;
  return 1;
}

function imagePromptTitle(prompt: string): string {
  return prompt.split("\n")[0].slice(0, 60) || "图片素材";
}

function filenameFromUrl(url: string | null): string | null {
  if (!url) return null;
  return url.split("?")[0].replace(/\/$/, "").split("/").pop() || null;
}

function stringifyValue(value: unknown): string {
  if (Array.isArray(value)) return value.map(stringifyValue).join(", ");
  if (typeof value === "object" && value !== null) return JSON.stringify(value);
  return String(value);
}

function readText(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function parseJsonRecord(value: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(value);
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isHttpUrl(value: string): boolean {
  return /^https?:\/\//i.test(value);
}

function formatDate(value: string | null): string {
  if (!value) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function shortId(value: string): string {
  return value.slice(0, 8);
}

export default App;
