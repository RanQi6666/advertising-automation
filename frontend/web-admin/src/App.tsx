import {
  BarChart3,
  Check,
  ClipboardList,
  Clock3,
  FileText,
  Film,
  Image,
  Layers3,
  Loader2,
  Megaphone,
  Pause,
  RefreshCw,
  Send,
  Sparkles,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { ApiError, api } from "./lib/api";
import type {
  AdCreativeDraft,
  AdPixel,
  AdsPlanDraft,
  Campaign,
  CopyDraft,
  CreativeAsset,
  FacebookPublishConfig,
  LandingPageSnapshot,
  MetaAccount,
  MetaAdsDraftCreateResult,
  PublishJob,
  ReviewedDeliveryFields,
  Topic,
  VideoAsset,
  WorkOrder,
  WorkOrderDeliveryExtraction,
  WorkOrderDeliveryField,
} from "./types/domain";

type ViewKey =
  | "dashboard"
  | "work-orders"
  | "campaign"
  | "topics"
  | "copy"
  | "creatives"
  | "videos"
  | "publishing";

type PublishChannelKey = "facebook_page" | "facebook_ad";
type PublishMediaType = "image" | "video";
type PublishJobFilter = "all" | "review" | "published" | "active" | "paused" | "issue";
type PreflightStatus = "ready" | "warning" | "error";
type PreflightChecklistItem = {
  label: string;
  status: PreflightStatus;
  value: string;
  detail: string;
};
type MetaAssetOption = {
  id: string;
  name: string;
  label: string;
  accountId: string | null;
  businessId?: string | null;
  businessName?: string | null;
};
type MetaIdentityOption = {
  key: string;
  label: string;
  subtitle: string;
  accounts: MetaAccount[];
  pages: MetaAssetOption[];
  adAccounts: MetaAssetOption[];
};
type PendingPreflightAction =
  | {
      kind: "prepare_meta_ads";
      warnings: PreflightChecklistItem[];
    }
  | {
      kind: "publish_job";
      jobId: string;
      warnings: PreflightChecklistItem[];
    };
type CreativeGenerationPhase = "submitting" | "generating" | "saving" | "done" | "failed";
type CreativeGenerationState = {
  phase: CreativeGenerationPhase;
  draftId: string;
  expectedCount: number;
  completedCount: number;
  startedAt: number;
  elapsedSeconds: number;
  message: string;
  detail: string;
};

const navItems: Array<{ key: ViewKey; label: string; icon: typeof BarChart3 }> = [
  { key: "dashboard", label: "工作台", icon: BarChart3 },
  { key: "work-orders", label: "工单", icon: ClipboardList },
  { key: "campaign", label: "广告项目", icon: Layers3 },
  { key: "topics", label: "选题", icon: Sparkles },
  { key: "copy", label: "文案", icon: FileText },
  { key: "creatives", label: "图片", icon: Image },
  { key: "videos", label: "视频", icon: Film },
  { key: "publishing", label: "发布", icon: Megaphone },
];

const viewSubtitles: Record<ViewKey, string> = {
  dashboard: "查看今日待办、审核和发布任务",
  "work-orders": "粘贴工单内容，解析投放信息并创建项目",
  campaign: "检查工单信息、落地页分析和项目上下文",
  topics: "选择 AI 生成的广告角度，进入文案生产",
  copy: "审核、修改并确认可用于投放的广告文案",
  creatives: "生成并审核图片素材，可作为视频来源",
  videos: "基于图片配置视频任务，并提交 Seedance 生成",
  publishing: "创建 Facebook 发布任务，并先用 dry-run 验证",
};

const VIDEO_MAX_REFERENCE_IMAGES = 2;
const DELIVERY_EVENT_OPTIONS = ["流量", "购物", "加购", "线索"] as const;

const sampleWorkOrder = `工单

项目名称：印度tv8%
投放国家：印度
投放时间：待定
日报时区：+7
投放媒体：fb
投放事件：购物
投放人群；男。年龄25-45

产品名称：印度tv
打款金额：216（广告过审打款）
服务费：8%
商务：西伯
投放链接：https://www.mensparadise.store/TV.html`;

function App() {
  const [activeView, setActiveView] = useState<ViewKey>(() => initialViewFromUrl());
  const [workOrders, setWorkOrders] = useState<WorkOrder[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [selectedWorkOrderId, setSelectedWorkOrderId] = useState<string | null>(null);
  const [selectedCampaignId, setSelectedCampaignId] = useState<string | null>(null);
  const [snapshots, setSnapshots] = useState<LandingPageSnapshot[]>([]);
  const [topics, setTopics] = useState<Topic[]>([]);
  const [drafts, setDrafts] = useState<CopyDraft[]>([]);
  const [creatives, setCreatives] = useState<CreativeAsset[]>([]);
  const [hiddenCreativeIds, setHiddenCreativeIds] = useState<string[]>([]);
  const [videos, setVideos] = useState<VideoAsset[]>([]);
  const [publishJobs, setPublishJobs] = useState<PublishJob[]>([]);
  const [facebookConfig, setFacebookConfig] = useState<FacebookPublishConfig | null>(null);
  const [metaAccounts, setMetaAccounts] = useState<MetaAccount[]>([]);
  const [adCreativeDraft, setAdCreativeDraft] = useState<AdCreativeDraft | null>(null);
  const [adsPlanDraft, setAdsPlanDraft] = useState<AdsPlanDraft | null>(null);
  const [metaAdsDraftResult, setMetaAdsDraftResult] = useState<MetaAdsDraftCreateResult | null>(null);
  const [rawWorkOrder, setRawWorkOrder] = useState(sampleWorkOrder);
  const [deliveryConfirmRawContent, setDeliveryConfirmRawContent] = useState("");
  const [deliveryExtraction, setDeliveryExtraction] = useState<WorkOrderDeliveryExtraction | null>(null);
  const [deliveryConfirmForm, setDeliveryConfirmForm] = useState<ReviewedDeliveryFields>(() =>
    emptyReviewedDeliveryFields(),
  );
  const [deliveryConfirmOpen, setDeliveryConfirmOpen] = useState(false);
  const [selectedTopicId, setSelectedTopicId] = useState<string | null>(null);
  const [selectedDraftId, setSelectedDraftId] = useState<string | null>(null);
  const [selectedCreativeIds, setSelectedCreativeIds] = useState<string[]>([]);
  const [currentCreativeBatchIds, setCurrentCreativeBatchIds] = useState<string[]>([]);
  const [copyFeedback, setCopyFeedback] = useState("");
  const [publishMessage, setPublishMessage] = useState("");
  const [publishImageUrl, setPublishImageUrl] = useState("");
  const [publishVideoAssetId, setPublishVideoAssetId] = useState("");
  const [publishChannel, setPublishChannel] = useState<PublishChannelKey>("facebook_page");
  const [publishMediaType, setPublishMediaType] = useState<PublishMediaType>("image");
  const [publishPageId, setPublishPageId] = useState("dry-run-page");
  const [publishAdAccountId, setPublishAdAccountId] = useState("dry-run-ad-account");
  const [selectedMetaAccountId, setSelectedMetaAccountId] = useState("");
  const [metaDailyBudget, setMetaDailyBudget] = useState("");
  const [metaPixelId, setMetaPixelId] = useState("");
  const [metaPixels, setMetaPixels] = useState<AdPixel[]>([]);
  const [metaPixelError, setMetaPixelError] = useState("");
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [creativeConfirmOpen, setCreativeConfirmOpen] = useState(false);
  const [creativeGeneration, setCreativeGeneration] = useState<CreativeGenerationState | null>(null);
  const [videoConfirmId, setVideoConfirmId] = useState<string | null>(null);
  const [metaAdsConfirmOpen, setMetaAdsConfirmOpen] = useState(false);
  const [pendingPreflightAction, setPendingPreflightAction] = useState<PendingPreflightAction | null>(null);
  const [activateConfirmJobId, setActivateConfirmJobId] = useState<string | null>(null);
  const [activateConfirmText, setActivateConfirmText] = useState("");
  const [pauseConfirmJobId, setPauseConfirmJobId] = useState<string | null>(null);
  const [pauseConfirmText, setPauseConfirmText] = useState("");
  const [videoAspectRatio, setVideoAspectRatio] = useState("9:16");
  const [videoDurationSeconds, setVideoDurationSeconds] = useState(12);
  const [videoInstructions, setVideoInstructions] = useState("");
  const [videoStoryboardText, setVideoStoryboardText] = useState("");
  const [videoStoryboard, setVideoStoryboard] = useState<Record<string, unknown>[]>([]);
  const [videoStoryboardDirty, setVideoStoryboardDirty] = useState(false);

  const selectedWorkOrder = useMemo(
    () => workOrders.find((item) => item.id === selectedWorkOrderId) ?? workOrders[0] ?? null,
    [selectedWorkOrderId, workOrders],
  );
  const selectedCampaign = useMemo(
    () => campaigns.find((item) => item.id === selectedCampaignId) ?? campaigns[0] ?? null,
    [selectedCampaignId, campaigns],
  );
  const campaignWorkOrder = useMemo(
    () =>
      (selectedCampaign?.work_order_id
        ? workOrders.find((item) => item.id === selectedCampaign.work_order_id)
        : null) ?? selectedWorkOrder,
    [selectedCampaign?.work_order_id, selectedWorkOrder, workOrders],
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
    [selectedDraftId, drafts],
  );
  const visibleCreatives = useMemo(
    () => creatives.filter((item) => Boolean(item.url) && !hiddenCreativeIds.includes(item.id)),
    [creatives, hiddenCreativeIds],
  );
  const visibleCreativeIds = useMemo(
    () => new Set(visibleCreatives.map((item) => item.id)),
    [visibleCreatives],
  );
  const selectedVisibleCreativeIds = useMemo(
    () => selectedCreativeIds.filter((id) => visibleCreativeIds.has(id)),
    [selectedCreativeIds, visibleCreativeIds],
  );
  const currentCreativeBatchIdSet = useMemo(
    () => new Set(currentCreativeBatchIds),
    [currentCreativeBatchIds],
  );
  const currentBatchCreatives = useMemo(() => {
    const creativesById = new Map(visibleCreatives.map((item) => [item.id, item]));
    return currentCreativeBatchIds
      .map((id) => creativesById.get(id))
      .filter((item): item is CreativeAsset => Boolean(item));
  }, [currentCreativeBatchIds, visibleCreatives]);
  const historicalCreatives = useMemo(
    () => visibleCreatives.filter((item) => !currentCreativeBatchIdSet.has(item.id)),
    [currentCreativeBatchIdSet, visibleCreatives],
  );
  const publishableImages = visibleCreatives;
  const selectedPublishableImage = useMemo(
    () => publishableImages.find((item) => item.url === publishImageUrl) ?? null,
    [publishImageUrl, publishableImages],
  );
  const publishableVideos = useMemo(
    () => videos.filter(isPublishableVideo),
    [videos],
  );
  const selectedPublishableVideo = useMemo(
    () => publishableVideos.find((item) => item.id === publishVideoAssetId) ?? null,
    [publishVideoAssetId, publishableVideos],
  );
  const selectedVideoThumbnailAssetId = useMemo(() => {
    const sourceAssetIds = selectedPublishableVideo?.source_asset_ids ?? [];
    const firstVisibleSource = sourceAssetIds.find((id) => visibleCreativeIds.has(String(id)));
    return firstVisibleSource ? String(firstVisibleSource) : null;
  }, [selectedPublishableVideo, visibleCreativeIds]);
  const publishCreativeAssetId =
    publishMediaType === "image"
      ? selectedPublishableImage?.id ?? null
      : selectedVideoThumbnailAssetId;
  const selectedMetaAccount = useMemo(
    () => metaAccounts.find((item) => item.id === selectedMetaAccountId) ?? null,
    [metaAccounts, selectedMetaAccountId],
  );
  const metaIdentityOptions = useMemo(
    () => buildMetaIdentityOptions(metaAccounts, facebookConfig),
    [facebookConfig, metaAccounts],
  );
  const selectedMetaIdentity = useMemo(
    () =>
      selectedMetaAccount
        ? metaIdentityOptions.find((item) => item.key === metaIdentityKey(selectedMetaAccount)) ?? null
        : null,
    [metaIdentityOptions, selectedMetaAccount],
  );

  useEffect(() => {
    void refreshBaseData();
    const params = new URLSearchParams(window.location.search);
    if (params.get("meta_oauth") === "success") {
      setNotice("Meta 账号授权成功，已刷新可用投放账号。");
    }
    if (params.get("meta_oauth") === "error") {
      setError(`Meta 授权失败：${params.get("reason") ?? "unknown"}`);
    }
  }, []);

  useEffect(() => {
    if (selectedCampaign?.id) {
      void refreshCampaignData(selectedCampaign.id);
    }
  }, [selectedCampaign?.id]);

  useEffect(() => {
    if (!publishMessage && selectedDraft) {
      setPublishMessage(selectedDraft.primary_text || selectedDraft.body);
    }
  }, [publishMessage, selectedDraft]);

  useEffect(() => {
    setPublishImageUrl((current) => {
      if (current && publishableImages.some((item) => item.url === current)) return current;
      return publishableImages[0]?.url ?? "";
    });
  }, [publishableImages]);

  useEffect(() => {
    setPublishVideoAssetId((current) => {
      if (current && publishableVideos.some((item) => item.id === current)) return current;
      return publishableVideos[0]?.id ?? "";
    });
  }, [publishableVideos]);

  useEffect(() => {
    if (publishMediaType === "video" && !publishableVideos.length && publishableImages.length) {
      setPublishMediaType("image");
    }
    if (publishMediaType === "image" && !publishableImages.length && publishableVideos.length) {
      setPublishMediaType("video");
    }
  }, [publishMediaType, publishableImages.length, publishableVideos.length]);

  useEffect(() => {
    if (!selectedMetaAccount) {
      return;
    }
    const pageIds = new Set((selectedMetaIdentity?.pages ?? []).map((item) => item.id));
    const adAccountIds = new Set((selectedMetaIdentity?.adAccounts ?? []).map((item) => item.id));
    setPublishPageId((current) => {
      if (current && pageIds.has(current)) return current;
      return selectedMetaAccount.page_id || selectedMetaIdentity?.pages[0]?.id || current;
    });
    setPublishAdAccountId((current) => {
      const normalizedCurrent = current ? normalizeMetaAdAccountId(current) : "";
      if (normalizedCurrent && adAccountIds.has(normalizedCurrent)) return normalizedCurrent;
      return (
        normalizeMetaAdAccountId(selectedMetaAccount.ad_account_id) ||
        selectedMetaIdentity?.adAccounts[0]?.id ||
        current
      );
    });
  }, [selectedMetaAccount, selectedMetaIdentity]);

  useEffect(() => {
    if (!publishAdAccountId) {
      setMetaPixels([]);
      setMetaPixelError("");
      return;
    }
    void refreshMetaPixels({ silent: true });
  }, [publishAdAccountId, selectedMetaAccountId]);

  useEffect(() => {
    if (!isCreativeGenerationActive(creativeGeneration)) {
      return;
    }
    const timer = window.setInterval(() => {
      setCreativeGeneration((current) => {
        if (!current || !isCreativeGenerationActive(current)) {
          return current;
        }
        return {
          ...current,
          elapsedSeconds: Math.max(0, Math.floor((Date.now() - current.startedAt) / 1000)),
        };
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [creativeGeneration?.phase, creativeGeneration?.startedAt]);

  useEffect(() => {
    setCreativeGeneration(null);
    setCurrentCreativeBatchIds([]);
    setVideoStoryboard([]);
    setVideoStoryboardText("");
    setVideoStoryboardDirty(false);
    setAdCreativeDraft(null);
    setAdsPlanDraft(null);
    setMetaAdsDraftResult(null);
  }, [selectedCampaign?.id]);

  async function run<T>(key: string, task: () => Promise<T>, success?: string): Promise<T | null> {
    setLoading(key);
    setError(null);
    setNotice(null);
    try {
      const result = await task();
      if (success) {
        setNotice(success);
      }
      return result;
    } catch (caught) {
      const message =
        caught instanceof ApiError || caught instanceof Error ? caught.message : "操作失败";
      setError(message);
      return null;
    } finally {
      setLoading(null);
    }
  }

  async function refreshBaseData() {
    await run("refresh", async () => {
      const [
        nextWorkOrders,
        nextCampaigns,
        nextPublishJobs,
        nextFacebookConfig,
        nextMetaAccounts,
      ] = await Promise.all([
        api.listWorkOrders(100),
        api.listCampaigns(100),
        api.listPublishJobs(),
        api.getMetaPublishConfig(),
        api.listMetaAccounts(),
      ]);
      setWorkOrders(nextWorkOrders);
      setCampaigns(nextCampaigns);
      setPublishJobs(nextPublishJobs);
      setFacebookConfig(nextFacebookConfig);
      setMetaAccounts(nextMetaAccounts);
      const defaultMetaAccount = nextMetaAccounts[0] ?? null;
      const activeMetaAccount =
        (selectedMetaAccountId
          ? nextMetaAccounts.find((item) => item.id === selectedMetaAccountId)
          : null) ?? defaultMetaAccount;
      if (!selectedMetaAccountId && activeMetaAccount) {
        setSelectedMetaAccountId(activeMetaAccount.id);
      }
      if (activeMetaAccount?.page_id) {
        setPublishPageId(activeMetaAccount.page_id);
      } else if (nextFacebookConfig.page.id) {
        setPublishPageId(nextFacebookConfig.page.id);
      }
      if (activeMetaAccount?.ad_account_id) {
        setPublishAdAccountId(normalizeMetaAdAccountId(activeMetaAccount.ad_account_id));
      } else if (nextFacebookConfig.ads.ad_account_id) {
        setPublishAdAccountId(normalizeMetaAdAccountId(nextFacebookConfig.ads.ad_account_id));
      }
      if (!selectedWorkOrderId && nextWorkOrders[0]) {
        setSelectedWorkOrderId(nextWorkOrders[0].id);
      }
      if (!selectedCampaignId && nextCampaigns[0]) {
        setSelectedCampaignId(nextCampaigns[0].id);
      }
    });
  }

  async function refreshCampaignData(campaignId: string) {
    await run("campaign-refresh", async () => {
      const [nextSnapshots, nextTopics, nextDrafts, nextCreatives, nextVideos, nextPublishJobs] =
        await Promise.all([
          api.listLandingPageSnapshots(campaignId),
          api.listTopics(campaignId),
          api.listDrafts(campaignId),
          api.listCreatives(campaignId),
          api.listVideos(campaignId),
          api.listPublishJobs(campaignId),
        ]);
      setSnapshots(nextSnapshots);
      setTopics(nextTopics);
      setDrafts(nextDrafts);
      setCreatives(nextCreatives);
      setVideos(nextVideos);
      setPublishJobs(nextPublishJobs);
      const availableIds = new Set(nextCreatives.map((item) => item.id));
      setHiddenCreativeIds((current) => current.filter((id) => availableIds.has(id)));
      setCurrentCreativeBatchIds((current) => {
        const retainedIds = current.filter((id) => availableIds.has(id));
        if (retainedIds.length) return retainedIds;
        return nextCreatives.filter((item) => Boolean(item.url)).slice(0, 3).map((item) => item.id);
      });
      setSelectedTopicId((current) =>
        current && nextTopics.some((item) => item.id === current) ? current : nextTopics[0]?.id ?? null,
      );
      setSelectedDraftId((current) =>
        current && nextDrafts.some((item) => item.id === current) ? current : nextDrafts[0]?.id ?? null,
      );
      setSelectedCreativeIds((current) => {
        const retainedIds = current.filter((id) => availableIds.has(id));
        return retainedIds.length ? retainedIds : nextCreatives.slice(0, 3).map((item) => item.id);
      });
    });
  }

  async function refreshMetaPixels(options: { silent?: boolean } = {}) {
    if (!publishAdAccountId) {
      setMetaPixels([]);
      setMetaPixelError("请先选择广告账户 Ad Account。");
      return;
    }
    if (!options.silent) {
      setLoading("meta-pixels");
      setError(null);
      setNotice(null);
    }
    setMetaPixelError("");
    try {
      const pixels = await api.listAdPixels(selectedMetaAccountId || null, publishAdAccountId);
      setMetaPixels(pixels);
      if (!options.silent) {
        setNotice(pixels.length ? "Meta Pixel 列表已刷新" : "当前广告账户没有返回 Pixel。");
      }
      setMetaPixelId((current) => {
        if (current && pixels.some((pixel) => pixel.id === current)) return current;
        return current || pixels[0]?.id || "";
      });
    } catch (caught) {
      const message =
        caught instanceof ApiError || caught instanceof Error ? caught.message : "Pixel 列表获取失败";
      const friendlyMessage = formatMetaPixelError(message);
      setMetaPixels([]);
      setMetaPixelError(friendlyMessage);
      if (!options.silent) setError(friendlyMessage);
    } finally {
      if (!options.silent) setLoading(null);
    }
  }

  function handleCreativeImageInvalid(assetId: string) {
    setHiddenCreativeIds((current) => (current.includes(assetId) ? current : [...current, assetId]));
  }

  useEffect(() => {
    setSelectedCreativeIds((current) => {
      const next = current.filter((id) => !hiddenCreativeIds.includes(id));
      return next.length === current.length ? current : next;
    });
  }, [hiddenCreativeIds]);

  async function handleCreateWorkOrder() {
    const content = rawWorkOrder.trim();
    if (!content) {
      setError("请先粘贴工单内容。");
      setNotice(null);
      return;
    }

    const extracted = await run(
      "extract-work-order",
      () => api.extractWorkOrderDeliveryFields(content),
      "请确认投放信息",
    );
    if (extracted) {
      setDeliveryExtraction(extracted);
      setDeliveryConfirmForm(buildDeliveryConfirmForm(extracted));
      setDeliveryConfirmRawContent(content);
      setDeliveryConfirmOpen(true);
    }
  }

  function handleDeliveryConfirmChange(key: keyof ReviewedDeliveryFields, value: string) {
    setDeliveryConfirmForm((current) => ({ ...current, [key]: value }));
  }

  function handleCancelDeliveryConfirm() {
    setDeliveryConfirmOpen(false);
  }

  async function handleConfirmCreateWorkOrder() {
    if (!deliveryExtraction) return;
    const validationError = validateDeliveryConfirmForm(deliveryConfirmForm);
    if (validationError) {
      setError(validationError);
      setNotice(null);
      return;
    }

    const reviewedFields = normalizeReviewedDeliveryFields(deliveryConfirmForm);
    const created = await run(
      "create-work-order",
      () =>
        api.createWorkOrder(
          deliveryConfirmRawContent || rawWorkOrder.trim(),
          reviewedFields,
          deliveryExtraction,
        ),
      "工单已创建",
    );
    if (created) {
      setWorkOrders((current) => [created, ...current]);
      setSelectedWorkOrderId(created.id);
      setActiveView("work-orders");
      setDeliveryConfirmOpen(false);
      setDeliveryExtraction(null);
      setDeliveryConfirmRawContent("");
    }
  }

  async function handleCreateCampaign(workOrderId: string) {
    const campaign = await run(
      "create-campaign",
      () => api.createCampaignFromWorkOrder(workOrderId),
      "广告项目已创建",
    );
    if (campaign) {
      setCampaigns((current) => [campaign, ...current]);
      setSelectedCampaignId(campaign.id);
      setActiveView("campaign");
    }
  }

  async function handleAnalyzeLandingPage(forceRefresh = true) {
    if (!selectedCampaign) return;
    const snapshot = await run(
      "landing",
      () => api.analyzeLandingPage(selectedCampaign.id, forceRefresh),
      "落地页分析已完成",
    );
    if (snapshot) {
      setSnapshots((current) => [snapshot, ...current]);
    }
  }

  async function handleGenerateTopics(signals: Record<string, unknown> = {}) {
    if (!selectedCampaign) return;
    const generated = await run(
      "topics",
      () => api.generateTopics(selectedCampaign.id, 3, signals),
      "选题已生成",
    );
    if (generated) {
      setTopics(generated);
      setSelectedTopicId(generated[0]?.id ?? null);
      setActiveView("topics");
    }
  }

  async function handleSelectTopic(topicId: string) {
    const topic = await run("select-topic", () => api.selectTopic(topicId), "选题已选择");
    if (topic) {
      setTopics((current) =>
        current.map((item) => {
          if (item.id === topic.id) return topic;
          if (item.campaign_id === topic.campaign_id && item.status === "selected") {
            return { ...item, status: "proposed" };
          }
          return item;
        }),
      );
      setSelectedTopicId(topic.id);
    }
  }

  async function handleGenerateCopy() {
    if (!selectedTopic) return;
    const draft = await run(
      "copy",
      () => api.generateCopy(selectedTopic.id, "Learn More"),
      "文案已生成",
    );
    if (draft) {
      setDrafts((current) => [draft, ...current]);
      setSelectedDraftId(draft.id);
      setPublishMessage(draft.primary_text || draft.body);
      setActiveView("copy");
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

  async function handleReview(entityType: string, entityId: string, decision: "approved" | "rejected" | "needs_revision") {
    if (!selectedCampaign) return;
    const review = await run(
      `review-${entityType}-${decision}`,
      () => api.submitReview(entityType, entityId, decision, selectedCampaign.id, copyFeedback || undefined),
      "审核结果已提交",
    );
    if (review && selectedCampaign.id) {
      await refreshCampaignData(selectedCampaign.id);
    }
  }

  async function handleGenerateCreatives() {
    if (!selectedDraft) return;
    const expectedCount = 3;
    const startedAt = Date.now();
    const draftId = selectedDraft.id;
    setActiveView("creatives");
    setCreativeGeneration({
      phase: "submitting",
      draftId,
      expectedCount,
      completedCount: 0,
      startedAt,
      elapsedSeconds: 0,
      message: "正在提交图片生成请求",
      detail: "准备把当前文案发送给图片生成服务。",
    });
    const generated = await run(
      "creatives",
      async () => {
        setCreativeGeneration((current) =>
          current?.startedAt === startedAt
            ? {
                ...current,
                phase: "generating",
                message: "火山引擎正在生成图片",
                detail: "通常需要几十秒，请保持后端服务运行，完成后会自动刷新到图片列表。",
                elapsedSeconds: Math.max(0, Math.floor((Date.now() - startedAt) / 1000)),
              }
            : current,
        );
        const result = await api.generateCreatives(draftId, expectedCount, "1:1");
        setCreativeGeneration((current) =>
          current?.startedAt === startedAt
            ? {
                ...current,
                phase: "saving",
                completedCount: result.length,
                message: "正在更新图片列表",
                detail: "图片已经生成并写入本地存储，正在同步到当前页面。",
                elapsedSeconds: Math.max(0, Math.floor((Date.now() - startedAt) / 1000)),
              }
            : current,
        );
        return result;
      },
      "图片已生成",
    );
    if (generated) {
      setCreatives((current) => [...generated, ...current]);
      const generatedIds = generated.slice(0, 3).map((item) => item.id);
      setCurrentCreativeBatchIds(generatedIds);
      setSelectedCreativeIds(generatedIds);
      setActiveView("creatives");
      setCreativeGeneration({
        phase: "done",
        draftId,
        expectedCount,
        completedCount: generated.length,
        startedAt,
        elapsedSeconds: Math.max(0, Math.floor((Date.now() - startedAt) / 1000)),
        message: `本次已生成 ${generated.length} 张图片`,
        detail: "新图片已经放在列表顶部，可以直接审核或选择用于视频。",
      });
    } else {
      setCreativeGeneration((current) =>
        current?.startedAt === startedAt
          ? {
              ...current,
              phase: "failed",
              message: "图片生成失败",
              detail: "请查看页面顶部的错误提示，修正后可以重新生成。",
              elapsedSeconds: Math.max(0, Math.floor((Date.now() - startedAt) / 1000)),
            }
          : current,
      );
    }
  }

  function requestGenerateCreatives() {
    setError(null);
    setNotice(null);
    if (!selectedDraft) {
      setError("请先生成或选择一条文案，再生成图片");
      return;
    }
    setCreativeConfirmOpen(true);
  }

  async function confirmGenerateCreatives() {
    setCreativeConfirmOpen(false);
    await handleGenerateCreatives();
  }

  function handleOpenVideoConfig() {
    setError(null);
    setNotice(null);
    if (selectedVisibleCreativeIds.length === 0) {
      setError("请先选择至少一张图片，再创建视频任务");
      return;
    }
    setActiveView("videos");
  }

  async function handleGenerateVideoStoryboard() {
    if (!selectedCampaign) return;
    if (selectedVisibleCreativeIds.length === 0) {
      setError("请先选择至少一张图片，再生成视频脚本");
      return;
    }
    if (selectedVisibleCreativeIds.length > VIDEO_MAX_REFERENCE_IMAGES) {
      setError(`Seedance 1.5 pro 当前最多支持 ${VIDEO_MAX_REFERENCE_IMAGES} 张首尾帧图片，请减少选择后再生成视频脚本`);
      return;
    }
    const generated = await run(
      "video-storyboard",
      () =>
        api.generateVideoStoryboard(
          selectedCampaign.id,
          selectedVisibleCreativeIds,
          selectedDraft?.id ?? null,
          videoDurationSeconds,
          videoAspectRatio,
          videoInstructions,
        ),
      "视频脚本已生成",
    );
    if (generated) {
      setVideoAspectRatio(generated.aspect_ratio);
      setVideoDurationSeconds(generated.duration_seconds);
      setVideoStoryboard(generated.storyboard);
      setVideoStoryboardText(formatStoryboard(generated.storyboard));
      setVideoStoryboardDirty(false);
    }
  }

  function handleStoryboardTextChange(value: string) {
    setVideoStoryboardText(value);
    setVideoStoryboardDirty(true);
  }

  async function handleCreateVideo() {
    if (!selectedCampaign || selectedVisibleCreativeIds.length === 0) return;
    if (selectedVisibleCreativeIds.length > VIDEO_MAX_REFERENCE_IMAGES) {
      setError(`Seedance 1.5 pro 当前最多支持 ${VIDEO_MAX_REFERENCE_IMAGES} 张首尾帧图片，请减少选择后再创建视频任务`);
      return;
    }
    if (!videoStoryboardText.trim()) {
      setError("请先使用 AI 生成视频脚本，或手动填写 storyboard");
      return;
    }
    const storyboardPayload = videoStoryboardDirty
      ? storyboardPayloadFromText(videoStoryboardText, videoStoryboard)
      : videoStoryboard;
    const video = await run(
      "video",
      () =>
        api.createVideoFromImages({
          campaignId: selectedCampaign.id,
          creativeAssetIds: selectedVisibleCreativeIds,
          draftId: selectedDraft?.id ?? null,
          prompt: videoStoryboardText,
          durationSeconds: videoDurationSeconds,
          aspectRatio: videoAspectRatio,
          storyboard: storyboardPayload,
        }),
      "视频任务已创建",
    );
    if (video) {
      setVideos((current) => [video, ...current]);
      setActiveView("videos");
    }
  }

  function requestStartVideoGeneration(videoId: string) {
    setError(null);
    setNotice(null);
    setVideoConfirmId(videoId);
  }

  async function confirmStartVideoGeneration() {
    if (!videoConfirmId) return;
    const targetId = videoConfirmId;
    const video = await run(
      `video-generate-${targetId}`,
      () => api.startVideoGeneration(targetId),
      "视频生成任务已提交",
    );
    if (video) {
      setVideos((current) => current.map((item) => (item.id === video.id ? video : item)));
    }
    setVideoConfirmId(null);
  }

  async function handleRefreshVideoGeneration(videoId: string) {
    const video = await run(
      `video-refresh-${videoId}`,
      () => api.refreshVideoGeneration(videoId),
      "视频状态已刷新",
    );
    if (video) {
      setVideos((current) => current.map((item) => (item.id === video.id ? video : item)));
    }
  }

  async function handleCreatePublishJob() {
    if (!selectedCampaign) return;
    const message = publishMessage || selectedDraft?.primary_text || selectedDraft?.body || "";
    if (publishMediaType === "image" && !selectedPublishableImage) {
      setError("请选择一张可发布图片，或切换为视频发布。");
      return;
    }
    if (publishMediaType === "video" && !publishVideoAssetId) {
      setError("请选择一个已生成的视频，或切换为图片发布。");
      return;
    }
    const accessTokenRef =
      publishChannel === "facebook_page"
        ? facebookConfig?.page.access_token_ref
        : facebookConfig?.ads.access_token_ref;
    const job = await run(
      "publish-create",
      () =>
        api.createPublishJob({
          campaignId: selectedCampaign.id,
          facebookAccountId: selectedMetaAccountId || null,
          draftId: selectedDraft?.id ?? null,
          channel: publishChannel,
          mediaType: publishMediaType,
          message,
          pageId: publishPageId,
          adAccountId: publishAdAccountId,
          accessTokenRef,
          imageUrl: publishMediaType === "image" ? selectedPublishableImage?.url ?? undefined : undefined,
          videoAssetId: publishMediaType === "video" ? publishVideoAssetId : undefined,
        }),
      "发布任务已创建",
    );
    if (job) {
      setPublishJobs((current) => [job, ...current]);
      setActiveView("publishing");
    }
  }

  async function handleBuildAdCreativeDraft() {
    if (!selectedCampaign) return;
    const draft = await run(
      "ad-creative-draft",
      () =>
        api.buildAdCreativeDraft({
          campaignId: selectedCampaign.id,
          facebookAccountId: selectedMetaAccountId || null,
          draftId: selectedDraft?.id ?? null,
          topicId: selectedTopic?.id ?? null,
          creativeAssetId: publishCreativeAssetId,
          videoAssetId: publishMediaType === "video" ? publishVideoAssetId : null,
          pageId: publishPageId,
          adAccountId: publishAdAccountId,
          ctaType: "LEARN_MORE",
        }),
      "广告创意草稿已生成",
    );
    if (draft) {
      setAdCreativeDraft(draft);
    }
  }

  async function handleBuildAdsPlanDraft() {
    if (!selectedCampaign) return;
    const draft = await run(
      "ads-plan-draft",
      () =>
        api.buildAdsPlanDraft({
          campaignId: selectedCampaign.id,
          facebookAccountId: selectedMetaAccountId || null,
          draftId: selectedDraft?.id ?? null,
          topicId: selectedTopic?.id ?? null,
          creativeAssetId: publishCreativeAssetId,
          videoAssetId: publishMediaType === "video" ? publishVideoAssetId || null : null,
          pageId: publishPageId,
          adAccountId: publishAdAccountId,
          dailyBudget: parseOptionalInteger(metaDailyBudget),
          pixelId: metaPixelId.trim() || null,
        }),
      "投放计划草稿已生成",
    );
    if (draft) {
      setAdsPlanDraft(draft);
    }
  }

  function requestPrepareMetaAdsPackage(preflightItems: PreflightChecklistItem[] = []) {
    setError(null);
    setNotice(null);
    if (!selectedCampaign || !selectedDraft) {
      setError("请先选择广告项目和文案。");
      return;
    }
    if (!parseOptionalInteger(metaDailyBudget)) {
      setError("请填写 Meta daily_budget，必须是大于 0 的整数。");
      return;
    }
    const blockingItems = preflightItems.filter((item) => item.status === "error");
    if (blockingItems.length) {
      setError(`投放前检查未通过：${blockingItems.map((item) => item.label).join("、")}。请先修正红色项。`);
      return;
    }
    const warningItems = preflightItems.filter((item) => item.status === "warning");
    if (warningItems.length) {
      setPendingPreflightAction({ kind: "prepare_meta_ads", warnings: warningItems });
      return;
    }
    setMetaAdsConfirmOpen(true);
  }

  async function confirmPrepareMetaAdsPackage() {
    if (!selectedCampaign || !selectedDraft) return;
    const dailyBudget = parseOptionalInteger(metaDailyBudget);
    if (!dailyBudget) return;
    setMetaAdsConfirmOpen(false);
    const job = await run(
      "meta-ads-prepare",
      () =>
        api.prepareMetaAdsPackage({
          campaignId: selectedCampaign.id,
          facebookAccountId: selectedMetaAccountId || null,
          draftId: selectedDraft.id,
          topicId: selectedTopic?.id ?? null,
          creativeAssetId: publishCreativeAssetId,
          videoAssetId: publishMediaType === "video" ? publishVideoAssetId || null : null,
          pageId: publishPageId,
          adAccountId: publishAdAccountId,
          dailyBudget,
          pixelId: metaPixelId.trim() || null,
        }),
      "Meta 投流包已准备，等待人工审核",
    );
    if (job) {
      setPublishJobs((current) => [job, ...current]);
      setMetaAdsDraftResult(null);
      await refreshCampaignData(selectedCampaign.id);
    }
  }

  function confirmPreflightAction() {
    const action = pendingPreflightAction;
    if (!action) return;
    setPendingPreflightAction(null);
    if (action.kind === "prepare_meta_ads") {
      void confirmPrepareMetaAdsPackage();
      return;
    }
    void executePublishJob(action.jobId);
  }

  async function handleConnectMetaAccount() {
    const result = await run("meta-oauth", async () => {
      const returnUrl = `${window.location.origin}${window.location.pathname}?view=publishing`;
      return await api.getMetaOAuthAuthorizeUrl(returnUrl);
    });
    if (result) {
      window.location.href = result.authorization_url;
    }
  }

  function handlePublish(jobId: string) {
    setError(null);
    setNotice(null);
    const publishJob = publishJobs.find((item) => item.id === jobId);
    if (!publishJob) {
      setError("未找到发布任务，请刷新后重试。");
      return;
    }
    const blockingItems = publishJobBlockingPreflightItems(publishJob);
    if (blockingItems.length) {
      setError(`一键发布已阻止：${blockingItems.map((item) => item.label).join("、")} 未通过。`);
      return;
    }
    const warningItems = publishJobWarningPreflightItems(publishJob);
    if (warningItems.length) {
      setPendingPreflightAction({ kind: "publish_job", jobId, warnings: warningItems });
      return;
    }
    void executePublishJob(jobId);
  }

  async function executePublishJob(jobId: string) {
    const job = await run("publish", () => api.publishJob(jobId), "dry-run 发布已完成");
    if (job) {
      setPublishJobs((current) => current.map((item) => (item.id === job.id ? job : item)));
    }
  }

  async function handleSyncMetaStatus(jobId: string) {
    const job = await run(
      `meta-status-${jobId}`,
      () => api.syncMetaAdsStatus(jobId),
      "Meta 审核状态已同步",
    );
    if (job) {
      setPublishJobs((current) => current.map((item) => (item.id === job.id ? job : item)));
    }
  }

  async function handleSyncMetaInsights(jobId: string) {
    const job = await run(
      `meta-insights-${jobId}`,
      () => api.syncMetaAdsInsights(jobId),
      "Meta 广告数据已同步",
    );
    if (job) {
      setPublishJobs((current) => current.map((item) => (item.id === job.id ? job : item)));
    }
  }

  function requestActivateMetaAds(jobId: string) {
    setError(null);
    setNotice(null);
    setActivateConfirmText("");
    setActivateConfirmJobId(jobId);
  }

  async function confirmActivateMetaAds() {
    if (!activateConfirmJobId || activateConfirmText !== "ACTIVE") return;
    const jobId = activateConfirmJobId;
    const job = await run(
      "meta-ads-activate",
      () => api.activateMetaAdsJob(jobId, activateConfirmText),
      "Meta ads are now ACTIVE",
    );
    if (job) {
      setPublishJobs((current) => current.map((item) => (item.id === job.id ? job : item)));
      setActivateConfirmJobId(null);
      setActivateConfirmText("");
    }
  }

  function requestPauseMetaAds(jobId: string) {
    setError(null);
    setNotice(null);
    setPauseConfirmText("");
    setPauseConfirmJobId(jobId);
  }

  async function confirmPauseMetaAds() {
    if (!pauseConfirmJobId || pauseConfirmText !== "PAUSE") return;
    const jobId = pauseConfirmJobId;
    const job = await run(
      "meta-ads-pause",
      () => api.pauseMetaAdsJob(jobId, pauseConfirmText),
      "Meta 投放已暂停",
    );
    if (job) {
      setPublishJobs((current) => current.map((item) => (item.id === job.id ? job : item)));
      setPauseConfirmJobId(null);
      setPauseConfirmText("");
    }
  }

  const todayWorkOrders = workOrders.filter((item) => isToday(item.created_at)).length;
  const pendingCopy = drafts.filter((item) => !["approved", "rejected"].includes(item.status)).length;
  const pendingCreatives = creatives.filter((item) => !["approved", "rejected"].includes(item.status)).length;
  const latestSnapshot = snapshots[0] ?? null;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-block">
          <div className="brand-mark">AA</div>
          <div>
            <div className="brand-name">广告自动化</div>
            <div className="brand-meta">运营后台</div>
          </div>
        </div>

        <nav className="nav-list">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.key}
                className={`nav-button ${activeView === item.key ? "active" : ""}`}
                onClick={() => setActiveView(item.key)}
                title={item.label}
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
            <div className="project-control">
              <span>当前项目</span>
              {selectedCampaign ? (
                <select
                  value={selectedCampaign.id}
                  onChange={(event) => setSelectedCampaignId(event.target.value)}
                  className="select project-select"
                >
                  {campaigns.map((campaign, index) => (
                    <option value={campaign.id} key={campaign.id}>
                      {campaignOptionLabel(campaign, index)}
                    </option>
                  ))}
                </select>
              ) : (
                <strong>未选择</strong>
              )}
            </div>
            <button className="icon-button" onClick={() => void refreshBaseData()} title="刷新">
              {loading === "refresh" ? <Loader2 size={18} className="spin" /> : <RefreshCw size={18} />}
            </button>
          </div>
        </header>

        {(error || notice) && (
          <div className={`banner ${error ? "error" : "success"}`}>
            {error ? <X size={18} /> : <Check size={18} />}
            <span>{error || notice}</span>
          </div>
        )}

        {activeView === "dashboard" && (
          <DashboardView
            todayWorkOrders={todayWorkOrders}
            pendingCopy={pendingCopy}
            pendingCreatives={pendingCreatives}
            publishJobs={publishJobs}
            selectedCampaign={selectedCampaign}
            setActiveView={setActiveView}
          />
        )}

        {activeView === "work-orders" && (
          <WorkOrdersView
            rawWorkOrder={rawWorkOrder}
            setRawWorkOrder={setRawWorkOrder}
            workOrders={workOrders}
            selectedWorkOrder={selectedWorkOrder}
            setSelectedWorkOrderId={setSelectedWorkOrderId}
            onCreateWorkOrder={handleCreateWorkOrder}
            onCreateCampaign={handleCreateCampaign}
            loading={loading}
          />
        )}

        {activeView === "campaign" && (
          <CampaignView
            campaign={selectedCampaign}
            workOrder={campaignWorkOrder}
            snapshot={latestSnapshot}
            snapshots={snapshots}
            onAnalyze={() => void handleAnalyzeLandingPage(true)}
            onGenerateTopics={handleGenerateTopics}
            loading={loading}
          />
        )}

        {activeView === "topics" && (
          <TopicsView
            topics={topics}
            selectedTopic={selectedTopic}
            onGenerate={handleGenerateTopics}
            onSelect={handleSelectTopic}
            onGenerateCopy={handleGenerateCopy}
            loading={loading}
          />
        )}

        {activeView === "copy" && (
          <CopyView
            drafts={drafts}
            selectedDraft={selectedDraft}
            setSelectedDraftId={setSelectedDraftId}
            selectedTopic={selectedTopic}
            selectedCampaign={selectedCampaign}
            creatives={visibleCreatives}
            feedback={copyFeedback}
            setFeedback={setCopyFeedback}
            onGenerateCopy={handleGenerateCopy}
            onReviseCopy={handleReviseCopy}
            onReview={handleReview}
            onGenerateCreatives={requestGenerateCreatives}
            loading={loading}
          />
        )}

        {activeView === "creatives" && (
          <CreativesView
            currentCreatives={currentBatchCreatives}
            historicalCreatives={historicalCreatives}
            selectedCreativeIds={selectedVisibleCreativeIds}
            setSelectedCreativeIds={setSelectedCreativeIds}
            onGenerate={requestGenerateCreatives}
            onReview={handleReview}
            onCreateVideo={handleOpenVideoConfig}
            onInvalidImage={handleCreativeImageInvalid}
            generation={creativeGeneration}
          />
        )}

        {activeView === "videos" && (
          <VideosView
            videos={videos}
            creatives={visibleCreatives}
            selectedCreativeIds={selectedVisibleCreativeIds}
            setSelectedCreativeIds={setSelectedCreativeIds}
            aspectRatio={videoAspectRatio}
            setAspectRatio={setVideoAspectRatio}
            durationSeconds={videoDurationSeconds}
            setDurationSeconds={setVideoDurationSeconds}
            instructions={videoInstructions}
            setInstructions={setVideoInstructions}
            storyboardText={videoStoryboardText}
            setStoryboardText={handleStoryboardTextChange}
            onGenerateStoryboard={handleGenerateVideoStoryboard}
            onCreateVideo={handleCreateVideo}
            onStartGeneration={requestStartVideoGeneration}
            onRefreshGeneration={handleRefreshVideoGeneration}
            onReview={handleReview}
            loading={loading}
          />
        )}

        {activeView === "publishing" && (
          <PublishingView
            publishJobs={publishJobs}
            facebookConfig={facebookConfig}
            metaAccounts={metaAccounts}
            selectedMetaAccountId={selectedMetaAccountId}
            setSelectedMetaAccountId={setSelectedMetaAccountId}
            adCreativeDraft={adCreativeDraft}
            adsPlanDraft={adsPlanDraft}
            metaAdsDraftResult={metaAdsDraftResult}
            selectedCampaign={selectedCampaign}
            selectedDraft={selectedDraft}
            publishMessage={publishMessage}
            setPublishMessage={setPublishMessage}
            publishImageUrl={publishImageUrl}
            setPublishImageUrl={setPublishImageUrl}
            publishVideoAssetId={publishVideoAssetId}
            setPublishVideoAssetId={setPublishVideoAssetId}
            publishChannel={publishChannel}
            setPublishChannel={setPublishChannel}
            publishMediaType={publishMediaType}
            setPublishMediaType={setPublishMediaType}
            publishPageId={publishPageId}
            setPublishPageId={setPublishPageId}
            publishAdAccountId={publishAdAccountId}
            setPublishAdAccountId={setPublishAdAccountId}
        metaDailyBudget={metaDailyBudget}
        setMetaDailyBudget={setMetaDailyBudget}
        metaPixelId={metaPixelId}
        setMetaPixelId={setMetaPixelId}
        metaPixels={metaPixels}
        metaPixelError={metaPixelError}
        creatives={publishableImages}
        videos={publishableVideos}
            onCreateJob={handleCreatePublishJob}
        onConnectMetaAccount={handleConnectMetaAccount}
        onRefreshMetaAccounts={() => void refreshBaseData()}
        onRefreshMetaPixels={() => void refreshMetaPixels()}
        onBuildAdCreativeDraft={handleBuildAdCreativeDraft}
            onBuildAdsPlanDraft={handleBuildAdsPlanDraft}
            onPrepareMetaAdsPackage={requestPrepareMetaAdsPackage}
            onReview={handleReview}
            onPublish={handlePublish}
            onSyncMetaStatus={handleSyncMetaStatus}
            onSyncMetaInsights={handleSyncMetaInsights}
            onActivateMetaAds={requestActivateMetaAds}
            onPauseMetaAds={requestPauseMetaAds}
            loading={loading}
          />
        )}

        <DeliveryConfirmDialog
          open={deliveryConfirmOpen}
          extraction={deliveryExtraction}
          form={deliveryConfirmForm}
          loading={loading === "create-work-order"}
          onChange={handleDeliveryConfirmChange}
          onConfirm={() => void handleConfirmCreateWorkOrder()}
          onCancel={handleCancelDeliveryConfirm}
        />

        <ConfirmDialog
          open={creativeConfirmOpen}
          icon="image"
          title="确认生成图片？"
          description="这一步会调用真实图片生成 API，一次生成 3 张图片，可能消耗额度。确认后才会开始生成。"
          confirmLabel="确认生成"
          cancelLabel="取消"
          loading={loading === "creatives"}
          onConfirm={() => void confirmGenerateCreatives()}
          onCancel={() => setCreativeConfirmOpen(false)}
        />
        <ConfirmDialog
          open={Boolean(videoConfirmId)}
          icon="video"
          title="开始生成视频？"
          description="确认后会调用火山方舟 Doubao-Seedance-1.5-pro 视频生成 API，可能消耗额度。任务会进入生成中，需要稍后刷新状态获取视频。"
          confirmLabel="开始生成"
          cancelLabel="取消"
          loading={videoConfirmId ? loading === `video-generate-${videoConfirmId}` : false}
          onConfirm={() => void confirmStartVideoGeneration()}
          onCancel={() => setVideoConfirmId(null)}
        />
        <ConfirmDialog
          open={metaAdsConfirmOpen}
          icon="publish"
          title="确认准备 Meta 投流包？"
          description="系统会把 Campaign、Ad Set、Ad Creative 和 Ad 所需字段保存为待审核投流包。此步骤不会调用 Facebook Marketing API。"
          confirmLabel="确认准备"
          cancelLabel="取消"
          loading={loading === "meta-ads-prepare"}
          onConfirm={() => void confirmPrepareMetaAdsPackage()}
          onCancel={() => setMetaAdsConfirmOpen(false)}
        />
        <PreflightRiskDialog
          action={pendingPreflightAction}
          loading={loading === "meta-ads-prepare" || loading === "publish"}
          onConfirm={confirmPreflightAction}
          onCancel={() => setPendingPreflightAction(null)}
        />
        <ActivateAdsDialog
          open={Boolean(activateConfirmJobId)}
          value={activateConfirmText}
          loading={loading === "meta-ads-activate"}
          onChange={setActivateConfirmText}
          onConfirm={() => void confirmActivateMetaAds()}
          onCancel={() => {
            setActivateConfirmJobId(null);
            setActivateConfirmText("");
          }}
        />
        <PauseAdsDialog
          open={Boolean(pauseConfirmJobId)}
          value={pauseConfirmText}
          loading={loading === "meta-ads-pause"}
          onChange={setPauseConfirmText}
          onConfirm={() => void confirmPauseMetaAds()}
          onCancel={() => {
            setPauseConfirmJobId(null);
            setPauseConfirmText("");
          }}
        />
      </main>
    </div>
  );
}

function DashboardView({
  todayWorkOrders,
  pendingCopy,
  pendingCreatives,
  publishJobs,
  selectedCampaign,
  setActiveView,
}: {
  todayWorkOrders: number;
  pendingCopy: number;
  pendingCreatives: number;
  publishJobs: PublishJob[];
  selectedCampaign: Campaign | null;
  setActiveView: (view: ViewKey) => void;
}) {
  return (
    <section className="view-stack">
      <div className="metrics-grid">
        <Metric label="今日工单" value={todayWorkOrders} accent="blue" hint="今日新增" icon={ClipboardList} />
        <Metric label="待审核文案" value={pendingCopy} accent="amber" hint="需要人工确认" icon={FileText} />
        <Metric label="待审核图片" value={pendingCreatives} accent="violet" hint="等待素材审核" icon={Image} />
        <Metric label="最近发布任务" value={publishJobs.length} accent="rose" hint="近期待发布/已发布" icon={Megaphone} />
      </div>

      <div className="two-column">
        <section className="panel workflow-panel">
          <div className="panel-header">
            <h2>当前流程</h2>
            <span className="panel-note">按顺序推进</span>
          </div>
          <div className="flow-actions">
            <StepButton index={1} label="工单" onClick={() => setActiveView("work-orders")} />
            <StepButton index={2} label="广告项目" onClick={() => setActiveView("campaign")} disabled={!selectedCampaign} />
            <StepButton index={3} label="选题" onClick={() => setActiveView("topics")} disabled={!selectedCampaign} />
            <StepButton index={4} label="文案" onClick={() => setActiveView("copy")} disabled={!selectedCampaign} />
            <StepButton index={5} label="图片" onClick={() => setActiveView("creatives")} disabled={!selectedCampaign} />
            <StepButton index={6} label="视频" onClick={() => setActiveView("videos")} disabled={!selectedCampaign} />
            <StepButton index={7} label="发布" onClick={() => setActiveView("publishing")} disabled={!selectedCampaign} />
          </div>
        </section>

        <section className="panel">
          <div className="panel-header">
            <h2>最近发布</h2>
          </div>
          <DataList emptyText="暂无发布任务">
            {publishJobs.slice(0, 5).map((job) => (
              <div className="list-row" key={job.id}>
                <div>
                  <strong>{job.channel}</strong>
                  <span>{job.external_id || job.id}</span>
                </div>
                <StatusPill status={job.status} />
              </div>
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
  workOrders,
  selectedWorkOrder,
  setSelectedWorkOrderId,
  onCreateWorkOrder,
  onCreateCampaign,
  loading,
}: {
  rawWorkOrder: string;
  setRawWorkOrder: (value: string) => void;
  workOrders: WorkOrder[];
  selectedWorkOrder: WorkOrder | null;
  setSelectedWorkOrderId: (id: string) => void;
  onCreateWorkOrder: () => void;
  onCreateCampaign: (id: string) => void;
  loading: string | null;
}) {
  const createLoading = loading === "extract-work-order" || loading === "create-work-order";

  return (
    <section className="three-column">
      <section className="panel wide">
        <div className="panel-header">
          <h2>创建工单</h2>
          <button className="primary-button" onClick={onCreateWorkOrder} disabled={createLoading}>
            {createLoading ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />}
            <span>{loading === "extract-work-order" ? "解析中" : "解析并创建"}</span>
          </button>
        </div>
        <textarea
          className="work-order-input"
          value={rawWorkOrder}
          onChange={(event) => setRawWorkOrder(event.target.value)}
        />
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2>工单列表</h2>
        </div>
        <DataList emptyText="暂无工单">
          {workOrders.map((order) => (
            <button
              className={`list-button ${selectedWorkOrder?.id === order.id ? "active" : ""}`}
              key={order.id}
              onClick={() => setSelectedWorkOrderId(order.id)}
            >
              <strong>{order.project_name || "未命名工单"}</strong>
              <span>{formatDate(order.created_at)}</span>
            </button>
          ))}
        </DataList>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2>解析字段</h2>
          {selectedWorkOrder && (
            <button
              className="secondary-button"
              onClick={() => onCreateCampaign(selectedWorkOrder.id)}
              disabled={loading === "create-campaign"}
            >
              <Layers3 size={16} />
              <span>创建项目</span>
            </button>
          )}
        </div>
        {selectedWorkOrder ? <KeyValueTable data={workOrderFields(selectedWorkOrder)} /> : <EmptyState text="暂无工单" />}
      </section>
    </section>
  );
}

function CampaignView({
  campaign,
  workOrder,
  snapshot,
  snapshots,
  onAnalyze,
  onGenerateTopics,
  loading,
}: {
  campaign: Campaign | null;
  workOrder: WorkOrder | null;
  snapshot: LandingPageSnapshot | null;
  snapshots: LandingPageSnapshot[];
  onAnalyze: () => void;
  onGenerateTopics: (signals?: Record<string, unknown>) => void;
  loading: string | null;
}) {
  if (!campaign) return <EmptyState text="暂无广告项目" />;

  return (
    <section className="view-stack">
      <div className="two-column">
        <section className="panel">
          <div className="panel-header">
            <h2>广告项目</h2>
          </div>
          <KeyValueTable
            data={{
              项目名称: campaign.name,
              投放事件: campaign.objective,
              产品名称: campaign.product_name,
              投放人群: campaign.audience_description,
              状态: campaign.status,
            }}
          />
        </section>

        <section className="panel">
          <div className="panel-header">
            <h2>工单信息</h2>
          </div>
          {workOrder ? (
            <KeyValueTable data={workOrderFields(workOrder)} />
          ) : (
            <JsonBlock value={campaign.metadata_json.work_order} />
          )}
        </section>
      </div>

      <section className="panel">
        <div className="panel-header">
          <h2>落地页分析</h2>
          <div className="button-row">
            <button className="secondary-button" onClick={onAnalyze} disabled={loading === "landing"}>
              {loading === "landing" ? <Loader2 size={16} className="spin" /> : <RefreshCw size={16} />}
              <span>分析落地页</span>
            </button>
            <button className="primary-button" onClick={() => onGenerateTopics()} disabled={loading === "topics"}>
              {loading === "topics" ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />}
              <span>生成选题</span>
            </button>
          </div>
        </div>
        {snapshot ? (
          <div className="snapshot-layout">
            <KeyValueTable
              data={{
                URL: snapshot.url,
                状态: snapshot.status,
                HTTP: snapshot.http_status,
                标题: snapshot.title,
                描述: snapshot.description,
              }}
            />
            <div className="excerpt">{snapshot.text_content?.slice(0, 900)}</div>
          </div>
        ) : (
          <EmptyState text="暂无落地页快照" />
        )}
        {snapshots.length > 1 && <div className="muted-line">历史快照：{snapshots.length} 条</div>}
      </section>
    </section>
  );
}

function TopicsView({
  topics,
  selectedTopic,
  onGenerate,
  onSelect,
  onGenerateCopy,
  loading,
}: {
  topics: Topic[];
  selectedTopic: Topic | null;
  onGenerate: (signals?: Record<string, unknown>) => void;
  onSelect: (id: string) => void;
  onGenerateCopy: () => void;
  loading: string | null;
}) {
  const [directionText, setDirectionText] = useState("");
  const [selectedDirections, setSelectedDirections] = useState<string[]>([]);
  const currentBatch = topics.slice(0, Math.min(3, topics.length));
  const confirmedSelectedTopic =
    topics.find((topic) => topic.status === "selected") ??
    (selectedTopic?.status === "selected" ? selectedTopic : null);
  const mainTopic = confirmedSelectedTopic ?? selectedTopic ?? currentBatch[0] ?? null;
  const isMainTopicSelected = Boolean(confirmedSelectedTopic && mainTopic?.id === confirmedSelectedTopic.id);
  const alternativeTopics = currentBatch.filter((topic) => topic.id !== mainTopic?.id);
  const historyTopics = topics.filter(
    (topic) => !currentBatch.some((item) => item.id === topic.id) && topic.id !== mainTopic?.id,
  );
  const directionOptions = [
    { label: "更本地化", value: "localization" },
    { label: "突出价格", value: "price_value" },
    { label: "降低风险", value: "lower_policy_risk" },
    { label: "换痛点", value: "new_pain_point" },
    { label: "更强转化", value: "conversion_focus" },
  ];

  function toggleDirection(value: string) {
    setSelectedDirections((current) =>
      current.includes(value) ? current.filter((item) => item !== value) : [...current, value],
    );
  }

  function handleRegenerateTopics() {
    onGenerate({
      operator_direction: {
        presets: selectedDirections,
        notes: directionText.trim(),
      },
      requested_output: {
        topic_count: 3,
        include_recommendation_reason: true,
        include_policy_risk: true,
        include_image_direction: true,
      },
    });
  }

  return (
    <section className="view-stack topic-workbench">
      <section className="topic-main-grid">
        <section className="panel topic-decision-panel">
          <div className="panel-header">
            <div>
              <h2>{confirmedSelectedTopic ? "当前已选题" : "AI 推荐选题"}</h2>
              <span className="panel-note">
                {mainTopic
                  ? `${topicRecommendationLabel(mainTopic, topics.indexOf(mainTopic), isMainTopicSelected)} · ${statusLabel(topicEffectiveStatus(mainTopic, isMainTopicSelected))}`
                  : "等待生成"}
              </span>
            </div>
            <button className="primary-button" onClick={() => onGenerate()} disabled={loading === "topics"}>
              {loading === "topics" ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />}
              <span>生成 1-3 个</span>
            </button>
          </div>

          {mainTopic ? (
            <TopicDecisionCard
              topic={mainTopic}
              isSelected={isMainTopicSelected}
              index={Math.max(0, topics.indexOf(mainTopic))}
              onSelect={onSelect}
              onGenerateCopy={onGenerateCopy}
              loading={loading}
              featured
            />
          ) : (
            <EmptyState text="暂无选题，请先生成 1-3 个广告选题" />
          )}
        </section>

        <section className="topic-side-stack">
          <section className="panel">
            <div className="panel-header">
              <div>
                <h2>重新生成方向</h2>
                <span className="panel-note">让下一批选题更贴近运营判断</span>
              </div>
            </div>
            <div className="topic-regenerate-box">
              <div className="copy-feedback-tags">
                {directionOptions.map((option) => (
                  <button
                    className={`copy-feedback-tag ${selectedDirections.includes(option.value) ? "active" : ""}`}
                    key={option.value}
                    type="button"
                    onClick={() => toggleDirection(option.value)}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
              <textarea
                className="feedback-input topic-direction-input"
                value={directionText}
                onChange={(event) => setDirectionText(event.target.value)}
                placeholder="例如：更适合印度男性 25-45 岁，少用夸张承诺，突出免费看球和频道丰富。"
              />
              <button
                className="secondary-button"
                onClick={handleRegenerateTopics}
                disabled={loading === "topics"}
              >
                {loading === "topics" ? <Loader2 size={16} className="spin" /> : <RefreshCw size={16} />}
                <span>按方向重新生成</span>
              </button>
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <h2>备选选题</h2>
                <span className="panel-note">{alternativeTopics.length ? `${alternativeTopics.length} 个备选` : "暂无备选"}</span>
              </div>
            </div>
            <DataList emptyText="暂无备选选题">
              {alternativeTopics.map((topic) => (
                <TopicCompactCard
                  topic={topic}
                  key={topic.id}
                  isSelected={confirmedSelectedTopic?.id === topic.id}
                  confirmedSelectedTopicId={confirmedSelectedTopic?.id ?? null}
                  index={Math.max(0, topics.indexOf(topic))}
                  onSelect={onSelect}
                />
              ))}
            </DataList>
          </section>
        </section>
      </section>

      <details className="topic-history-details">
        <summary>
          <span>历史批次</span>
          <small>{historyTopics.length ? `${historyTopics.length} 个历史选题` : "暂无历史"}</small>
        </summary>
        <DataList emptyText="暂无历史选题">
          {historyTopics.map((topic) => (
            <TopicCompactCard
              topic={topic}
              key={topic.id}
              isSelected={confirmedSelectedTopic?.id === topic.id}
              confirmedSelectedTopicId={confirmedSelectedTopic?.id ?? null}
              index={Math.max(0, topics.indexOf(topic))}
              onSelect={onSelect}
            />
          ))}
        </DataList>
      </details>

      {confirmedSelectedTopic && (
        <NextStep
          title="下一步：生成文案"
          actionLabel="生成文案"
          onAction={onGenerateCopy}
          loading={loading === "copy"}
        />
      )}
    </section>
  );
}

function TopicDecisionCard({
  topic,
  isSelected,
  index,
  onSelect,
  onGenerateCopy,
  loading,
  featured = false,
}: {
  topic: Topic;
  isSelected: boolean;
  index: number;
  onSelect: (id: string) => void;
  onGenerateCopy: () => void;
  loading: string | null;
  featured?: boolean;
}) {
  const risk = topicRiskLevel(topic);
  const match = topicLandingMatch(topic);
  const effectiveStatus = topicEffectiveStatus(topic, isSelected);
  return (
    <article className={`topic-decision-card ${featured ? "featured" : ""} ${isSelected ? "active" : ""}`}>
      <div className="item-head">
        <div>
          <span className="topic-kicker">{topicRecommendationLabel(topic, index, isSelected)}</span>
          <h3>{topic.title}</h3>
        </div>
        <StatusPill status={effectiveStatus} />
      </div>
      <p className="topic-angle">{topic.angle}</p>

      <div className="topic-metric-grid">
        <TopicMetric label="目标人群" value={topic.audience || "按工单人群"} />
        <TopicMetric label="推荐度" value={topicScoreLabel(topic.score)} tone={topic.score && topic.score >= 0.8 ? "ready" : "warning"} />
        <TopicMetric label="风险等级" value={risk.label} tone={risk.tone} />
        <TopicMetric label="落地页匹配" value={match.label} tone={match.tone} />
      </div>

      <div className="tag-row">
        {topic.selling_points.slice(0, 5).map((point) => (
          <span className="tag" key={point}>
            {point}
          </span>
        ))}
      </div>

      <div className="topic-detail-grid">
        <div>
          <span>推荐理由</span>
          <p>{topic.rationale || "结合工单、落地页和目标人群生成，可作为当前广告切入角度。"}</p>
        </div>
        <div>
          <span>风险提示</span>
          <p>{topic.risk_notes || "暂无明显风险，发布前仍建议检查夸张承诺和平台政策。"}</p>
        </div>
        <div>
          <span>图片方向</span>
          <p>{topicImageDirection(topic)}</p>
        </div>
        <div>
          <span>文案预览方向</span>
          <p>{topicCopyDirection(topic)}</p>
        </div>
      </div>

      <div className="button-row topic-card-actions">
        {!isSelected ? (
          <button className="primary-button" onClick={() => onSelect(topic.id)}>
            <Check size={16} />
            <span>选择这个选题</span>
          </button>
        ) : (
          <div className="review-complete topic-selected-note">
            <Check size={16} />
            <span>已作为当前选题</span>
          </div>
        )}
        <button className="secondary-button" onClick={onGenerateCopy} disabled={!isSelected || loading === "copy"}>
          {loading === "copy" ? <Loader2 size={16} className="spin" /> : <FileText size={16} />}
          <span>生成文案</span>
        </button>
      </div>
    </article>
  );
}

function TopicCompactCard({
  topic,
  isSelected,
  confirmedSelectedTopicId,
  index,
  onSelect,
}: {
  topic: Topic;
  isSelected: boolean;
  confirmedSelectedTopicId: string | null;
  index: number;
  onSelect: (id: string) => void;
}) {
  const risk = topicRiskLevel(topic);
  const effectiveStatus = topicEffectiveStatus(topic, isSelected);
  return (
    <article className={`topic-item topic-compact-card ${isSelected ? "active" : ""}`}>
      <div className="item-head">
        <div>
          <span className="topic-kicker">{topicRecommendationLabel(topic, index, isSelected)}</span>
          <h3>{topic.title}</h3>
        </div>
        <StatusPill status={effectiveStatus} />
      </div>
      <p>{topic.angle}</p>
      <div className="topic-compact-meta">
        <span>{topic.audience || "按工单人群"}</span>
        <span>{topicScoreLabel(topic.score)}</span>
        <span className={`topic-risk-${risk.tone}`}>{risk.label}</span>
      </div>
      <div className="tag-row">
        {topic.selling_points.slice(0, 3).map((point) => (
          <span className="tag" key={point}>
            {point}
          </span>
        ))}
      </div>
      <div className="button-row">
        <button className="secondary-button" onClick={() => onSelect(topic.id)} disabled={isSelected}>
          <Check size={16} />
          <span>{isSelected ? "已选择" : "选择"}</span>
        </button>
        {!isSelected && topic.status === "selected" && confirmedSelectedTopicId && (
          <span className="topic-stale-note">旧选择记录，重新选择会自动修正</span>
        )}
      </div>
    </article>
  );
}

function TopicMetric({
  label,
  value,
  tone = "ready",
}: {
  label: string;
  value: string;
  tone?: PreflightStatus;
}) {
  return (
    <div className={`topic-metric topic-metric-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function topicRecommendationLabel(topic: Topic, index: number, isSelected = false): string {
  if (isSelected) return "当前选题";
  if ((topic.score ?? 0) >= 0.85 || index === 0) return "AI 推荐";
  if (index === 1) return "备选角度";
  return "差异化角度";
}

function topicEffectiveStatus(topic: Topic, isSelected: boolean): string {
  if (isSelected) return "selected";
  return topic.status === "selected" ? "proposed" : topic.status;
}

function topicScoreLabel(score: number | null): string {
  if (score == null) return "待判断";
  if (score >= 0.85) return "高";
  if (score >= 0.68) return "中";
  return "低";
}

function topicRiskLevel(topic: Topic): { label: string; tone: PreflightStatus } {
  const text = `${topic.risk_notes || ""} ${topic.angle || ""}`.toLowerCase();
  if (/(guarantee|unsupported|敏感|违规|封号|绝对|100%|治愈|保证|暴富)/i.test(text)) {
    return { label: "较高", tone: "error" };
  }
  if (/(validate|avoid|claim|risk|注意|审核|承诺|免费|夸张)/i.test(text)) {
    return { label: "中", tone: "warning" };
  }
  return { label: "低", tone: "ready" };
}

function topicLandingMatch(topic: Topic): { label: string; tone: PreflightStatus } {
  const signals = topic.source_data?.signals;
  if (!isRecord(signals)) return { label: "待确认", tone: "warning" };
  const hasLandingPage = isRecord(signals.landing_page);
  const hasWorkOrder = isRecord(signals.work_order);
  if (hasLandingPage && hasWorkOrder) return { label: "高", tone: "ready" };
  if (hasLandingPage || hasWorkOrder) return { label: "中", tone: "warning" };
  return { label: "低", tone: "error" };
}

function topicImageDirection(topic: Topic): string {
  const points = topic.selling_points.slice(0, 2).join("、");
  if (points) return `围绕「${points}」做主视觉，画面文字短、对比强，适合信息流快速扫读。`;
  return `围绕「${topic.title}」做主视觉，突出核心利益点和清晰行动引导。`;
}

function topicCopyDirection(topic: Topic): string {
  const audience = topic.audience || "目标人群";
  const point = topic.selling_points[0] || topic.angle;
  return `先抓住${audience}的痛点，再突出「${point}」，结尾给出明确点击理由。`;
}

function CopyView({
  drafts,
  selectedDraft,
  setSelectedDraftId,
  selectedTopic,
  selectedCampaign,
  creatives,
  feedback,
  setFeedback,
  onGenerateCopy,
  onReviseCopy,
  onReview,
  onGenerateCreatives,
  loading,
}: {
  drafts: CopyDraft[];
  selectedDraft: CopyDraft | null;
  setSelectedDraftId: (id: string) => void;
  selectedTopic: Topic | null;
  selectedCampaign: Campaign | null;
  creatives: CreativeAsset[];
  feedback: string;
  setFeedback: (value: string) => void;
  onGenerateCopy: () => void;
  onReviseCopy: () => void;
  onReview: (entityType: string, entityId: string, decision: "approved" | "rejected" | "needs_revision") => void;
  onGenerateCreatives: () => void;
  loading: string | null;
}) {
  const landingUrl = landingUrlFromCampaign(selectedCampaign);
  const previewImage =
    (selectedDraft
      ? creatives.find((asset) => asset.draft_id === selectedDraft.id && asset.url)
      : null) ??
    creatives.find((asset) => asset.url) ??
    null;
  const primaryText = selectedDraft?.primary_text || selectedDraft?.body || "";
  const fullBody = selectedDraft?.body || "";
  const hasSeparateFullBody =
    Boolean(selectedDraft?.primary_text) &&
    Boolean(fullBody) &&
    selectedDraft?.primary_text?.trim() !== fullBody.trim();
  const headline = selectedDraft?.headline || selectedTopic?.title || "广告标题";
  const description = selectedDraft?.description || landingUrl || "落地页描述";
  const cta = copyCtaLabel(selectedDraft?.cta);
  const isReviewed = selectedDraft ? ["approved", "rejected"].includes(selectedDraft.status) : false;
  const canGenerateImages = selectedDraft?.status === "approved";
  const hasFeedback = feedback.trim().length > 0;
  const feedbackSuggestions = ["语气更本地化", "缩短正文", "突出优惠", "CTA 更明确", "降低夸张承诺"];

  return (
    <section className="view-stack copy-workbench">
      <section className="copy-main-grid">
        <section className="panel copy-editor-panel">
          <div className="panel-header">
            <div>
              <h2>当前文案</h2>
              <span className="panel-note">
                {selectedDraft ? `v${selectedDraft.version} · ${statusLabel(selectedDraft.status)}` : "等待生成"}
              </span>
            </div>
            <button
              className="primary-button"
              onClick={onGenerateCopy}
              disabled={!selectedTopic || loading === "copy"}
            >
              {loading === "copy" ? <Loader2 size={16} className="spin" /> : <FileText size={16} />}
              <span>生成文案</span>
            </button>
          </div>

          {selectedDraft ? (
            <div className="copy-current">
              <div className="copy-field-grid">
                <div>
                  <span>广告标题</span>
                  <strong>{headline}</strong>
                </div>
                <div>
                  <span>CTA</span>
                  <strong>{cta}</strong>
                </div>
                <div>
                  <span>描述</span>
                  <strong>{description || "-"}</strong>
                </div>
              </div>

              <article className="copy-body-card">
                <div className="copy-body-head">
                  <span>Facebook 正文</span>
                  <StatusPill status={selectedDraft.status} />
                </div>
                <p className="copy-body-main">{primaryText}</p>
                {hasSeparateFullBody && (
                  <details className="copy-source-details">
                    <summary>查看完整文案</summary>
                    <pre className="copy-source-text">{fullBody}</pre>
                  </details>
                )}
              </article>

              <section className="copy-feedback-card">
                <div className="copy-section-title">
                  <strong>修改意见</strong>
                  <span>需要调整时填写，系统会按意见重新生成新版本</span>
                </div>
                <div className="copy-feedback-tags">
                  {feedbackSuggestions.map((suggestion) => (
                    <button
                      className="copy-feedback-tag"
                      key={suggestion}
                      type="button"
                      onClick={() =>
                        setFeedback(feedback.trim() ? `${feedback.trim()}；${suggestion}` : suggestion)
                      }
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
                <textarea
                  className="feedback-input copy-feedback-input"
                  value={feedback}
                  onChange={(event) => setFeedback(event.target.value)}
                  placeholder="例如：语气更自然一些，突出价格优势，避免过度承诺。"
                />
              </section>

              <div className="copy-actions">
                {!isReviewed ? (
                  <>
                    <button
                      className="primary-button"
                      onClick={() => onReview("copy_draft", selectedDraft.id, "approved")}
                      disabled={loading?.startsWith("review-copy_draft") ?? false}
                    >
                      <Check size={16} />
                      <span>通过文案</span>
                    </button>
                    <button
                      className="secondary-button"
                      onClick={() => onReview("copy_draft", selectedDraft.id, "needs_revision")}
                      disabled={!hasFeedback || (loading?.startsWith("review-copy_draft") ?? false)}
                    >
                      <RefreshCw size={16} />
                      <span>提交修改意见</span>
                    </button>
                    <button
                      className="secondary-button danger"
                      onClick={() => onReview("copy_draft", selectedDraft.id, "rejected")}
                      disabled={loading?.startsWith("review-copy_draft") ?? false}
                    >
                      <X size={16} />
                      <span>拒绝</span>
                    </button>
                  </>
                ) : (
                  <div className={`review-complete copy-review-complete ${selectedDraft.status === "rejected" ? "rejected" : ""}`}>
                    {selectedDraft.status === "approved" ? <Check size={16} /> : <X size={16} />}
                    <span>{selectedDraft.status === "approved" ? "文案已通过审核" : "文案已拒绝"}</span>
                  </div>
                )}
                <button
                  className="secondary-button"
                  onClick={onReviseCopy}
                  disabled={!hasFeedback || loading === "revise-copy"}
                >
                  {loading === "revise-copy" ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />}
                  <span>按意见重新生成</span>
                </button>
                <button
                  className="primary-button"
                  onClick={onGenerateCreatives}
                  disabled={!canGenerateImages}
                  title={canGenerateImages ? "生成图片" : "文案通过后再生成图片"}
                >
                  <Image size={16} />
                  <span>生成图片</span>
                </button>
              </div>
            </div>
          ) : (
            <EmptyState text="暂无文案，请先选择选题并生成文案" />
          )}
        </section>

        <section className="panel copy-preview-panel">
          <div className="panel-header">
            <div>
              <h2>Facebook 预览</h2>
              <span className="panel-note">模拟 Feed 广告展示</span>
            </div>
          </div>
          <div className="facebook-preview-shell">
            <article className="facebook-preview-card">
              <div className="facebook-preview-head">
                <div className="facebook-page-avatar">Ad</div>
                <div>
                  <strong>{selectedCampaign?.product_name || selectedCampaign?.name || "广告主页"}</strong>
                  <span>Sponsored</span>
                </div>
              </div>
              <p className="facebook-preview-text">
                {primaryText || "生成文案后，这里会展示广告正文预览。"}
              </p>
              {previewImage?.url ? (
                <img
                  className="facebook-preview-image"
                  src={previewImage.url}
                  alt={previewImage.alt_text || "广告素材预览"}
                />
              ) : (
                <div className="facebook-preview-empty">
                  <Image size={26} />
                  <span>图片生成后会出现在这里</span>
                </div>
              )}
              <div className="facebook-preview-footer">
                <div>
                  <span>{landingUrl ? safeHostname(landingUrl) : "landing page"}</span>
                  <strong>{headline}</strong>
                  <p>{description}</p>
                </div>
                <button type="button">{cta}</button>
              </div>
            </article>
          </div>
        </section>
      </section>

      <details className="copy-history-details">
        <summary>
          <span>版本记录</span>
          <small>{drafts.length ? `${drafts.length} 个版本` : "暂无版本"}</small>
        </summary>
        <DataList emptyText="暂无文案版本">
          {drafts.map((draft) => (
            <button
              className={`list-button copy-version-button ${selectedDraft?.id === draft.id ? "active" : ""}`}
              key={draft.id}
              onClick={() => setSelectedDraftId(draft.id)}
            >
              <strong>{draft.headline || `版本 ${draft.version}`}</strong>
              <span>{statusLabel(draft.status)} · v{draft.version} · {formatDate(draft.created_at)}</span>
            </button>
          ))}
        </DataList>
      </details>
    </section>
  );
}

function copyCtaLabel(value: string | null | undefined): string {
  const normalized = (value || "LEARN_MORE").trim();
  const labels: Record<string, string> = {
    LEARN_MORE: "了解更多",
    SHOP_NOW: "立即购买",
    SIGN_UP: "立即注册",
    CONTACT_US: "联系我们",
    DOWNLOAD: "下载",
    SUBSCRIBE: "订阅",
    APPLY_NOW: "立即申请",
    GET_OFFER: "领取优惠",
  };
  return labels[normalized.toUpperCase()] ?? normalized;
}

function safeHostname(value: string): string {
  try {
    return new URL(value).hostname;
  } catch {
    return value;
  }
}

function CreativesView({
  currentCreatives,
  historicalCreatives,
  selectedCreativeIds,
  setSelectedCreativeIds,
  onGenerate,
  onReview,
  onCreateVideo,
  onInvalidImage,
  generation,
}: {
  currentCreatives: CreativeAsset[];
  historicalCreatives: CreativeAsset[];
  selectedCreativeIds: string[];
  setSelectedCreativeIds: (ids: string[]) => void;
  onGenerate: () => void;
  onReview: (entityType: string, entityId: string, decision: "approved" | "rejected" | "needs_revision") => void;
  onCreateVideo: () => void;
  onInvalidImage: (assetId: string) => void;
  generation: CreativeGenerationState | null;
}) {
  const [previewAsset, setPreviewAsset] = useState<CreativeAsset | null>(null);
  const isGenerating = isCreativeGenerationActive(generation);
  const hasCurrentCreativeContent = isGenerating || currentCreatives.length > 0;

  function renderCreativeAsset(asset: CreativeAsset) {
    const checked = selectedCreativeIds.includes(asset.id);
    return (
      <article className="asset-item" key={asset.id}>
        <div className="asset-card-head">
          <label className="check-row compact">
            <input
              type="checkbox"
              checked={checked}
              onChange={(event) => {
                setSelectedCreativeIds(
                  event.target.checked
                    ? [...selectedCreativeIds, asset.id]
                    : selectedCreativeIds.filter((id) => id !== asset.id),
                );
              }}
            />
            <span>用于视频</span>
          </label>
          <StatusPill status={asset.status} />
        </div>
        <ImagePreview asset={asset} onInvalid={onInvalidImage} onOpen={() => setPreviewAsset(asset)} />
        <div className="asset-summary">
          <strong>{asset.alt_text || imagePromptTitle(asset.prompt) || `图片 ${asset.version}`}</strong>
          <span>{checked ? "已选为视频素材" : "未选为视频素材"}</span>
        </div>
        {!isReviewedStatus(asset.status) ? (
          <div className="button-row asset-actions">
            <button className="secondary-button" onClick={() => onReview("creative_asset", asset.id, "approved")}>
              <Check size={16} />
              <span>通过</span>
            </button>
            <button className="secondary-button danger" onClick={() => onReview("creative_asset", asset.id, "rejected")}>
              <X size={16} />
              <span>拒绝</span>
            </button>
          </div>
        ) : (
          <div className="review-complete">
            {asset.status === "approved" ? <Check size={16} /> : <X size={16} />}
            <span>{asset.status === "approved" ? "已通过，可用于视频/发布" : "已拒绝"}</span>
          </div>
        )}
        <details className="asset-details">
          <summary>详情</summary>
          <div className="asset-details-body">
            {asset.url && (
              <a className="asset-url" href={asset.url} target="_blank" rel="noreferrer">
                打开图片 URL
              </a>
            )}
            <div className="asset-prompt">{asset.prompt}</div>
          </div>
        </details>
      </article>
    );
  }

  return (
    <section className="view-stack">
      <section className="panel">
        <div className="panel-header">
          <h2>图片素材</h2>
          <div className="button-row">
            <button className="primary-button" onClick={onGenerate} disabled={isGenerating}>
              {isGenerating ? <Loader2 size={16} className="spin" /> : <Image size={16} />}
              <span>{isGenerating ? "生成中" : "生成图片"}</span>
            </button>
            <button className="secondary-button" onClick={onCreateVideo} disabled={!selectedCreativeIds.length}>
              <Film size={16} />
              <span>创建视频任务</span>
            </button>
          </div>
        </div>
        {generation && <CreativeGenerationPanel generation={generation} />}
        <section className="creative-batch-section">
          <div className="creative-batch-head">
            <div>
              <h3>本次生成</h3>
              <span>
                {isGenerating
                  ? "正在生成本轮 3 张图片"
                  : currentCreatives.length
                    ? "优先审核这组图片，满意后可直接用于视频"
                    : "点击生成图片后，本轮结果会显示在这里"}
              </span>
            </div>
            <span className="creative-count">{isGenerating ? "生成中" : `${currentCreatives.length} 张`}</span>
          </div>
          <div className="asset-grid">
            {isGenerating &&
              Array.from({ length: generation?.expectedCount ?? 3 }).map((_, index) => (
                <CreativeGeneratingCard key={`creative-generating-${generation?.startedAt}-${index}`} index={index} />
              ))}
            {currentCreatives.map(renderCreativeAsset)}
          </div>
          {!hasCurrentCreativeContent && <EmptyState text="暂无本次生成图片" />}
        </section>
        {historicalCreatives.length > 0 && (
          <details className="creative-history-section">
            <summary>
              <div>
                <strong>历史图片</strong>
                <span>之前生成的素材保留在这里，可展开复用或审核。</span>
              </div>
              <span>{historicalCreatives.length} 张</span>
            </summary>
            <div className="asset-grid history-grid">{historicalCreatives.map(renderCreativeAsset)}</div>
          </details>
        )}
      </section>
      <ImagePreviewDialog asset={previewAsset} onClose={() => setPreviewAsset(null)} />
    </section>
  );
}

function CreativeGenerationPanel({ generation }: { generation: CreativeGenerationState }) {
  const steps: Array<{ phase: CreativeGenerationPhase; label: string }> = [
    { phase: "submitting", label: "提交请求" },
    { phase: "generating", label: "生成图片" },
    { phase: "saving", label: "更新列表" },
    { phase: "done", label: "完成" },
  ];
  const activeStepIndex = Math.max(
    0,
    steps.findIndex((step) => step.phase === generation.phase),
  );
  const status = generation.phase === "failed" ? "failed" : isCreativeGenerationActive(generation) ? "generating" : "approved";

  return (
    <div className={`creative-progress ${generation.phase}`}>
      <div className="creative-progress-head">
        <div className="creative-progress-icon">
          {generation.phase === "failed" ? (
            <X size={18} />
          ) : generation.phase === "done" ? (
            <Check size={18} />
          ) : (
            <Loader2 size={18} className="spin" />
          )}
        </div>
        <div>
          <strong>{generation.message}</strong>
          <span>{generation.detail}</span>
        </div>
        <StatusPill status={status} />
      </div>
      <div className="creative-progress-meta">
        <span>预计 {generation.expectedCount} 张</span>
        <span>完成 {generation.completedCount} 张</span>
        <span>用时 {formatElapsedSeconds(generation.elapsedSeconds)}</span>
      </div>
      <div className="creative-progress-steps">
        {steps.map((step, index) => {
          const stepState =
            generation.phase === "failed" && index === activeStepIndex
              ? "failed"
              : generation.phase === "done" || index < activeStepIndex
                ? "done"
                : index === activeStepIndex
                  ? "active"
                  : "pending";
          return (
            <div className={`creative-progress-step ${stepState}`} key={step.phase}>
              <span>{index + 1}</span>
              <strong>{step.label}</strong>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CreativeGeneratingCard({ index }: { index: number }) {
  return (
    <article className="asset-item creative-skeleton-card" aria-busy="true">
      <div className="asset-card-head">
        <span className="creative-skeleton-label">第 {index + 1} 张</span>
        <StatusPill status="generating" />
      </div>
      <div className="creative-skeleton-image">
        <Loader2 size={24} className="spin" />
      </div>
      <div className="asset-summary">
        <strong>图片生成中</strong>
        <span>生成完成后会自动出现在列表顶部</span>
      </div>
      <div className="creative-skeleton-lines">
        <span />
        <span />
      </div>
    </article>
  );
}

function VideosView({
  videos,
  creatives,
  selectedCreativeIds,
  setSelectedCreativeIds,
  aspectRatio,
  setAspectRatio,
  durationSeconds,
  setDurationSeconds,
  instructions,
  setInstructions,
  storyboardText,
  setStoryboardText,
  onGenerateStoryboard,
  onCreateVideo,
  onStartGeneration,
  onRefreshGeneration,
  onReview,
  loading,
}: {
  videos: VideoAsset[];
  creatives: CreativeAsset[];
  selectedCreativeIds: string[];
  setSelectedCreativeIds: (ids: string[]) => void;
  aspectRatio: string;
  setAspectRatio: (value: string) => void;
  durationSeconds: number;
  setDurationSeconds: (value: number) => void;
  instructions: string;
  setInstructions: (value: string) => void;
  storyboardText: string;
  setStoryboardText: (value: string) => void;
  onGenerateStoryboard: () => void;
  onCreateVideo: () => void;
  onStartGeneration: (videoId: string) => void;
  onRefreshGeneration: (videoId: string) => void;
  onReview: (entityType: string, entityId: string, decision: "approved" | "rejected" | "needs_revision") => void;
  loading: string | null;
}) {
  const selectedTooManyImages = selectedCreativeIds.length > VIDEO_MAX_REFERENCE_IMAGES;
  const sortedVideos = [...videos].sort(
    (left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at),
  );
  const currentVideo = sortedVideos[0] ?? null;
  const historyVideos = sortedVideos.slice(1);

  useEffect(() => {
    if (selectedCreativeIds.length > VIDEO_MAX_REFERENCE_IMAGES) {
      setSelectedCreativeIds(selectedCreativeIds.slice(0, VIDEO_MAX_REFERENCE_IMAGES));
    }
  }, [selectedCreativeIds, setSelectedCreativeIds]);

  function renderVideoTaskCard(video: VideoAsset, variant: "current" | "history" = "history") {
    const generateKey = `video-generate-${video.id}`;
    const refreshKey = `video-refresh-${video.id}`;
    const providerStarted = Boolean(video.provider_job_id);
    const durationSupported =
      !video.duration_seconds || (video.duration_seconds >= 4 && video.duration_seconds <= 12);
    const referenceImageCount = video.source_asset_ids.length;
    const referenceImageCountSupported = referenceImageCount <= VIDEO_MAX_REFERENCE_IMAGES;
    const canSubmitProvider =
      (!providerStarted || video.status === "failed") &&
      referenceImageCountSupported &&
      video.status !== "rejected" &&
      video.status !== "generating" &&
      video.status !== "generated";
    const canReviewVideo =
      !isReviewedStatus(video.status) &&
      Boolean(video.url) &&
      video.status !== "generating";

    return (
      <article className={`video-item ${variant === "current" ? "current" : "compact"}`} key={video.id}>
        <div className="item-head">
          <div>
            <strong>{variant === "current" ? "当前视频任务" : `${video.aspect_ratio} · ${video.duration_seconds ?? "-"}s`}</strong>
            <span className="item-subtitle">
              {video.aspect_ratio} · {video.duration_seconds ?? "-"}s · {referenceImageCount} 张图片
            </span>
          </div>
          <StatusPill status={video.status} />
        </div>

        <div className="video-task-summary">
          <div>
            <span>生成</span>
            <strong>{providerStarted ? "已提交 Seedance" : "未开始"}</strong>
          </div>
          <div>
            <span>审核</span>
            <strong>{videoReviewLabel(video)}</strong>
          </div>
          <div>
            <span>视频</span>
            <strong>{video.url ? "已获取链接" : videoTaskStateLabel(video)}</strong>
          </div>
        </div>

        {!referenceImageCountSupported && (
          <div className="inline-warning">
            这条任务包含 {referenceImageCount} 张图片，Seedance 1.5 pro 当前最多支持{" "}
            {VIDEO_MAX_REFERENCE_IMAGES} 张首尾帧图片。请重新选择图片并创建视频任务。
          </div>
        )}
        {!durationSupported && (
          <div className="inline-warning">
            Doubao-Seedance-1.5-pro 当前支持 4-12 秒，请重新创建 6 秒、10 秒或 12 秒任务。
          </div>
        )}
        {video.error_message && <div className="inline-error">{video.error_message}</div>}
        {video.url && <VideoPreview url={video.url} />}
        <div className="video-task-actions">
          {canSubmitProvider && (
            <button
              className="primary-button"
              onClick={() => onStartGeneration(video.id)}
              disabled={!durationSupported || loading === generateKey}
            >
              {loading === generateKey ? <Loader2 size={16} className="spin" /> : <Film size={16} />}
              <span>{providerStarted ? "重新生成视频" : "开始生成视频"}</span>
            </button>
          )}
          {providerStarted && (
            <button
              className="secondary-button"
              onClick={() => onRefreshGeneration(video.id)}
              disabled={loading === refreshKey}
            >
              {loading === refreshKey ? <Loader2 size={16} className="spin" /> : <RefreshCw size={16} />}
              <span>刷新状态</span>
            </button>
          )}
          {canReviewVideo && (
            <>
              <button
                className="secondary-button"
                onClick={() => onReview("video_asset", video.id, "approved")}
              >
                <Check size={16} />
                <span>通过</span>
              </button>
              <button
                className="secondary-button danger"
                onClick={() => onReview("video_asset", video.id, "rejected")}
              >
                <X size={16} />
                <span>拒绝</span>
              </button>
            </>
          )}
        </div>

        <details className="video-technical-details">
          <summary>技术详情</summary>
          <div className="video-technical-body">
            <div className="video-meta-grid">
              <div>
                <span>任务 ID</span>
                <code>{shortId(video.id)}</code>
              </div>
              <div>
                <span>上游任务 ID</span>
                <code>{video.provider_job_id || "-"}</code>
              </div>
              <div>
                <span>素材 ID</span>
                <code>{video.source_asset_ids.length ? video.source_asset_ids.map(shortId).join(", ") : "-"}</code>
              </div>
            </div>
            {video.prompt && <p className="video-prompt-muted">{video.prompt}</p>}
            {video.storyboard.length > 0 && (
              <pre className="video-storyboard-preview">
                {formatStoryboard(video.storyboard as Record<string, unknown>[])}
              </pre>
            )}
          </div>
        </details>
      </article>
    );
  }

  return (
    <section className="two-column video-layout">
      <section className="panel wide">
        <div className="panel-header">
          <div>
            <h2>视频任务配置</h2>
            <span className="panel-note">先生成脚本，再创建任务；开始生成前会再次确认</span>
          </div>
          <div className="button-row">
            <button
              className="secondary-button"
              onClick={onGenerateStoryboard}
              disabled={!selectedCreativeIds.length || selectedTooManyImages || loading === "video-storyboard"}
            >
              {loading === "video-storyboard" ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />}
              <span>AI 生成视频脚本</span>
            </button>
            <button
              className="primary-button"
              onClick={onCreateVideo}
              disabled={
                !selectedCreativeIds.length ||
                selectedTooManyImages ||
                !storyboardText.trim() ||
                loading === "video"
              }
            >
              {loading === "video" ? <Loader2 size={16} className="spin" /> : <Film size={16} />}
              <span>创建任务</span>
            </button>
          </div>
        </div>

        <div className="video-config">
          <div className="config-group">
            <label>视频比例</label>
            <div className="segmented-control">
              {["9:16", "1:1", "16:9"].map((value) => (
                <button
                  key={value}
                  className={aspectRatio === value ? "active" : ""}
                  onClick={() => setAspectRatio(value)}
                >
                  {value}
                </button>
              ))}
            </div>
          </div>

          <div className="config-group">
            <label>视频时长</label>
            <div className="segmented-control">
              {[6, 10, 12].map((value) => (
                <button
                  key={value}
                  className={durationSeconds === value ? "active" : ""}
                  onClick={() => setDurationSeconds(value)}
                >
                  {value} 秒
                </button>
              ))}
            </div>
            <span className="field-hint">Doubao-Seedance-1.5-pro 当前支持 4-12 秒。</span>
          </div>

          <div className="config-group full">
            <label>使用图片</label>
            <div className="source-asset-list">
              {creatives.map((asset) => (
                <label className="list-check" key={asset.id}>
                  <input
                    type="checkbox"
                    checked={selectedCreativeIds.includes(asset.id)}
                    onChange={(event) => {
                      if (event.target.checked) {
                        if (selectedCreativeIds.length >= VIDEO_MAX_REFERENCE_IMAGES) return;
                        setSelectedCreativeIds([...selectedCreativeIds, asset.id]);
                        return;
                      }
                      setSelectedCreativeIds(selectedCreativeIds.filter((id) => id !== asset.id));
                    }}
                  />
                  <span>{asset.alt_text || asset.prompt || asset.id}</span>
                  <StatusPill status={asset.status} />
                </label>
              ))}
              {!creatives.length && <EmptyState text="暂无图片" />}
            </div>
            <span className={selectedTooManyImages ? "field-hint warning-text" : "field-hint"}>
              已选择 {selectedCreativeIds.length} 张，Seedance 1.5 pro 当前最多支持{" "}
              {VIDEO_MAX_REFERENCE_IMAGES} 张首尾帧图片。
            </span>
          </div>

          <div className="config-group full">
            <label>补充要求</label>
            <textarea
              className="video-instructions"
              value={instructions}
              onChange={(event) => setInstructions(event.target.value)}
              placeholder="可选，例如：节奏快一点，字幕用英文，最后 2 秒突出 Download Now"
            />
          </div>

          <div className="config-group full">
            <details className="storyboard-editor-details" open={!storyboardText.trim() || undefined}>
              <summary>
                <span>视频脚本</span>
                <small>{storyboardText.trim() ? "已生成，可展开微调" : "先生成脚本，再创建任务"}</small>
              </summary>
              <textarea
                className="storyboard-input"
                value={storyboardText}
                onChange={(event) => setStoryboardText(event.target.value)}
                placeholder="点击 AI 生成视频脚本，或在这里手动填写每个镜头要展示什么。"
              />
            </details>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2>视频任务</h2>
        </div>
        {!currentVideo && <EmptyState text="暂无视频任务" />}
        {currentVideo && (
          <div className="video-task-stack">
            {renderVideoTaskCard(currentVideo, "current")}
            {historyVideos.length > 0 && (
              <details className="history-details">
                <summary>历史视频任务（{historyVideos.length}）</summary>
                <DataList emptyText="暂无历史任务">
                  {historyVideos.map((video) => renderVideoTaskCard(video, "history"))}
                </DataList>
              </details>
            )}
          </div>
        )}
      </section>
    </section>
  );
}

function PublishingView({
  publishJobs,
  facebookConfig,
  metaAccounts,
  selectedMetaAccountId,
  setSelectedMetaAccountId,
  adCreativeDraft,
  adsPlanDraft,
  metaAdsDraftResult,
  selectedCampaign,
  selectedDraft,
  publishMessage,
  setPublishMessage,
  publishImageUrl,
  setPublishImageUrl,
  publishVideoAssetId,
  setPublishVideoAssetId,
  publishChannel,
  setPublishChannel,
  publishMediaType,
  setPublishMediaType,
  publishPageId,
  setPublishPageId,
  publishAdAccountId,
  setPublishAdAccountId,
  metaDailyBudget,
  setMetaDailyBudget,
  metaPixelId,
  setMetaPixelId,
  metaPixels,
  metaPixelError,
  creatives,
  videos,
  onCreateJob,
  onConnectMetaAccount,
  onRefreshMetaAccounts,
  onRefreshMetaPixels,
  onBuildAdCreativeDraft,
  onBuildAdsPlanDraft,
  onPrepareMetaAdsPackage,
  onReview,
  onPublish,
  onSyncMetaStatus,
  onSyncMetaInsights,
  onActivateMetaAds,
  onPauseMetaAds,
  loading,
}: {
  publishJobs: PublishJob[];
  facebookConfig: FacebookPublishConfig | null;
  metaAccounts: MetaAccount[];
  selectedMetaAccountId: string;
  setSelectedMetaAccountId: (value: string) => void;
  adCreativeDraft: AdCreativeDraft | null;
  adsPlanDraft: AdsPlanDraft | null;
  metaAdsDraftResult: MetaAdsDraftCreateResult | null;
  selectedCampaign: Campaign | null;
  selectedDraft: CopyDraft | null;
  publishMessage: string;
  setPublishMessage: (value: string) => void;
  publishImageUrl: string;
  setPublishImageUrl: (value: string) => void;
  publishVideoAssetId: string;
  setPublishVideoAssetId: (value: string) => void;
  publishChannel: PublishChannelKey;
  setPublishChannel: (value: PublishChannelKey) => void;
  publishMediaType: PublishMediaType;
  setPublishMediaType: (value: PublishMediaType) => void;
  publishPageId: string;
  setPublishPageId: (value: string) => void;
  publishAdAccountId: string;
  setPublishAdAccountId: (value: string) => void;
  metaDailyBudget: string;
  setMetaDailyBudget: (value: string) => void;
  metaPixelId: string;
  setMetaPixelId: (value: string) => void;
  metaPixels: AdPixel[];
  metaPixelError: string;
  creatives: CreativeAsset[];
  videos: VideoAsset[];
  onCreateJob: () => void;
  onConnectMetaAccount: () => void;
  onRefreshMetaAccounts: () => void;
  onRefreshMetaPixels: () => void;
  onBuildAdCreativeDraft: () => void;
  onBuildAdsPlanDraft: () => void;
  onPrepareMetaAdsPackage: (preflightItems: PreflightChecklistItem[]) => void;
  onReview: (entityType: string, entityId: string, decision: "approved" | "rejected" | "needs_revision") => void;
  onPublish: (jobId: string) => void;
  onSyncMetaStatus: (jobId: string) => void;
  onSyncMetaInsights: (jobId: string) => void;
  onActivateMetaAds: (jobId: string) => void;
  onPauseMetaAds: (jobId: string) => void;
  loading: string | null;
}) {
  const [selectedPublishJobId, setSelectedPublishJobId] = useState<string | null>(null);
  const [publishJobFilter, setPublishJobFilter] = useState<PublishJobFilter>("all");
  const selectedVideo = videos.find((video) => video.id === publishVideoAssetId) ?? null;
  const selectedVideoReady = Boolean(selectedVideo && isPublishableVideo(selectedVideo));
  const selectedPublishableImage = creatives.find((asset) => asset.url === publishImageUrl) ?? null;
  const selectedMetaAccount = metaAccounts.find((item) => item.id === selectedMetaAccountId) ?? null;
  const metaIdentityOptions = buildMetaIdentityOptions(metaAccounts, facebookConfig);
  const selectedMetaIdentityKey = selectedMetaAccount ? metaIdentityKey(selectedMetaAccount) : "";
  const selectedMetaIdentity =
    metaIdentityOptions.find((item) => item.key === selectedMetaIdentityKey) ?? null;
  const selectedPageOption =
    selectedMetaIdentity?.pages.find((item) => item.id === publishPageId) ?? null;
  const selectedAdAccountOption =
    selectedMetaIdentity?.adAccounts.find((item) => item.id === normalizeMetaAdAccountId(publishAdAccountId)) ??
    null;
  const identityHealth = metaAccountIdentityHealth(
    selectedMetaAccount,
    facebookConfig,
    publishPageId,
    publishAdAccountId,
    selectedPageOption,
    selectedAdAccountOption,
  );
  const identitySummary = metaAccountIdentitySummary(
    selectedMetaAccount,
    facebookConfig,
    selectedPageOption,
    selectedAdAccountOption,
    selectedMetaIdentity,
  );
  const message = publishMessage || selectedDraft?.primary_text || selectedDraft?.body || "";
  const accessTokenRef =
    publishChannel === "facebook_page"
      ? facebookConfig?.page.access_token_ref
      : facebookConfig?.ads.access_token_ref;
  const isPageDryRunMode = facebookConfig?.dry_run ?? true;
  const isAdsDryRunMode = facebookConfig?.ads.dry_run ?? isPageDryRunMode;
  const isDryRunMode = publishChannel === "facebook_ad" ? isAdsDryRunMode : isPageDryRunMode;
  const selectedMetaAccountReady = Boolean(
    selectedMetaAccount &&
      publishAdAccountId &&
      publishPageId &&
      selectedMetaAccount.access_token_configured,
  );
  const activeCredentialReady =
    publishChannel === "facebook_page"
      ? Boolean(
          (selectedMetaAccount && publishPageId && selectedMetaAccount.page_access_token_configured) ||
            (facebookConfig?.page.id_configured && facebookConfig.page.access_token_configured),
        )
      : Boolean(
          selectedMetaAccountReady ||
            (facebookConfig?.ads.ad_account_configured && facebookConfig.ads.access_token_configured),
        );
  const mediaReady =
    (publishMediaType === "image" && Boolean(selectedPublishableImage)) ||
    (publishMediaType === "video" && selectedVideoReady);
  const credentialReadyForMode = isDryRunMode ? true : activeCredentialReady;
  const canCreate = Boolean(selectedCampaign && message.trim() && mediaReady && credentialReadyForMode);
  const metaDailyBudgetValue = parseOptionalInteger(metaDailyBudget);
  const metaAdsCredentialReady = Boolean(
    selectedMetaAccountReady ||
      (facebookConfig?.ads.ad_account_configured &&
        facebookConfig.ads.access_token_configured &&
        facebookConfig.page.id_configured),
  );
  const canCreateMetaAds = Boolean(
    selectedCampaign &&
      selectedDraft &&
      mediaReady &&
      metaDailyBudgetValue &&
      metaAdsCredentialReady,
  );
  const filteredPublishJobs = publishJobs.filter(
    (job) => publishJobFilter === "all" || publishJobBucket(job) === publishJobFilter,
  );
  const selectedPublishJob =
    publishJobs.find((job) => job.id === selectedPublishJobId) ?? filteredPublishJobs[0] ?? publishJobs[0] ?? null;
  const latestMetaAdsJob =
    publishJobs
      .filter((job) => job.campaign_id === selectedCampaign?.id && isMetaAdsPackageJob(job))
      .sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at))[0] ?? null;
  const landingUrl =
    landingUrlFromCampaign(selectedCampaign) || adCreativeDraft?.destination_url || adsPlanDraft?.destination_url || "";
  const preflightItems = buildPreflightChecklist({
    landingUrl,
    selectedCampaign,
    selectedDraft,
    message,
    publishChannel,
    publishMediaType,
    pageId: publishPageId || selectedMetaAccount?.page_id || facebookConfig?.page.id || "",
    adAccountId: publishAdAccountId || selectedMetaAccount?.ad_account_id || facebookConfig?.ads.ad_account_id || "",
    mediaReady,
    selectedImage: selectedPublishableImage,
    selectedVideo,
    selectedVideoReady,
    metaDailyBudgetValue,
    metaAdsCredentialReady,
    latestMetaAdsJob,
    metaAdsDraftResult,
    isDryRunMode,
  });
  const hasBlockingPreflightItems = preflightItems.some((item) => item.status === "error");
  const canPrepareMetaAdsPackage = canCreateMetaAds && !hasBlockingPreflightItems;

  function handleMetaIdentityChange(identityKey: string) {
    if (!identityKey) {
      setSelectedMetaAccountId("");
      if (facebookConfig?.page.id) setPublishPageId(facebookConfig.page.id);
      if (facebookConfig?.ads.ad_account_id) {
        setPublishAdAccountId(normalizeMetaAdAccountId(facebookConfig.ads.ad_account_id));
      }
      return;
    }
    const identity = metaIdentityOptions.find((item) => item.key === identityKey);
    const account = identity?.accounts[0];
    if (!identity || !account) return;
    setSelectedMetaAccountId(account.id);
    setPublishPageId(identity.pages[0]?.id || account.page_id || "");
    setPublishAdAccountId(identity.adAccounts[0]?.id || normalizeMetaAdAccountId(account.ad_account_id) || "");
  }

  function handleMetaPageChange(pageId: string) {
    setPublishPageId(pageId);
  }

  function handleMetaAdAccountChange(adAccountId: string) {
    const normalizedAdAccountId = normalizeMetaAdAccountId(adAccountId);
    const option = selectedMetaIdentity?.adAccounts.find((item) => item.id === normalizedAdAccountId);
    if (option?.accountId) {
      setSelectedMetaAccountId(option.accountId);
    }
    setPublishAdAccountId(normalizedAdAccountId);
  }

  useEffect(() => {
    if (!publishJobs.length) {
      if (selectedPublishJobId) setSelectedPublishJobId(null);
      return;
    }
    if (!selectedPublishJobId || !publishJobs.some((job) => job.id === selectedPublishJobId)) {
      setSelectedPublishJobId(filteredPublishJobs[0]?.id ?? publishJobs[0].id);
    }
  }, [filteredPublishJobs, publishJobs, selectedPublishJobId]);

  const previewPayload = {
    campaign_id: selectedCampaign?.id ?? null,
    facebook_account_id: selectedMetaAccountId || null,
    draft_id: selectedDraft?.id ?? null,
    channel: publishChannel,
    payload: {
      media_type: publishMediaType,
      page_id: publishPageId || "dry-run-page",
      ad_account_id: publishAdAccountId || "dry-run-ad-account",
      message,
      access_token_ref: accessTokenRef ?? null,
      ...(publishMediaType === "image" && selectedPublishableImage
        ? { image_url: selectedPublishableImage.url ?? undefined }
        : {}),
      ...(publishMediaType === "video" && publishVideoAssetId
        ? {
            video_asset_id: publishVideoAssetId,
            video_url_preview: selectedVideo?.url ?? null,
            video_upload_mode: metaVideoId(selectedVideo) ? "meta_video_id" : "local_file",
          }
        : {}),
    },
  };

  return (
    <section className="two-column publishing-layout">
      <section className="panel wide">
        <div className="panel-header">
          <div>
            <h2>创建发布任务</h2>
            <span className="panel-note">先准备 dry-run payload，不会真实发布到 Meta</span>
          </div>
          <button className="primary-button" onClick={onCreateJob} disabled={!canCreate || loading === "publish-create"}>
            {loading === "publish-create" ? <Loader2 size={16} className="spin" /> : <Send size={16} />}
            <span>创建任务</span>
          </button>
        </div>

        <div className="publish-form">
          <div className={isDryRunMode ? "meta-config-card dry-run" : "meta-config-card live"}>
            <div>
              <strong>{isDryRunMode ? "当前为 dry-run 模式" : "当前为真实发布模式"}</strong>
              <span>
                Graph API {facebookConfig?.graph_api_version ?? "-"} /{" "}
                {activeCredentialReady ? "当前渠道配置完整" : "当前渠道配置未完整"}
              </span>
            </div>
            <div className="meta-config-grid">
              <span>App ID：{facebookConfig?.app.app_id_configured ? "已配置" : "未配置"}</span>
              <span>
                App Secret：{facebookConfig?.app.app_secret_configured ? "已配置" : "未配置"}
              </span>
              <span>Page ID：{facebookConfig?.page.id_configured ? "已配置" : "未配置"}</span>
              <span>Page Token：{facebookConfig?.page.access_token_configured ? "已配置" : "未配置"}</span>
              <span>
                Ad Account：{facebookConfig?.ads.ad_account_configured ? "已配置" : "未配置"}
              </span>
              <span>Ad Token：{facebookConfig?.ads.access_token_configured ? "已配置" : "未配置"}</span>
            </div>
          </div>

          <div className={`oauth-account-panel identity-card state-${identityHealth.status}`}>
            <div className="identity-card-head">
              <div>
                <strong>Meta 投放身份</strong>
                <span>{identitySummary.subtitle}</span>
              </div>
              <span className={`identity-status state-${identityHealth.status}`}>{identityHealth.label}</span>
            </div>

            <div className="identity-details-grid">
              <div>
                <span>Meta 账号</span>
                <strong>{identitySummary.accountName}</strong>
              </div>
              <div>
                <span>公共主页 Page</span>
                <strong>{identitySummary.pageLabel}</strong>
              </div>
              <div>
                <span>广告账户 Ad Account</span>
                <strong>{identitySummary.adAccountLabel}</strong>
              </div>
              <div>
                <span>Business / BM</span>
                <strong>{identitySummary.businessLabel}</strong>
              </div>
              <div>
                <span>Token</span>
                <strong>{identitySummary.tokenLabel}</strong>
              </div>
              <div>
                <span>有效期</span>
                <strong>{identitySummary.expiryLabel}</strong>
              </div>
            </div>

            {identityHealth.notes.length > 0 && (
              <div className="identity-note-list">
                {identityHealth.notes.map((note) => (
                  <span key={note}>{note}</span>
                ))}
              </div>
            )}

            <div className="identity-picker-grid">
              <label>
                <span>授权身份</span>
                <select
                  className="select publish-select"
                  value={selectedMetaIdentityKey}
                  onChange={(event) => handleMetaIdentityChange(event.target.value)}
                >
                  <option value="">使用 .env 默认账号 / {metaAccountEnvOptionLabel(facebookConfig)}</option>
                  {metaIdentityOptions.map((identity) => (
                    <option key={identity.key} value={identity.key}>
                      {identity.label} / {identity.subtitle}
                    </option>
                  ))}
                </select>
              </label>

              <label>
                <span>公共主页 Page</span>
                <select
                  className="select publish-select"
                  value={publishPageId}
                  onChange={(event) => handleMetaPageChange(event.target.value)}
                  disabled={!selectedMetaIdentity && !facebookConfig?.page.id}
                >
                  {!selectedMetaIdentity ? (
                    <option value={facebookConfig?.page.id ?? ""}>
                      {facebookConfig?.page.id
                        ? `使用 .env Page / ${facebookConfig.page.id}`
                        : "未配置 Page"}
                    </option>
                  ) : selectedMetaIdentity.pages.length ? (
                    selectedMetaIdentity.pages.map((page) => (
                      <option key={page.id} value={page.id}>
                        {page.label}
                      </option>
                    ))
                  ) : (
                    <option value="">未找到可用 Page</option>
                  )}
                </select>
              </label>

              <label>
                <span>广告账户 Ad Account</span>
                <select
                  className="select publish-select"
                  value={normalizeMetaAdAccountId(publishAdAccountId)}
                  onChange={(event) => handleMetaAdAccountChange(event.target.value)}
                  disabled={!selectedMetaIdentity && !facebookConfig?.ads.ad_account_id}
                >
                  {!selectedMetaIdentity ? (
                    <option value={normalizeMetaAdAccountId(facebookConfig?.ads.ad_account_id)}>
                      {facebookConfig?.ads.ad_account_id
                        ? `使用 .env Ad Account / ${normalizeMetaAdAccountId(facebookConfig.ads.ad_account_id)}`
                        : "未配置 Ad Account"}
                    </option>
                  ) : selectedMetaIdentity.adAccounts.length ? (
                    selectedMetaIdentity.adAccounts.map((adAccount) => (
                      <option key={adAccount.id} value={adAccount.id}>
                        {adAccount.label}
                      </option>
                    ))
                  ) : (
                    <option value="">未找到可用 Ad Account</option>
                  )}
                </select>
              </label>
            </div>

            <div className="oauth-actions identity-actions">
              <button
                className="secondary-button"
                onClick={onRefreshMetaAccounts}
                disabled={loading === "refresh"}
              >
                {loading === "refresh" ? <Loader2 size={16} className="spin" /> : <RefreshCw size={16} />}
                <span>刷新账号资产</span>
              </button>
              <button
                className="secondary-button"
                onClick={onConnectMetaAccount}
                disabled={loading === "meta-oauth"}
              >
                {loading === "meta-oauth" ? <Loader2 size={16} className="spin" /> : <Megaphone size={16} />}
                <span>{selectedMetaAccount ? "重新授权" : "连接 Meta 账号"}</span>
              </button>
            </div>
          </div>

          <div className="publish-readiness-grid">
            <div className={`readiness-item ${selectedCampaign ? "ready" : "missing"}`}>
              <span>项目</span>
              <strong>{selectedCampaign?.name || "未选择"}</strong>
            </div>
            <div className={`readiness-item ${selectedDraft ? "ready" : "missing"}`}>
              <span>文案</span>
              <strong>{selectedDraft ? statusLabel(selectedDraft.status) : "未选择"}</strong>
            </div>
            <div className={`readiness-item ${mediaReady ? "ready" : "missing"}`}>
              <span>素材</span>
              <strong>{publishMediaTypeLabel(publishMediaType)}</strong>
            </div>
            <div className={`readiness-item ${credentialReadyForMode ? "ready" : "missing"}`}>
              <span>账号</span>
              <strong>{credentialReadyForMode ? "可创建任务" : "配置不完整"}</strong>
            </div>
          </div>

          <PreflightChecklist items={preflightItems} />

          <div className="publish-primary-grid">
            <div className="publish-main-fields">
              <div className="publish-section-title">
                <strong>日常发布信息</strong>
                <span>选择渠道、素材和正文，确认后再创建任务。</span>
              </div>

          <div className="publish-controls">
            <div className="config-group">
              <label>发布渠道</label>
              <div className="segmented-control two">
                {(["facebook_page", "facebook_ad"] as PublishChannelKey[]).map((value) => (
                  <button
                    key={value}
                    className={publishChannel === value ? "active" : ""}
                    onClick={() => setPublishChannel(value)}
                  >
                    {publishChannelLabel(value)}
                  </button>
                ))}
              </div>
            </div>

            <div className="config-group">
              <label>素材类型</label>
              <div className="segmented-control">
                {(["image", "video"] as PublishMediaType[]).map((value) => (
                  <button
                    key={value}
                    className={publishMediaType === value ? "active" : ""}
                    onClick={() => setPublishMediaType(value)}
                    disabled={
                      (value === "image" && !creatives.length) ||
                      (value === "video" && !videos.length)
                    }
                  >
                    {publishMediaTypeLabel(value)}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="publish-controls">
            {publishChannel === "facebook_page" ? (
              <div className="config-group">
                <label>Page ID</label>
                <input
                  className="text-field"
                  value={publishPageId}
                  onChange={(event) => setPublishPageId(event.target.value)}
                  placeholder="例如：1234567890"
                />
              </div>
            ) : (
              <div className="config-group">
                <label>Ad Account ID</label>
                <input
                  className="text-field"
                  value={publishAdAccountId}
                  onChange={(event) => setPublishAdAccountId(event.target.value)}
                  placeholder="例如：act_1234567890"
                />
              </div>
            )}

            {publishMediaType === "image" && (
              <div className="config-group">
                <label>图片素材</label>
                <select
                  className="select publish-select"
                  value={publishImageUrl}
                  onChange={(event) => setPublishImageUrl(event.target.value)}
                >
                  <option value="">请选择图片</option>
                  {creatives.map((asset) => (
                    <option key={asset.id} value={asset.url ?? ""}>
                      {imageOptionLabel(asset)}
                    </option>
                  ))}
                </select>
              </div>
            )}

            {publishMediaType === "video" && (
              <div className="config-group">
                <label>视频素材</label>
                <select
                  className="select publish-select"
                  value={publishVideoAssetId}
                  onChange={(event) => setPublishVideoAssetId(event.target.value)}
                >
                  <option value="">请选择视频</option>
                  {videos.map((video) => (
                    <option key={video.id} value={video.id}>
                      {videoOptionLabel(video)}
                    </option>
                  ))}
                </select>
              </div>
            )}
          </div>

          <textarea
            className="publish-input compact"
            value={publishMessage}
            onChange={(event) => setPublishMessage(event.target.value)}
            placeholder="发布文案"
          />

          {publishMediaType === "video" && selectedVideo && (
            <div className="publish-media-summary">
              <Film size={16} />
              <div>
                <strong>{videoOptionLabel(selectedVideo)}</strong>
                {selectedVideo.url && (
                  <a className="asset-url" href={selectedVideo.url} target="_blank" rel="noreferrer">
                    打开视频 URL
                  </a>
                )}
              </div>
            </div>
          )}

            </div>

            <div className="publish-meta-action-card">
              <div className="publish-section-title">
                <strong>Meta 投流包准备</strong>
                <span>预算与 Pixel 确认后，生成待人工审核的投流包。</span>
              </div>
              <div className="meta-create-fields">
                <div className="publish-controls">
                  <div className="config-group">
                    <label>Meta daily_budget</label>
                    <input
                      className="text-field"
                      value={metaDailyBudget}
                      onChange={(event) => setMetaDailyBudget(event.target.value.replace(/\D/g, ""))}
                      placeholder="例如：100"
                    />
                    <span className="field-hint">按 Meta 广告账户最小货币单位填写，最终创建状态固定为 PAUSED。</span>
                  </div>
                  <div className="config-group">
                    <label>Pixel ID（可选）</label>
                    <div className="pixel-picker-row">
                      <select
                        className="select publish-select"
                        value={metaPixels.some((pixel) => pixel.id === metaPixelId) ? metaPixelId : ""}
                        onChange={(event) => setMetaPixelId(event.target.value)}
                      >
                        <option value="">
                          {metaPixels.length ? "手动输入或不使用 Pixel" : "未选择 Pixel"}
                        </option>
                        {metaPixels.map((pixel) => (
                          <option key={pixel.id} value={pixel.id}>
                            {pixelOptionLabel(pixel)}
                          </option>
                        ))}
                      </select>
                      <button
                        className="secondary-button icon-compact"
                        onClick={onRefreshMetaPixels}
                        disabled={loading === "meta-pixels"}
                        title="刷新 Pixel"
                      >
                        {loading === "meta-pixels" ? <Loader2 size={16} className="spin" /> : <RefreshCw size={16} />}
                      </button>
                    </div>
                    <input
                      className="text-field"
                      value={metaPixelId}
                      onChange={(event) => setMetaPixelId(event.target.value.replace(/\D/g, ""))}
                      placeholder="也可以手动输入 Pixel ID，例如：1234567890"
                    />
                    {metaPixelError && <span className="field-hint warning-text">Pixel 自动获取失败：{metaPixelError}</span>}
                    <span className="field-hint">不填写则按流量/链接点击创建；选择或填写后购物事件可映射为 PURCHASE 转化。</span>
                  </div>
                </div>
                {!metaAdsCredentialReady && (
                  <div className="inline-warning">
                    需要完整配置 Page ID、Ad Account ID 和 Ad Token 后才能准备投流包。
                  </div>
                )}
                <button
                  className="primary-button publish-meta-action"
                  onClick={() => onPrepareMetaAdsPackage(preflightItems)}
                  disabled={!canPrepareMetaAdsPackage || loading === "meta-ads-prepare"}
                >
                  {loading === "meta-ads-prepare" ? <Loader2 size={16} className="spin" /> : <Megaphone size={16} />}
                  <span>准备待审核投流包</span>
                </button>
                {metaAdsDraftResult && (
                  <div className="meta-result">
                    <div className="ad-creative-fields compact">
                      <div>
                        <span>状态</span>
                        <strong>{statusLabel(metaAdsDraftResult.status)}</strong>
                      </div>
                      <div>
                        <span>模式</span>
                        <strong>{metaAdsDraftResult.dry_run ? "dry-run" : "真实创建"}</strong>
                      </div>
                      <div>
                        <span>Campaign ID</span>
                        <strong>{metaAdsDraftResult.meta_campaign_id || "-"}</strong>
                      </div>
                      <div>
                        <span>Ad ID</span>
                        <strong>{metaAdsDraftResult.meta_ad_id || "-"}</strong>
                      </div>
                    </div>
                    {metaAdsDraftResult.error_message && (
                      <div className="inline-error">{metaAdsDraftResult.error_message}</div>
                    )}
                    <JsonDetails title="查看返回 ID" value={metaAdsDraftResult.ids} />
                  </div>
                )}
              </div>
            </div>
          </div>

          <details className="advanced-publish-details">
            <summary>
              <span>高级配置与调试</span>
              <small>字段映射、投放草稿、dry-run payload</small>
            </summary>
            <div className="advanced-publish-body">
          <div className="payload-preview">
            <div className="payload-preview-head">
              <h3>广告创意字段映射</h3>
              <button
                className="secondary-button"
                onClick={onBuildAdCreativeDraft}
                disabled={!selectedCampaign || !selectedDraft || loading === "ad-creative-draft"}
              >
                {loading === "ad-creative-draft" ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />}
                <span>生成草稿</span>
              </button>
            </div>
            {adCreativeDraft ? (
              <div className="ad-creative-fields">
                <div>
                  <span>目标位置 / 网站</span>
                  <strong>{adCreativeDraft.destination_url || "-"}</strong>
                </div>
                <div>
                  <span>广告标题 / 选题</span>
                  <strong>{adCreativeDraft.headline}</strong>
                </div>
                <div>
                  <span>正文 / 文案</span>
                  <p>{adCreativeDraft.primary_text || "-"}</p>
                </div>
                <div>
                  <span>素材</span>
                  <strong>
                    {publishMediaTypeLabel(adCreativeDraft.media_type)}{" "}
                    {adCreativeDraft.facebook_video_id ? `/ Meta Video ${adCreativeDraft.facebook_video_id}` : ""}
                  </strong>
                </div>
                <JsonDetails title="查看 Meta payload" value={adCreativeDraft.meta_payload} />
              </div>
            ) : (
              <div className="field-hint padded">
                这里会把工单链接填到“网站”，选题填到“广告标题”，文案填到“正文”。
              </div>
            )}
          </div>

          <div className="payload-preview">
            <div className="payload-preview-head">
              <h3>投放计划草稿</h3>
              <button
                className="secondary-button"
                onClick={onBuildAdsPlanDraft}
                disabled={!selectedCampaign || !selectedDraft || loading === "ads-plan-draft"}
              >
                {loading === "ads-plan-draft" ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />}
                <span>生成投放草稿</span>
              </button>
            </div>
            {adsPlanDraft ? (
              <div className="ads-plan-fields">
                <div className="ad-creative-fields compact">
                  <div>
                    <span>国家 / 年龄 / 性别</span>
                    <strong>
                      {stringifyValue(adsPlanDraft.targeting_summary.country_code ?? "-")} /{" "}
                      {stringifyValue(adsPlanDraft.targeting_summary.age_label ?? "-")} /{" "}
                      {stringifyValue(adsPlanDraft.targeting_summary.gender_label ?? "-")}
                    </strong>
                  </div>
                  <div>
                    <span>事件 / 目标</span>
                    <strong>
                      {stringifyValue(adsPlanDraft.targeting_summary.event_name ?? "-")} /{" "}
                      {stringifyValue(adsPlanDraft.targeting_summary.mapped_objective ?? "-")}
                    </strong>
                  </div>
                </div>
                {adsPlanDraft.warnings.length > 0 && (
                  <div className="inline-warning">
                    {adsPlanDraft.warnings.map((warning) => (
                      <div key={warning}>{warning}</div>
                    ))}
                  </div>
                )}
                <div className="ads-plan-grid">
                  <div>
                    <strong>Campaign</strong>
                    <JsonBlock value={adsPlanDraft.campaign_payload} />
                  </div>
                  <div>
                    <strong>Ad Set</strong>
                    <JsonBlock value={adsPlanDraft.adset_payload} />
                  </div>
                  <div>
                    <strong>Ad</strong>
                    <JsonBlock value={adsPlanDraft.ad_payload} />
                  </div>
                </div>
              </div>
            ) : (
              <div className="field-hint padded">
                这里会根据工单国家、人群、事件和已选创意，生成 Campaign / Ad Set / Ad 的 dry-run 草稿。
              </div>
            )}
          </div>

          <div className="payload-preview">
            <div className="payload-preview-head">
              <h3>dry-run payload 预览</h3>
              <span>点击创建任务后，后端会补全 video_url 和 Meta endpoint</span>
            </div>
            <JsonDetails title="查看 dry-run payload" value={previewPayload} />
          </div>
            </div>
          </details>
        </div>
      </section>

      <section className="publishing-board">
        <section className="panel publish-task-panel">
          <div className="panel-header publish-task-header">
            <div>
              <h2>投流任务</h2>
              <span className="panel-note">列表只显示运营判断需要的信息，原始数据在右侧详情查看</span>
            </div>
            <div className="task-filter-tabs">
              {(["all", "review", "published", "active", "paused", "issue"] as PublishJobFilter[]).map((filter) => (
                <button
                  key={filter}
                  className={publishJobFilter === filter ? "active" : ""}
                  onClick={() => setPublishJobFilter(filter)}
                >
                  {publishJobFilterLabel(filter)}
                </button>
              ))}
            </div>
          </div>
          <DataList emptyText="暂无发布任务">
            {filteredPublishJobs.map((job) => (
              <PublishJobCard
                key={job.id}
                job={job}
                selected={selectedPublishJob?.id === job.id}
                loading={loading}
                onSelect={() => setSelectedPublishJobId(job.id)}
                onReview={onReview}
                onPublish={onPublish}
                onSyncMetaStatus={onSyncMetaStatus}
                onSyncMetaInsights={onSyncMetaInsights}
                onActivateMetaAds={onActivateMetaAds}
                onPauseMetaAds={onPauseMetaAds}
              />
            ))}
          </DataList>
        </section>

        <PublishJobDetailPanel job={selectedPublishJob} />
      </section>
    </section>
  );
}

function PublishJobCard({
  job,
  selected,
  loading,
  onSelect,
  onReview,
  onPublish,
  onSyncMetaStatus,
  onSyncMetaInsights,
  onActivateMetaAds,
  onPauseMetaAds,
}: {
  job: PublishJob;
  selected: boolean;
  loading: string | null;
  onSelect: () => void;
  onReview: (entityType: string, entityId: string, decision: "approved" | "rejected" | "needs_revision") => void;
  onPublish: (jobId: string) => void;
  onSyncMetaStatus: (jobId: string) => void;
  onSyncMetaInsights: (jobId: string) => void;
  onActivateMetaAds: (jobId: string) => void;
  onPauseMetaAds: (jobId: string) => void;
}) {
  const isMetaPackage = isMetaAdsPackageJob(job);
  const reviewStatus = publishJobReviewStatus(job);
  const activationStatus = publishJobActivationStatus(job);
  const metaReviewStatus = publishJobMetaReviewStatus(job);
  const insightSummary = publishJobInsightSummary(job);
  const isPausedDelivery = ["paused", "meta_paused"].includes(activationStatus);
  const canActivateMetaAds = isMetaPackage && job.status === "published" && isPausedDelivery;
  const canPauseMetaAds =
    isMetaPackage && job.status === "published" && !isPausedDelivery && activationStatus !== "archived";

  return (
    <article className={`publish-item publish-card ${selected ? "active" : ""}`} onClick={onSelect}>
      <div className="item-head">
        <div className="publish-card-title">
          <strong>{publishJobTitle(job)}</strong>
          <span>{job.external_id || job.id}</span>
        </div>
        <div className="button-row">
          {isMetaPackage && <StatusPill status={reviewStatus} />}
          {isMetaPackage && <StatusPill status={metaReviewStatus} />}
          {isMetaPackage && <StatusPill status={activationStatus} />}
          <StatusPill status={job.status} />
        </div>
      </div>

      <div className="publish-card-meta">
        <span>广告组：{publishJobAdsetName(job) || "-"}</span>
        <span>最近同步：{publishJobLastSync(job) || "-"}</span>
      </div>

      {insightSummary ? <MetaInsightStrip summary={insightSummary} /> : <EmptyInsightStrip />}

      {isMetaPackage ? (
        <div className="button-row publish-card-actions">
          {reviewStatus !== "approved" && job.status !== "cancelled" && (
            <>
              <button
                className="secondary-button"
                onClick={() => onReview("publish_job", job.id, "approved")}
                disabled={loading?.startsWith("review-publish_job")}
              >
                <Check size={16} />
                <span>审核通过</span>
              </button>
              <button
                className="secondary-button danger"
                onClick={() => onReview("publish_job", job.id, "rejected")}
                disabled={loading?.startsWith("review-publish_job")}
              >
                <X size={16} />
                <span>拒绝</span>
              </button>
            </>
          )}
          <button
            className="primary-button"
            onClick={() => onPublish(job.id)}
            disabled={reviewStatus !== "approved" || loading === "publish" || job.status === "published"}
          >
            <Send size={16} />
            <span>一键发布</span>
          </button>
          {job.status === "published" && (
            <>
              <button
                className="secondary-button"
                onClick={() => onSyncMetaInsights(job.id)}
                disabled={loading === `meta-insights-${job.id}`}
              >
                {loading === `meta-insights-${job.id}` ? (
                  <Loader2 size={16} className="spin" />
                ) : (
                  <BarChart3 size={16} />
                )}
                <span>同步数据</span>
              </button>
              <button
                className="secondary-button"
                onClick={() => onSyncMetaStatus(job.id)}
                disabled={loading === `meta-status-${job.id}`}
              >
                {loading === `meta-status-${job.id}` ? (
                  <Loader2 size={16} className="spin" />
                ) : (
                  <RefreshCw size={16} />
                )}
                <span>同步状态</span>
              </button>
            </>
          )}
          {canActivateMetaAds && (
            <button
              className="secondary-button danger"
              onClick={() => onActivateMetaAds(job.id)}
              disabled={loading === "meta-ads-activate"}
            >
              <Megaphone size={16} />
              <span>启用投放</span>
            </button>
          )}
          {canPauseMetaAds && (
            <button
              className="secondary-button danger"
              onClick={() => onPauseMetaAds(job.id)}
              disabled={loading === "meta-ads-pause"}
            >
              <Pause size={16} />
              <span>暂停投放</span>
            </button>
          )}
          <button className="secondary-button" onClick={onSelect}>
            <FileText size={16} />
            <span>查看详情</span>
          </button>
        </div>
      ) : (
        <div className="button-row publish-card-actions">
          <button className="secondary-button" onClick={() => onPublish(job.id)} disabled={loading === "publish"}>
            <Send size={16} />
            <span>dry-run 发布</span>
          </button>
          <button className="secondary-button" onClick={onSelect}>
            <FileText size={16} />
            <span>查看详情</span>
          </button>
        </div>
      )}
    </article>
  );
}

function PublishJobDetailPanel({ job }: { job: PublishJob | null }) {
  if (!job) {
    return (
      <aside className="panel publish-detail-panel">
        <div className="panel-header">
          <h2>任务详情</h2>
        </div>
        <EmptyState text="选择一条发布任务查看详情" />
      </aside>
    );
  }

  const isMetaPackage = isMetaAdsPackageJob(job);
  const insightSummary = publishJobInsightSummary(job);
  const metaStatus = isRecord(job.metadata_json.meta_status) ? job.metadata_json.meta_status : null;
  const metaInsights = isRecord(job.metadata_json.meta_insights) ? job.metadata_json.meta_insights : null;
  const ids = publishJobMetaIds(job);

  return (
    <aside className="panel publish-detail-panel">
      <div className="panel-header">
        <div>
          <h2>任务详情</h2>
          <span className="panel-note">{publishJobTitle(job)}</span>
        </div>
      </div>
      <div className="publish-detail-body">
        <div className="button-row">
          {isMetaPackage && <StatusPill status={publishJobReviewStatus(job)} />}
          {isMetaPackage && <StatusPill status={publishJobMetaReviewStatus(job)} />}
          {isMetaPackage && <StatusPill status={publishJobActivationStatus(job)} />}
          <StatusPill status={job.status} />
        </div>

        {insightSummary && <MetaInsightSummary summary={insightSummary} />}

        <KeyValueTable
          data={{
            任务ID: job.id,
            ExternalID: job.external_id,
            CampaignID: ids.campaign_id,
            AdSetID: ids.adset_id,
            AdID: ids.ad_id,
            广告组: publishJobAdsetName(job),
            最近同步: publishJobLastSync(job),
          }}
        />

        {typeof job.payload.video_url === "string" && (
          <a className="asset-url" href={job.payload.video_url} target="_blank" rel="noreferrer">
            打开视频 URL
          </a>
        )}

        <JsonDetails title="任务 payload" value={job.payload.meta_request ?? job.payload.plan ?? job.payload} />
        {metaInsights && <JsonDetails title="Meta 广告数据" value={metaInsights} />}
        {metaStatus && <JsonDetails title="Meta 状态" value={metaStatus} />}
      </div>
    </aside>
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
  accent: string;
  hint: string;
  icon: typeof BarChart3;
}) {
  return (
    <div className={`metric ${accent}`}>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{hint}</small>
      </div>
      <div className="metric-icon">
        <Icon size={20} />
      </div>
    </div>
  );
}

function StepButton({
  index,
  label,
  onClick,
  disabled,
}: {
  index: number;
  label: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button className="step-button" onClick={onClick} disabled={disabled}>
      <span>{index}</span>
      <strong>{label}</strong>
    </button>
  );
}

function NextStep({
  title,
  actionLabel,
  onAction,
  loading,
}: {
  title: string;
  actionLabel: string;
  onAction: () => void;
  loading: boolean;
}) {
  return (
    <section className="next-step">
      <strong>{title}</strong>
      <button className="primary-button" onClick={onAction} disabled={loading}>
        {loading ? <Loader2 size={16} className="spin" /> : <Send size={16} />}
        <span>{actionLabel}</span>
      </button>
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
  const fields = extraction.fields;

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onCancel}>
      <section
        className="confirm-dialog delivery-confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="delivery-confirm-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="confirm-icon">
          <Sparkles size={22} />
        </div>
        <div className="confirm-body">
          <h2 id="delivery-confirm-title">确认投放信息</h2>
          <p>缺失和建议值需要人工确认后再保存。</p>

          <div className="delivery-confirm-form">
            <div className="config-group full">
              <label>
                落地页链接 <span className="required-mark">必填</span>
              </label>
              <input
                className="text-field"
                value={form.landing_url}
                onChange={(event) => onChange("landing_url", event.target.value)}
                placeholder="https://example.com/page"
                autoComplete="off"
              />
              <DeliveryFieldMeta field={fields.landing_url} />
              <DeliveryCandidates
                values={fields.landing_url.candidates}
                onSelect={(value) => onChange("landing_url", value)}
              />
            </div>

            <div className="delivery-confirm-grid">
              <div className="config-group">
                <label>投放事件</label>
                <select
                  className="select delivery-select"
                  value={form.event_name}
                  onChange={(event) => onChange("event_name", event.target.value)}
                >
                  {DELIVERY_EVENT_OPTIONS.map((option) => (
                    <option value={option} key={option}>
                      {option}
                    </option>
                  ))}
                </select>
                <DeliveryFieldMeta field={fields.event_name} />
              </div>

              <div className="config-group">
                <label>
                  投放国家 <span className="required-mark">必填</span>
                </label>
                <input
                  className="text-field"
                  value={form.country}
                  onChange={(event) => onChange("country", event.target.value)}
                  placeholder="例如：印度 / IN"
                  autoComplete="off"
                />
                <DeliveryFieldMeta field={fields.country} />
                <DeliveryCandidates
                  values={fields.country.candidates}
                  onSelect={(value) => onChange("country", value)}
                />
              </div>

              <div className="config-group">
                <label>年龄</label>
                <div className="age-range-row">
                  <input
                    className="text-field"
                    value={form.age_min}
                    onChange={(event) => onChange("age_min", event.target.value.replace(/\D/g, ""))}
                    placeholder="不限"
                    inputMode="numeric"
                  />
                  <span>到</span>
                  <input
                    className="text-field"
                    value={form.age_max}
                    onChange={(event) => onChange("age_max", event.target.value.replace(/\D/g, ""))}
                    placeholder="不限"
                    inputMode="numeric"
                  />
                </div>
                <DeliveryFieldMeta field={fields.age_min} fallbackField={fields.age_max} />
              </div>

              <div className="config-group">
                <label>性别</label>
                <select
                  className="select delivery-select"
                  value={form.gender}
                  onChange={(event) => onChange("gender", event.target.value)}
                >
                  <option value="不限">不限</option>
                  <option value="男">男</option>
                  <option value="女">女</option>
                </select>
                <DeliveryFieldMeta field={fields.gender} />
              </div>
            </div>
          </div>
        </div>
        <div className="confirm-actions">
          <button className="secondary-button" onClick={onCancel} disabled={loading}>
            取消
          </button>
          <button className="primary-button" onClick={onConfirm} disabled={loading}>
            {loading ? <Loader2 size={16} className="spin" /> : <Check size={16} />}
            <span>确认创建</span>
          </button>
        </div>
      </section>
    </div>
  );
}

function DeliveryFieldMeta({
  field,
  fallbackField,
}: {
  field: WorkOrderDeliveryField;
  fallbackField?: WorkOrderDeliveryField;
}) {
  const reason = field.reason || fallbackField?.reason;
  return (
    <div className="delivery-field-meta">
      <span className={`delivery-status ${field.status}`}>{deliveryStatusLabel(field.status)}</span>
      {reason && <span>{reason}</span>}
    </div>
  );
}

function DeliveryCandidates({
  values,
  onSelect,
}: {
  values: unknown[];
  onSelect: (value: string) => void;
}) {
  const candidates = uniqueTexts(values).slice(0, 4);
  if (!candidates.length) return null;

  return (
    <div className="delivery-candidates">
      {candidates.map((candidate) => (
        <button
          className="candidate-button"
          type="button"
          key={candidate}
          onClick={() => onSelect(candidate)}
        >
          {candidate}
        </button>
      ))}
    </div>
  );
}

function ConfirmDialog({
  open,
  icon = "image",
  title,
  description,
  confirmLabel,
  cancelLabel,
  loading,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  icon?: "image" | "video" | "publish";
  title: string;
  description: string;
  confirmLabel: string;
  cancelLabel: string;
  loading: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  if (!open) return null;
  const Icon = icon === "video" ? Film : icon === "publish" ? Megaphone : Image;

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onCancel}>
      <section
        className="confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="confirm-icon">
          <Icon size={22} />
        </div>
        <div className="confirm-body">
          <h2 id="confirm-dialog-title">{title}</h2>
          <p>{description}</p>
        </div>
        <div className="confirm-actions">
          <button className="secondary-button" onClick={onCancel} disabled={loading}>
            {cancelLabel}
          </button>
          <button className="primary-button" onClick={onConfirm} disabled={loading}>
            {loading ? <Loader2 size={16} className="spin" /> : <Icon size={16} />}
            <span>{confirmLabel}</span>
          </button>
        </div>
      </section>
    </div>
  );
}

function PreflightRiskDialog({
  action,
  loading,
  onConfirm,
  onCancel,
}: {
  action: PendingPreflightAction | null;
  loading: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  if (!action) return null;

  const isPublish = action.kind === "publish_job";
  const title = isPublish ? "确认带风险一键发布？" : "确认带风险准备投流包？";
  const description = isPublish
    ? "以下项目仍需人工确认。继续后系统会执行当前发布任务。"
    : "以下项目仍需人工确认。继续后系统会生成待审核投流包。";
  const confirmLabel = isPublish ? "继续一键发布" : "继续准备";

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onCancel}>
      <section
        className="confirm-dialog preflight-risk-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="preflight-risk-dialog-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="confirm-icon">
          <Clock3 size={22} />
        </div>
        <div className="confirm-body">
          <h2 id="preflight-risk-dialog-title">{title}</h2>
          <p>{description}</p>
          <div className="preflight-risk-list">
            {action.warnings.map((item) => (
              <div className="preflight-risk-item" key={`${item.label}-${item.value}`}>
                <strong>{item.label}</strong>
                <span>{item.value}</span>
                <p>{item.detail}</p>
              </div>
            ))}
          </div>
        </div>
        <div className="confirm-actions">
          <button className="secondary-button" onClick={onCancel} disabled={loading}>
            取消
          </button>
          <button className="primary-button" onClick={onConfirm} disabled={loading}>
            {loading ? <Loader2 size={16} className="spin" /> : <Clock3 size={16} />}
            <span>{confirmLabel}</span>
          </button>
        </div>
      </section>
    </div>
  );
}

function ActivateAdsDialog({
  open,
  value,
  loading,
  onChange,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  value: string;
  loading: boolean;
  onChange: (value: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  if (!open) return null;

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onCancel}>
      <section
        className="confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="activate-ads-dialog-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="confirm-icon danger">
          <Megaphone size={22} />
        </div>
        <div className="confirm-body">
          <h2 id="activate-ads-dialog-title">确认启用真实投放</h2>
          <p>
            启用后 Meta Campaign、Ad Set 和 Ad 会切换为 ACTIVE，并可能开始消耗广告预算。请输入 ACTIVE
            确认。
          </p>
          <input
            className="text-field"
            value={value}
            onChange={(event) => onChange(event.target.value)}
            placeholder="ACTIVE"
            autoComplete="off"
          />
        </div>
        <div className="confirm-actions">
          <button className="secondary-button" onClick={onCancel} disabled={loading}>
            取消
          </button>
          <button className="primary-button danger" onClick={onConfirm} disabled={loading || value !== "ACTIVE"}>
            {loading ? <Loader2 size={16} className="spin" /> : <Megaphone size={16} />}
            <span>确认启用</span>
          </button>
        </div>
      </section>
    </div>
  );
}

function PauseAdsDialog({
  open,
  value,
  loading,
  onChange,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  value: string;
  loading: boolean;
  onChange: (value: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  if (!open) return null;

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onCancel}>
      <section
        className="confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="pause-ads-dialog-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="confirm-icon danger">
          <Pause size={22} />
        </div>
        <div className="confirm-body">
          <h2 id="pause-ads-dialog-title">确认暂停真实投放</h2>
          <p>
            暂停后 Meta Ad、Ad Set 和 Campaign 会切换为 PAUSED，通常不会继续产生新的投放消耗。已发生的展示、点击或数据结算可能仍有延迟。请输入 PAUSE 确认。
          </p>
          <input
            className="text-field"
            value={value}
            onChange={(event) => onChange(event.target.value)}
            placeholder="PAUSE"
            autoComplete="off"
          />
        </div>
        <div className="confirm-actions">
          <button className="secondary-button" onClick={onCancel} disabled={loading}>
            取消
          </button>
          <button className="primary-button danger" onClick={onConfirm} disabled={loading || value !== "PAUSE"}>
            {loading ? <Loader2 size={16} className="spin" /> : <Pause size={16} />}
            <span>确认暂停</span>
          </button>
        </div>
      </section>
    </div>
  );
}

function DataList({ children, emptyText }: { children: React.ReactNode; emptyText: string }) {
  const hasItems = Array.isArray(children) ? children.length > 0 : Boolean(children);
  return <div className="data-list">{hasItems ? children : <EmptyState text={emptyText} />}</div>;
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="empty-state">
      <Clock3 size={22} />
      <span>{text}</span>
    </div>
  );
}

function isCreativeGenerationActive(generation: CreativeGenerationState | null): boolean {
  return Boolean(
    generation &&
      ["submitting", "generating", "saving"].includes(generation.phase),
  );
}

function formatElapsedSeconds(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return `${minutes}m ${remainingSeconds}s`;
}

function VideoPreview({ url }: { url: string }) {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
  }, [url]);

  return (
    <div className="video-preview">
      {failed ? (
        <div className="video-preview-error">
          <Film size={18} />
          <div>
            <strong>视频暂时无法预览</strong>
            <span>链接可能已过期，或后端地址已变化。请先刷新状态；如果仍不可用，需要重新生成视频。</span>
          </div>
        </div>
      ) : (
        <video controls src={url} onError={() => setFailed(true)} />
      )}
      <a className="asset-url" href={url} target="_blank" rel="noreferrer">
        打开视频链接
      </a>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  return <span className={`status ${status}`}>{statusLabel(status)}</span>;
}

function MetaInsightStrip({ summary }: { summary: Record<string, unknown> }) {
  const currency = readText(summary.currency) || "USD";
  const metrics = [
    { label: "已花费", value: moneyLabel(summary.spend, currency) },
    { label: "展示", value: numberLabel(summary.impressions) },
    { label: "覆盖", value: numberLabel(summary.reach) },
    { label: "成效", value: numberLabel(summary.result_count) },
    { label: "单次成效", value: moneyLabel(summary.cost_per_result, currency) },
  ];

  return (
    <div className="meta-insight-strip">
      {metrics.map((metric) => (
        <div key={metric.label}>
          <span>{metric.label}</span>
          <strong>{metric.value}</strong>
        </div>
      ))}
    </div>
  );
}

function EmptyInsightStrip() {
  return (
    <div className="meta-insight-strip empty">
      <div>
        <span>广告数据</span>
        <strong>未同步</strong>
      </div>
      <div>
        <span>已花费</span>
        <strong>-</strong>
      </div>
      <div>
        <span>展示</span>
        <strong>-</strong>
      </div>
      <div>
        <span>成效</span>
        <strong>-</strong>
      </div>
    </div>
  );
}

function PreflightChecklist({ items }: { items: PreflightChecklistItem[] }) {
  const hasError = items.some((item) => item.status === "error");
  const hasWarning = items.some((item) => item.status === "warning");
  const state: PreflightStatus = hasError ? "error" : hasWarning ? "warning" : "ready";
  const summary =
    state === "ready"
      ? "可以进入投放审核"
      : state === "warning"
        ? "可继续，但需要确认"
        : "缺少必要信息";

  return (
    <section className={`preflight-checklist state-${state}`}>
      <div className="preflight-head">
        <div>
          <strong>投放前检查清单</strong>
          <span>发布前先核对关键资料，避免点发布后才发现字段缺失。</span>
        </div>
        <span className={`preflight-summary state-${state}`}>{summary}</span>
      </div>
      <div className="preflight-grid">
        {items.map((item) => {
          const Icon = item.status === "ready" ? Check : item.status === "warning" ? Clock3 : X;
          return (
            <article className={`preflight-item state-${item.status}`} key={item.label}>
              <div className="preflight-item-title">
                <span className="preflight-icon">
                  <Icon size={13} />
                </span>
                <strong>{item.label}</strong>
              </div>
              <span className="preflight-value" title={item.value}>
                {item.value}
              </span>
              <p>{item.detail}</p>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function MetaInsightSummary({ summary }: { summary: Record<string, unknown> }) {
  const currency = readText(summary.currency) || "USD";
  const metrics = [
    { label: "已花费", value: moneyLabel(summary.spend, currency) },
    { label: "展示次数", value: numberLabel(summary.impressions) },
    { label: "覆盖人数", value: numberLabel(summary.reach) },
    { label: "成效", value: `${numberLabel(summary.result_count)} ${resultTypeLabel(readText(summary.result_type))}` },
    { label: "单次成效费用", value: moneyLabel(summary.cost_per_result, currency) },
    { label: "预算", value: budgetLabel(summary, currency) },
    { label: "质量排名", value: rankingLabel(summary.quality_ranking) },
    { label: "互动率排名", value: rankingLabel(summary.engagement_rate_ranking) },
    { label: "转化率排名", value: rankingLabel(summary.conversion_rate_ranking) },
    { label: "广告组", value: readText(summary.adset_name) || "-" },
    { label: "结束日期", value: readText(summary.end_time) || "-" },
    { label: "上次修改", value: readText(summary.last_update_time) || "-" },
  ];

  return (
    <div className="meta-insight-grid">
      {metrics.map((metric) => (
        <div className="meta-insight-cell" key={metric.label}>
          <span>{metric.label}</span>
          <strong title={metric.value}>{metric.value}</strong>
        </div>
      ))}
    </div>
  );
}

function isReviewedStatus(status: string): boolean {
  return status === "approved" || status === "rejected";
}

function KeyValueTable({ data }: { data: Record<string, unknown> }) {
  return (
    <div className="kv-table">
      {Object.entries(data).map(([key, value]) => (
        <div className="kv-row" key={key}>
          <span>{key}</span>
          <strong>{value == null || value === "" ? "-" : stringifyValue(value)}</strong>
        </div>
      ))}
    </div>
  );
}

function JsonDetails({
  title,
  value,
  defaultOpen = false,
}: {
  title: string;
  value: unknown;
  defaultOpen?: boolean;
}) {
  return (
    <details className="json-details" open={defaultOpen}>
      <summary>{title}</summary>
      <JsonBlock value={value} />
    </details>
  );
}

function JsonBlock({ value }: { value: unknown }) {
  return <pre className="json-block">{JSON.stringify(value ?? {}, null, 2)}</pre>;
}

function formatStoryboard(storyboard: Record<string, unknown>[]): string {
  if (!storyboard.length) return "";

  return storyboard
    .map((scene, index) => {
      const sceneIndex = sceneValue(scene, "scene_index") || String(index + 1);
      const start = sceneValue(scene, "start_second") || "-";
      const end = sceneValue(scene, "end_second") || "-";
      const sourceAssetIds = scene["source_asset_ids"];
      const imageLine = Array.isArray(sourceAssetIds)
        ? `图片：${sourceAssetIds.map(String).join(", ")}`
        : "";
      return [
        `镜头 ${sceneIndex}（${start}-${end} 秒）`,
        `画面：${sceneValue(scene, "visual") || "-"}`,
        `字幕：${sceneValue(scene, "subtitle") || "-"}`,
        `动效：${sceneValue(scene, "motion") || "-"}`,
        `旁白：${sceneValue(scene, "voiceover") || "-"}`,
        imageLine,
        `备注：${sceneValue(scene, "notes") || "-"}`,
      ]
        .filter(Boolean)
        .join("\n");
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
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

function ImagePreview({
  asset,
  onInvalid,
  onOpen,
}: {
  asset: CreativeAsset;
  onInvalid: (assetId: string) => void;
  onOpen?: () => void;
}) {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
  }, [asset.url]);

  if (!asset.url || failed) {
    return (
      <div className="image-placeholder">
        <Image size={28} />
        <span>{failed ? "图片链接已过期，请重新生成图片" : asset.storage_key || "等待图片 URL"}</span>
      </div>
    );
  }
  return (
    <button className="asset-image-button" type="button" onClick={onOpen} title="打开大图预览">
      <img
        className="asset-image"
        src={asset.url}
        alt={asset.alt_text || "creative"}
        onError={() => {
          setFailed(true);
          onInvalid(asset.id);
        }}
      />
    </button>
  );
}

function ImagePreviewDialog({ asset, onClose }: { asset: CreativeAsset | null; onClose: () => void }) {
  if (!asset?.url) return null;

  return (
    <div className="modal-backdrop image-preview-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="image-preview-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="图片预览"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="image-preview-head">
          <div>
            <strong>{asset.alt_text || imagePromptTitle(asset.prompt) || "图片预览"}</strong>
            <span>{statusLabel(asset.status)}</span>
          </div>
          <button className="secondary-button icon-compact" onClick={onClose} title="关闭预览">
            <X size={16} />
          </button>
        </div>
        <img className="image-preview-large" src={asset.url} alt={asset.alt_text || "creative preview"} />
      </section>
    </div>
  );
}

function emptyReviewedDeliveryFields(): ReviewedDeliveryFields {
  return {
    landing_url: "",
    event_name: "流量",
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
    event_name: normalizeEventLabel(deliveryFieldDisplayText(fields.event_name)) || "流量",
    country: deliveryFieldDisplayText(fields.country),
    age_min: deliveryAgeText(fields.age_min),
    age_max: deliveryAgeText(fields.age_max),
    gender: normalizeGenderLabel(deliveryFieldDisplayText(fields.gender)),
    audience_description_raw: deliveryFieldDisplayText(fields.audience_description_raw),
  };
}

function validateDeliveryConfirmForm(form: ReviewedDeliveryFields): string | null {
  const landingUrl = form.landing_url.trim();
  const country = form.country.trim();
  const eventName = form.event_name.trim();
  const ageMin = form.age_min.trim();
  const ageMax = form.age_max.trim();

  if (!landingUrl) return "请补充落地页链接。";
  if (!isHttpUrl(landingUrl)) return "落地页链接需要是 http 或 https 开头的完整链接。";
  if (!country) return "请确认投放国家。";
  if (!eventName) return "请选择投放事件。";
  if ((ageMin && !ageMax) || (!ageMin && ageMax)) {
    return "年龄需要同时填写最小和最大，或都留空表示不限。";
  }
  if (ageMin && ageMax) {
    const min = Number(ageMin);
    const max = Number(ageMax);
    if (!Number.isInteger(min) || !Number.isInteger(max) || min < 13 || max > 65 || min > max) {
      return "年龄范围需要在 13 到 65 之间，且最小年龄不能大于最大年龄。";
    }
  }
  return null;
}

function normalizeReviewedDeliveryFields(form: ReviewedDeliveryFields): ReviewedDeliveryFields {
  const ageMin = form.age_min.trim();
  const ageMax = form.age_max.trim();
  return {
    landing_url: form.landing_url.trim(),
    event_name: normalizeEventLabel(form.event_name) || "流量",
    country: form.country.trim(),
    age_min: ageMin || "不限",
    age_max: ageMax || "不限",
    gender: normalizeGenderLabel(form.gender),
    audience_description_raw: form.audience_description_raw.trim(),
  };
}

function deliveryFieldDisplayText(field: WorkOrderDeliveryField): string {
  return readText(field.value) || readText(field.normalized_value);
}

function deliveryAgeText(field: WorkOrderDeliveryField): string {
  const value = deliveryFieldDisplayText(field);
  if (!value || ["不限", "all", "none"].includes(value.toLowerCase())) return "";
  return value.replace(/\D/g, "");
}

function normalizeEventLabel(value: string): string {
  const normalized = value.trim().toLowerCase().replace(/\s+/g, "");
  if (!normalized) return "";
  if (["purchase", "shop", "shopping"].includes(normalized) || /购物|购买|下单/.test(value)) {
    return "购物";
  }
  if (["addtocart", "add_to_cart", "cart"].includes(normalized) || value.includes("加购")) {
    return "加购";
  }
  if (["lead", "signup"].includes(normalized) || /线索|注册/.test(value)) {
    return "线索";
  }
  if (["traffic", "click", "linkclick", "link_click"].includes(normalized) || /流量|点击/.test(value)) {
    return "流量";
  }
  return DELIVERY_EVENT_OPTIONS.includes(value as (typeof DELIVERY_EVENT_OPTIONS)[number])
    ? value
    : "流量";
}

function normalizeGenderLabel(value: string): string {
  const normalized = value.trim().toLowerCase();
  if (!normalized || ["不限", "all", "all genders", "none"].includes(normalized)) return "不限";
  if (["male", "men", "man"].includes(normalized) || value.includes("男")) return "男";
  if (["female", "women", "woman"].includes(normalized) || value.includes("女")) return "女";
  return "不限";
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

function uniqueTexts(values: unknown[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const value of values) {
    const text = readText(value);
    if (text && !seen.has(text)) {
      seen.add(text);
      result.push(text);
    }
  }
  return result;
}

function reviewedDeliveryFieldText(fields: Record<string, unknown>, key: keyof ReviewedDeliveryFields): string {
  const value = fields[key];
  if (isRecord(value)) {
    return readText(value.value) || readText(value.normalized_value);
  }
  return readText(value);
}

function reviewedAgeLabel(fields: Record<string, unknown>): string | null {
  const ageMin = reviewedDeliveryFieldText(fields, "age_min");
  const ageMax = reviewedDeliveryFieldText(fields, "age_max");
  if (ageMin && ageMax && ageMin !== "不限" && ageMax !== "不限") return `${ageMin}-${ageMax}`;
  if (ageMin === "不限" || ageMax === "不限") return "不限";
  return null;
}

function workOrderFields(workOrder: WorkOrder): Record<string, unknown> {
  const reviewedFields = isRecord(workOrder.metadata_json.reviewed_delivery_fields)
    ? workOrder.metadata_json.reviewed_delivery_fields
    : {};
  const reviewedAge = reviewedAgeLabel(reviewedFields);
  const reviewedGender = reviewedDeliveryFieldText(reviewedFields, "gender");

  return {
    项目名称: workOrder.project_name,
    投放国家: workOrder.country || reviewedDeliveryFieldText(reviewedFields, "country"),
    投放媒体: workOrder.media,
    投放事件: workOrder.event_name || reviewedDeliveryFieldText(reviewedFields, "event_name"),
    年龄: reviewedAge,
    性别: reviewedGender || null,
    投放人群: workOrder.audience_description,
    产品名称: workOrder.product_name,
    投放链接: workOrder.landing_url || reviewedDeliveryFieldText(reviewedFields, "landing_url"),
    日报时区: workOrder.report_timezone,
  };
}

function viewTitle(view: ViewKey): string {
  return navItems.find((item) => item.key === view)?.label ?? "工作台";
}

function initialViewFromUrl(): ViewKey {
  const value = new URLSearchParams(window.location.search).get("view");
  return navItems.some((item) => item.key === value) ? (value as ViewKey) : "dashboard";
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    active: "进行中",
    archived: "已归档",
    approved: "已通过",
    cancelled: "已取消",
    completed: "已完成",
    draft: "草稿",
    failed: "失败",
    fetched: "已抓取",
    generated: "已生成",
    generating: "生成中",
    inactive: "不可用",
    meta_active: "Meta可投放",
    meta_approved: "Meta已通过",
    meta_inactive: "Meta不可用",
    meta_paused: "Meta暂停",
    meta_rejected: "Meta拒绝",
    meta_reviewing: "Meta审核中",
    meta_unknown: "Meta未知",
    meta_unsynced: "Meta未同步",
    needs_revision: "需修改",
    paused: "已暂停",
    pending: "待审核",
    proposed: "待选择",
    published: "已发布",
    publishing: "发布中",
    queued: "排队中",
    received: "已接收",
    rejected: "已拒绝",
    requested: "已创建",
    selected: "已选择",
  };
  return labels[status] ?? status;
}

function publishChannelLabel(value: string): string {
  const labels: Record<string, string> = {
    facebook_page: "Facebook Page",
    facebook_ad: "Facebook Ads",
  };
  return labels[value] ?? value;
}

function publishMediaTypeLabel(value: string): string {
  const labels: Record<string, string> = {
    image: "图片",
    video: "视频",
    text: "未配置素材",
  };
  return labels[value] ?? value;
}

function isMetaAdsPackageJob(job: PublishJob): boolean {
  return job.payload.ad_operation === "create_meta_ads_draft";
}

function publishJobFilterLabel(filter: PublishJobFilter): string {
  const labels: Record<PublishJobFilter, string> = {
    all: "全部",
    review: "待审核",
    published: "已发布",
    active: "投放中",
    paused: "暂停",
    issue: "异常",
  };
  return labels[filter];
}

function publishJobBucket(job: PublishJob): PublishJobFilter {
  const reviewStatus = publishJobReviewStatus(job);
  const activationStatus = publishJobActivationStatus(job);
  const metaReviewStatus = publishJobMetaReviewStatus(job);
  if (job.status === "failed" || reviewStatus === "rejected" || metaReviewStatus === "meta_rejected") {
    return "issue";
  }
  if (reviewStatus !== "approved" && isMetaAdsPackageJob(job)) {
    return "review";
  }
  if (["active", "meta_active"].includes(activationStatus)) {
    return "active";
  }
  if (["paused", "meta_paused"].includes(activationStatus)) {
    return "paused";
  }
  if (job.status === "published") {
    return "published";
  }
  return "published";
}

function publishJobTitle(job: PublishJob): string {
  const channel = isMetaAdsPackageJob(job)
    ? "Meta 投流包"
    : publishChannelLabel(String(job.channel) as PublishChannelKey);
  return `${channel} / ${publishMediaTypeLabel(String(job.payload.media_type ?? "image"))}`;
}

function publishJobMetaIds(job: PublishJob): Record<string, string | null> {
  const metadataIds = job.metadata_json.meta_ads_ids;
  const providerResponse = job.metadata_json.provider_response;
  const providerIds = isRecord(providerResponse) ? providerResponse.meta_ads_ids : null;
  const ids = isRecord(metadataIds) ? metadataIds : isRecord(providerIds) ? providerIds : {};
  return {
    campaign_id: readText(ids.campaign_id) || null,
    adset_id: readText(ids.adset_id) || null,
    ad_creative_id: readText(ids.ad_creative_id) || null,
    ad_id: readText(ids.ad_id) || job.external_id,
  };
}

function publishJobAdsetName(job: PublishJob): string {
  const summary = publishJobInsightSummary(job);
  const summaryName = summary ? readText(summary.adset_name) : "";
  if (summaryName) return summaryName;
  const plan = job.payload.plan;
  if (isRecord(plan)) {
    const adsetPayload = plan.adset_payload;
    if (isRecord(adsetPayload)) {
      return readText(adsetPayload.name);
    }
  }
  return "";
}

function publishJobLastSync(job: PublishJob): string {
  const insights = job.metadata_json.meta_insights;
  if (isRecord(insights)) {
    const syncedAt = readText(insights.synced_at);
    if (syncedAt) return syncedAt;
  }
  const status = job.metadata_json.meta_status;
  if (isRecord(status)) {
    const syncedAt = readText(status.synced_at);
    if (syncedAt) return syncedAt;
  }
  return "";
}

function publishJobReviewStatus(job: PublishJob): string {
  const status = job.metadata_json.review_status;
  return typeof status === "string" ? status : "pending";
}

function publishJobActivationStatus(job: PublishJob): string {
  const activation = job.metadata_json.activation;
  if (isRecord(activation)) {
    const status = activation.status;
    if (typeof status === "string") return status;
  }
  const deliveryStatus = job.metadata_json.delivery_status;
  if (typeof deliveryStatus === "string") return deliveryStatus;
  return "paused";
}

function publishJobMetaReviewStatus(job: PublishJob): string {
  const status = job.metadata_json.meta_review_status;
  if (typeof status === "string") {
    return status.startsWith("meta_") ? status : `meta_${status}`;
  }
  const metaStatus = job.metadata_json.meta_status;
  if (isRecord(metaStatus)) {
    const summary = metaStatus.summary;
    if (isRecord(summary)) {
      const reviewStatus = summary.review_status;
      if (typeof reviewStatus === "string") {
        return reviewStatus.startsWith("meta_") ? reviewStatus : `meta_${reviewStatus}`;
      }
    }
  }
  return "meta_unsynced";
}

function publishJobInsightSummary(job: PublishJob): Record<string, unknown> | null {
  const insights = job.metadata_json.meta_insights;
  if (!isRecord(insights)) return null;
  const summary = insights.summary;
  return isRecord(summary) ? summary : null;
}

function buildPreflightChecklist({
  landingUrl,
  selectedCampaign,
  selectedDraft,
  message,
  publishChannel,
  publishMediaType,
  pageId,
  adAccountId,
  mediaReady,
  selectedImage,
  selectedVideo,
  selectedVideoReady,
  metaDailyBudgetValue,
  metaAdsCredentialReady,
  latestMetaAdsJob,
  metaAdsDraftResult,
  isDryRunMode,
}: {
  landingUrl: string;
  selectedCampaign: Campaign | null;
  selectedDraft: CopyDraft | null;
  message: string;
  publishChannel: PublishChannelKey;
  publishMediaType: PublishMediaType;
  pageId: string;
  adAccountId: string;
  mediaReady: boolean;
  selectedImage: CreativeAsset | null;
  selectedVideo: VideoAsset | null;
  selectedVideoReady: boolean;
  metaDailyBudgetValue: number | null;
  metaAdsCredentialReady: boolean;
  latestMetaAdsJob: PublishJob | null;
  metaAdsDraftResult: MetaAdsDraftCreateResult | null;
  isDryRunMode: boolean;
}): PreflightChecklistItem[] {
  return [
    landingPagePreflightItem(landingUrl, selectedCampaign),
    pagePreflightItem(pageId),
    adAccountPreflightItem(adAccountId, publishChannel),
    creativePreflightItem(publishMediaType, mediaReady, selectedImage, selectedVideo, selectedVideoReady),
    copyPreflightItem(message, selectedDraft),
    budgetPreflightItem(metaDailyBudgetValue, publishChannel),
    metaStatusPreflightItem({
      metaAdsCredentialReady,
      latestMetaAdsJob,
      metaAdsDraftResult,
      isDryRunMode,
      publishChannel,
    }),
  ];
}

function landingPagePreflightItem(landingUrl: string, selectedCampaign: Campaign | null): PreflightChecklistItem {
  if (!selectedCampaign) {
    return {
      label: "落地页链接",
      status: "error",
      value: "未选择广告项目",
      detail: "先选择或创建广告项目，系统才能读取工单投放链接。",
    };
  }
  if (!landingUrl) {
    return {
      label: "落地页链接",
      status: "error",
      value: "未找到链接",
      detail: "工单或广告项目里缺少投放链接，真实广告无法落到目标网站。",
    };
  }
  if (!isHttpUrl(landingUrl)) {
    return {
      label: "落地页链接",
      status: "warning",
      value: landingUrl,
      detail: "链接格式不像 http/https 地址，发布前需要人工核实。",
    };
  }
  return {
    label: "落地页链接",
    status: "ready",
    value: landingUrl,
    detail: "将作为广告目标网站使用。",
  };
}

function pagePreflightItem(pageId: string): PreflightChecklistItem {
  if (!pageId.trim()) {
    return {
      label: "Page",
      status: "error",
      value: "缺少 Page ID",
      detail: "Meta 广告需要公共主页作为广告身份。",
    };
  }
  return {
    label: "Page",
    status: "ready",
    value: pageId,
    detail: "已准备公共主页身份。",
  };
}

function adAccountPreflightItem(adAccountId: string, publishChannel: PublishChannelKey): PreflightChecklistItem {
  if (publishChannel !== "facebook_ad") {
    return {
      label: "Ad Account",
      status: "warning",
      value: "当前是 Facebook Page",
      detail: "真实投流前请切换到 Facebook Ads 并确认广告账户。",
    };
  }
  if (!adAccountId.trim()) {
    return {
      label: "Ad Account",
      status: "error",
      value: "缺少广告账户",
      detail: "真实创建 Campaign / Ad Set / Ad 必须有广告账户。",
    };
  }
  return {
    label: "Ad Account",
    status: "ready",
    value: adAccountId,
    detail: "已准备广告账户。",
  };
}

function creativePreflightItem(
  publishMediaType: PublishMediaType,
  mediaReady: boolean,
  selectedImage: CreativeAsset | null,
  selectedVideo: VideoAsset | null,
  selectedVideoReady: boolean,
): PreflightChecklistItem {
  if (publishMediaType === "image") {
    return mediaReady && selectedImage
      ? {
          label: "素材",
          status: "ready",
          value: imageOptionLabel(selectedImage),
          detail: "已选择图片素材。",
        }
      : {
          label: "素材",
          status: "error",
          value: "未选择图片",
          detail: "当前选择图片投放，需要先选择可访问的图片素材。",
        };
  }
  if (publishMediaType === "video") {
    if (selectedVideoReady && selectedVideo) {
      return {
        label: "素材",
        status: "ready",
        value: videoOptionLabel(selectedVideo),
        detail: "已选择可发布的视频素材。",
      };
    }
    return {
      label: "素材",
      status: "error",
      value: selectedVideo ? statusLabel(selectedVideo.status) : "未选择视频",
      detail: "当前选择视频投放，需要可发布的视频或已上传 Meta Video。",
    };
  }
  return {
    label: "素材",
    status: "error",
    value: "缺少素材",
    detail: "投放素材只支持图片或视频，请先生成并选择素材。",
  };
}

function copyPreflightItem(message: string, selectedDraft: CopyDraft | null): PreflightChecklistItem {
  const trimmed = message.trim();
  if (!trimmed) {
    return {
      label: "文案",
      status: "error",
      value: "缺少正文",
      detail: "发布正文为空，先选择或生成审核通过的文案。",
    };
  }
  if (!selectedDraft) {
    return {
      label: "文案",
      status: "warning",
      value: `${trimmed.length} 字`,
      detail: "有正文，但未关联文案草稿，建议先走人工审核。",
    };
  }
  if (selectedDraft.status !== "approved") {
    return {
      label: "文案",
      status: "warning",
      value: statusLabel(selectedDraft.status),
      detail: "文案还不是已通过状态，真实投放前建议先审核通过。",
    };
  }
  return {
    label: "文案",
    status: "ready",
    value: `${trimmed.length} 字 / 已通过`,
    detail: "正文已准备好。",
  };
}

function budgetPreflightItem(
  metaDailyBudgetValue: number | null,
  publishChannel: PublishChannelKey,
): PreflightChecklistItem {
  if (publishChannel !== "facebook_ad") {
    return {
      label: "预算",
      status: "warning",
      value: "当前渠道不使用预算",
      detail: "真实广告投放前请切换 Facebook Ads 并填写 daily_budget。",
    };
  }
  if (!metaDailyBudgetValue) {
    return {
      label: "预算",
      status: "error",
      value: "缺少 daily_budget",
      detail: "真实创建 Ad Set 必须有日预算，按广告账户最小货币单位填写。",
    };
  }
  return {
    label: "预算",
    status: "ready",
    value: `daily_budget ${metaDailyBudgetValue}`,
    detail: "预算已填写，真实创建后默认保持 PAUSED。",
  };
}

function metaStatusPreflightItem({
  metaAdsCredentialReady,
  latestMetaAdsJob,
  metaAdsDraftResult,
  isDryRunMode,
  publishChannel,
}: {
  metaAdsCredentialReady: boolean;
  latestMetaAdsJob: PublishJob | null;
  metaAdsDraftResult: MetaAdsDraftCreateResult | null;
  isDryRunMode: boolean;
  publishChannel: PublishChannelKey;
}): PreflightChecklistItem {
  if (publishChannel !== "facebook_ad") {
    return {
      label: "Meta 状态",
      status: "warning",
      value: "Page 发布模式",
      detail: "当前不会创建 Meta 广告结构，真实投流前请切到 Facebook Ads。",
    };
  }
  if (!metaAdsCredentialReady) {
    return {
      label: "Meta 状态",
      status: "error",
      value: "账号配置不完整",
      detail: "需要 Page ID、Ad Account ID 和 Ad Token。",
    };
  }
  if (isDryRunMode) {
    return {
      label: "Meta 状态",
      status: "warning",
      value: "dry-run 模式",
      detail: "当前只会预览 payload，不会真实创建 Meta 广告。",
    };
  }
  if (metaAdsDraftResult?.error_message) {
    return {
      label: "Meta 状态",
      status: "error",
      value: "创建失败",
      detail: metaAdsDraftResult.error_message,
    };
  }
  if (!latestMetaAdsJob) {
    return {
      label: "Meta 状态",
      status: "warning",
      value: "尚未准备投流包",
      detail: "资料齐全后，先准备待审核投流包，再一键创建 Meta 广告。",
    };
  }

  const reviewStatus = publishJobReviewStatus(latestMetaAdsJob);
  const metaReviewStatus = publishJobMetaReviewStatus(latestMetaAdsJob);
  const activationStatus = publishJobActivationStatus(latestMetaAdsJob);
  if (latestMetaAdsJob.status === "failed" || reviewStatus === "rejected" || metaReviewStatus === "meta_rejected") {
    return {
      label: "Meta 状态",
      status: "error",
      value: statusLabel(metaReviewStatus),
      detail: latestMetaAdsJob.error_message || "Meta 或人工审核未通过，需要调整后重新创建。",
    };
  }
  if (activationStatus === "active" || activationStatus === "meta_active") {
    return {
      label: "Meta 状态",
      status: "ready",
      value: "投放中",
      detail: "Meta 广告已经启用。",
    };
  }
  if (metaReviewStatus === "meta_approved") {
    return {
      label: "Meta 状态",
      status: "ready",
      value: "Meta 已通过",
      detail: "广告已通过 Meta 审核，可按确认流程启用投放。",
    };
  }
  if (reviewStatus !== "approved") {
    return {
      label: "Meta 状态",
      status: "warning",
      value: "待人工审核",
      detail: "投流包还没有人工审核通过。",
    };
  }
  return {
    label: "Meta 状态",
    status: "warning",
    value: statusLabel(metaReviewStatus),
    detail: "Meta 广告已准备，建议同步审核状态后再启用投放。",
  };
}

function publishJobBlockingPreflightItems(job: PublishJob): PreflightChecklistItem[] {
  if (!isMetaAdsPackageJob(job)) return [];
  const items: PreflightChecklistItem[] = [];
  const plan = publishJobPreparedPlan(job);
  const dryRun = publishJobIsDryRun(job);
  const reviewStatus = publishJobReviewStatus(job);
  const pageId = readText(job.payload.page_id) || readText(plan?.page_id);
  const adAccountId = readText(job.payload.ad_account_id) || readText(plan?.ad_account_id);
  const destinationUrl = readText(plan?.destination_url);
  const mediaType = readText(job.payload.media_type) || readText(plan?.media_type);
  const adsetPayload = isRecord(plan?.adset_payload) ? plan.adset_payload : null;
  const dailyBudget = readText(adsetPayload?.daily_budget);

  if (reviewStatus !== "approved") {
    items.push({
      label: "人工审核",
      status: "error",
      value: statusLabel(reviewStatus),
      detail: "投流包必须先审核通过，才能一键发布。",
    });
  }
  if (!plan) {
    items.push({
      label: "投流包",
      status: "error",
      value: "缺少 plan",
      detail: "任务 payload 缺少 Campaign / Ad Set / Ad 计划，无法发布。",
    });
    return items;
  }
  if (!destinationUrl || !isHttpUrl(destinationUrl)) {
    items.push({
      label: "落地页链接",
      status: "error",
      value: destinationUrl || "缺少链接",
      detail: "Meta 广告必须有有效的目标网站链接。",
    });
  }
  if (!["image", "video"].includes(mediaType)) {
    items.push({
      label: "素材",
      status: "error",
      value: mediaType || "缺少素材类型",
      detail: "投放任务必须使用图片或视频素材，不能发布纯文字广告。",
    });
  }
  if (!dryRun && (!pageId || pageId === "dry-run-page")) {
    items.push({
      label: "Page",
      status: "error",
      value: pageId || "缺少 Page ID",
      detail: "真实发布必须使用有效 Page ID，不能使用 dry-run 占位值。",
    });
  }
  if (!dryRun && (!adAccountId || adAccountId === "dry-run-ad-account")) {
    items.push({
      label: "Ad Account",
      status: "error",
      value: adAccountId || "缺少广告账户",
      detail: "真实发布必须使用有效广告账户，不能使用 dry-run 占位值。",
    });
  }
  if (!dailyBudget || dailyBudget === "<DAILY_BUDGET_REQUIRED>") {
    items.push({
      label: "预算",
      status: "error",
      value: dailyBudget || "缺少 daily_budget",
      detail: "发布 Meta Ad Set 必须有 daily_budget。",
    });
  }
  if (containsRequiredPlaceholder(plan)) {
    items.push({
      label: "Meta payload",
      status: "error",
      value: "存在占位字段",
      detail: "payload 中仍有 REQUIRED 占位符，需要先补齐字段。",
    });
  }
  return items;
}

function publishJobWarningPreflightItems(job: PublishJob): PreflightChecklistItem[] {
  if (!isMetaAdsPackageJob(job)) return [];
  const items: PreflightChecklistItem[] = [];
  const plan = publishJobPreparedPlan(job);
  const dryRun = publishJobIsDryRun(job);
  const mediaType = readText(job.payload.media_type) || readText(plan?.media_type);
  const metaReviewStatus = publishJobMetaReviewStatus(job);
  const planWarnings = Array.isArray(job.metadata_json.preflight_warnings)
    ? job.metadata_json.preflight_warnings.map(readText).filter(Boolean)
    : [];

  if (dryRun) {
    items.push({
      label: "发布模式",
      status: "warning",
      value: "dry-run",
      detail: "继续后只会模拟创建，不会真实创建 Meta 广告。",
    });
  }
  if (job.status !== "published") {
    items.push({
      label: "投放状态",
      status: "warning",
      value: "将创建为 PAUSED",
      detail: "一键发布只创建广告结构，启用投放仍需后续人工确认。",
    });
  }
  if (!["meta_approved", "meta_unsynced", "meta_paused"].includes(metaReviewStatus)) {
    items.push({
      label: "Meta 审核",
      status: "warning",
      value: statusLabel(metaReviewStatus),
      detail: "建议发布后同步 Meta 审核状态，再决定是否启用投放。",
    });
  }
  for (const warning of planWarnings.slice(0, 4)) {
    items.push({
      label: "后端预检",
      status: "warning",
      value: "需要确认",
      detail: warning,
    });
  }
  return dedupePreflightItems(items);
}

function publishJobPreparedPlan(job: PublishJob): Record<string, unknown> | null {
  const plan = job.payload.plan;
  if (!isRecord(plan)) return null;
  return plan;
}

function publishJobIsDryRun(job: PublishJob): boolean {
  if (job.payload.dry_run === true) return true;
  const plan = publishJobPreparedPlan(job);
  const metaPayload = isRecord(plan?.meta_payload) ? plan.meta_payload : null;
  return metaPayload?.dry_run === true;
}

function containsRequiredPlaceholder(value: unknown): boolean {
  if (typeof value === "string") return value.includes("<") && value.includes("REQUIRED");
  if (Array.isArray(value)) return value.some(containsRequiredPlaceholder);
  if (isRecord(value)) return Object.values(value).some(containsRequiredPlaceholder);
  return false;
}

function dedupePreflightItems(items: PreflightChecklistItem[]): PreflightChecklistItem[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    const key = `${item.label}:${item.value}:${item.detail}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function landingUrlFromCampaign(campaign: Campaign | null): string {
  const metadata = campaign?.metadata_json ?? {};
  const landingPage = isRecord(metadata.landing_page) ? metadata.landing_page : null;
  const workOrder = isRecord(metadata.work_order) ? metadata.work_order : null;
  const parsedFields = isRecord(workOrder?.parsed_fields) ? workOrder.parsed_fields : null;
  return (
    readText(landingPage?.url) ||
    readText(workOrder?.landing_url) ||
    readText(parsedFields?.landing_url)
  );
}

function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

function readText(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function numberLabel(value: unknown): string {
  const text = readText(value);
  if (!text) return "0";
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed.toLocaleString() : text;
}

function moneyLabel(value: unknown, currency: string): string {
  const text = readText(value);
  if (!text) return `0.00 ${currency}`;
  const parsed = Number(text);
  return Number.isFinite(parsed) ? `${parsed.toFixed(2)} ${currency}` : `${text} ${currency}`;
}

function budgetLabel(summary: Record<string, unknown>, currency: string): string {
  const amount = readText(summary.budget_amount);
  if (!amount) return "-";
  const type = readText(summary.budget_type) === "lifetime_budget" ? "总预算" : "日预算";
  return `${type} ${minorCurrencyLabel(amount, currency)}`;
}

function minorCurrencyLabel(value: string, currency: string): string {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return `${value} ${currency}`;
  const zeroDecimalCurrencies = new Set(["BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW", "MGA", "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF"]);
  const amount = zeroDecimalCurrencies.has(currency.toUpperCase()) ? parsed : parsed / 100;
  return `${amount.toLocaleString(undefined, {
    minimumFractionDigits: zeroDecimalCurrencies.has(currency.toUpperCase()) ? 0 : 2,
    maximumFractionDigits: zeroDecimalCurrencies.has(currency.toUpperCase()) ? 0 : 2,
  })} ${currency}`;
}

function rankingLabel(value: unknown): string {
  const ranking = readText(value);
  const labels: Record<string, string> = {
    ABOVE_AVERAGE: "高于平均",
    AVERAGE: "平均",
    BELOW_AVERAGE_35: "低于平均 35%",
    BELOW_AVERAGE_20: "低于平均 20%",
    BELOW_AVERAGE_10: "低于平均 10%",
    UNKNOWN: "数据不足",
  };
  return labels[ranking] ?? (ranking || "数据不足");
}

function resultTypeLabel(value: string): string {
  const labels: Record<string, string> = {
    link_click: "链接点击",
    landing_page_view: "落地页浏览",
    purchase: "购买",
    "offsite_conversion.fb_pixel_purchase": "购买",
    add_to_cart: "加购",
    "offsite_conversion.fb_pixel_add_to_cart": "加购",
    lead: "线索",
  };
  return labels[value] ?? value;
}

function imageOptionLabel(asset: CreativeAsset): string {
  return asset.alt_text || asset.prompt || asset.url || shortId(asset.id);
}

function imagePromptTitle(prompt: string | null | undefined): string {
  const text = prompt || "";
  const titleLine = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("主题：") || line.toLowerCase().startsWith("theme:"));
  if (titleLine) {
    return titleLine.replace(/^主题：/i, "").replace(/^theme:/i, "").trim();
  }
  return text.split(/\r?\n/).find(Boolean)?.trim().slice(0, 80) || "";
}

function videoOptionLabel(video: VideoAsset): string {
  const duration = video.duration_seconds ? `${video.duration_seconds}s` : "-";
  const uploadMode = metaVideoId(video) ? "Meta Video" : hasLocalVideoFile(video) ? "local file" : "no upload file";
  return `${video.aspect_ratio} / ${duration} / ${statusLabel(video.status)} / ${uploadMode} / ${shortId(video.id)}`;
}

function videoTaskStateLabel(video: VideoAsset): string {
  if (video.status === "approved") return "已通过，可用于发布";
  if (video.status === "rejected") return "已拒绝";
  if (video.status === "failed") return "生成失败，可重试";
  if (video.status === "generating") return "生成中，稍后刷新";
  if (video.url || video.status === "generated") return "已生成，待审核";
  if (video.provider_job_id) return "已提交，等待结果";
  return "待开始生成";
}

function videoReviewLabel(video: VideoAsset): string {
  if (video.status === "approved") return "已通过";
  if (video.status === "rejected") return "已拒绝";
  if (video.url || video.status === "generated") return "待审核";
  return "生成后审核";
}

function pixelOptionLabel(pixel: AdPixel): string {
  const name = pixel.name || "Meta Pixel";
  const fired = pixel.last_fired_time ? ` / 最近触发：${formatDate(pixel.last_fired_time)}` : "";
  return `${name} (${pixel.id})${fired}`;
}

function formatMetaPixelError(message: string): string {
  if (message.includes("API access blocked") || message.includes('"code":200')) {
    return "Meta 阻止了当前 token 读取 Pixel 列表。可以先手动填写 Pixel ID；后续需要确认应用权限、广告账户权限或 BM 资产授权。";
  }
  if (message.includes("Ad Account ID is required")) {
    return "请先选择广告账户 Ad Account。";
  }
  if (message.includes("access token")) {
    return "当前账号缺少可用的 Ads Token，请重新授权或检查 token 配置。";
  }
  return message;
}

function isPublishableVideo(video: VideoAsset): boolean {
  return ["generated", "approved"].includes(video.status) && Boolean(metaVideoId(video) || hasLocalVideoFile(video));
}

function hasLocalVideoFile(video: VideoAsset | null): boolean {
  return Boolean(video?.storage_key?.startsWith("local://"));
}

function metaVideoId(video: VideoAsset | null): string | null {
  const value = video?.metadata_json?.facebook_video_id;
  return typeof value === "string" && value.trim() ? value : null;
}

function buildMetaIdentityOptions(
  accounts: MetaAccount[],
  _config: FacebookPublishConfig | null,
): MetaIdentityOption[] {
  const groups = new Map<string, MetaIdentityOption>();
  for (const account of accounts) {
    const key = metaIdentityKey(account);
    const existing = groups.get(key);
    if (existing) {
      existing.accounts.push(account);
      continue;
    }
    groups.set(key, {
      key,
      label: metaIdentityLabel(account),
      subtitle: "",
      accounts: [account],
      pages: [],
      adAccounts: [],
    });
  }

  return Array.from(groups.values()).map((identity) => {
    const pages = dedupeMetaAssetOptions(
      identity.accounts.flatMap((account) => metaPageOptionsForAccount(account)),
    );
    const adAccounts = dedupeMetaAssetOptions(
      identity.accounts.flatMap((account) =>
        metaAdAccountOptionsForAccount(account).map((option) => ({
          ...option,
          accountId: accountIdForAdAccount(identity.accounts, option.id, option.accountId),
        })),
      ),
    );
    return {
      ...identity,
      pages,
      adAccounts,
      subtitle: `${adAccounts.length || 0} 个广告账户 / ${pages.length || 0} 个 Page`,
    };
  });
}

function metaIdentityKey(account: MetaAccount): string {
  const oauthUser = metaOAuthUser(account);
  const userId = readText(oauthUser?.id);
  return userId ? `user:${userId}` : `account:${account.id}`;
}

function metaIdentityLabel(account: MetaAccount): string {
  const oauthUser = metaOAuthUser(account);
  return (
    readText(oauthUser?.name) ||
    cleanMetaIdentityName(account.name, account) ||
    account.ad_account_name ||
    account.ad_account_id ||
    shortId(account.id)
  );
}

function cleanMetaIdentityName(value: unknown, account: MetaAccount): string {
  let text = readText(value);
  if (!text) return "";

  const pageNames = new Set<string>();
  if (account.page_name) pageNames.add(account.page_name);
  if (account.page_id) pageNames.add(account.page_id);
  for (const page of account.available_pages ?? []) {
    const name = readText(page.name);
    const id = readText(page.id);
    if (name) pageNames.add(name);
    if (id) pageNames.add(id);
  }

  for (const pageName of pageNames) {
    const suffix = ` / ${pageName}`;
    if (text.endsWith(suffix)) {
      text = text.slice(0, -suffix.length).trim();
      break;
    }
  }
  return text;
}

function metaOAuthUser(account: MetaAccount): Record<string, unknown> | null {
  const value = account.metadata_json?.oauth_user;
  return isRecord(value) ? value : null;
}

function metaPageOptionsForAccount(account: MetaAccount): MetaAssetOption[] {
  const rawPages = Array.isArray(account.available_pages) ? account.available_pages : [];
  const options: MetaAssetOption[] = [];
  for (const page of rawPages) {
    const id = readText(page.id);
    if (!id) continue;
    const name = readText(page.name) || id;
    options.push({
      id,
      name,
      label: `${name} (${id})`,
      accountId: account.id,
    });
  }

  if (account.page_id && !options.some((item) => item.id === account.page_id)) {
    const name = account.page_name || account.page_id;
    options.unshift({
      id: account.page_id,
      name,
      label: `${name} (${account.page_id})`,
      accountId: account.id,
    });
  }
  return options;
}

function metaAdAccountOptionsForAccount(account: MetaAccount): MetaAssetOption[] {
  const rawAdAccounts = Array.isArray(account.available_ad_accounts)
    ? account.available_ad_accounts
    : [];
  const options: MetaAssetOption[] = [];
  for (const adAccount of rawAdAccounts) {
    const id = normalizeMetaAdAccountId(readText(adAccount.id) || readText(adAccount.account_id));
    if (!id) continue;
    const business = isRecord(adAccount.business) ? adAccount.business : null;
    const businessId = readText(business?.id) || null;
    const businessName = readText(business?.name) || null;
    const name = readText(adAccount.name) || id;
    options.push({
      id,
      name,
      label: businessName ? `${name} (${id}) / BM: ${businessName}` : `${name} (${id})`,
      accountId: account.id,
      businessId,
      businessName,
    });
  }

  const accountAdId = normalizeMetaAdAccountId(account.ad_account_id);
  if (accountAdId && !options.some((item) => item.id === accountAdId)) {
    const name = account.ad_account_name || accountAdId;
    options.unshift({
      id: accountAdId,
      name,
      label: account.business_name
        ? `${name} (${accountAdId}) / BM: ${account.business_name}`
        : `${name} (${accountAdId})`,
      accountId: account.id,
      businessId: account.business_id,
      businessName: account.business_name,
    });
  }
  return options;
}

function dedupeMetaAssetOptions(options: MetaAssetOption[]): MetaAssetOption[] {
  const byId = new Map<string, MetaAssetOption>();
  for (const option of options) {
    const existing = byId.get(option.id);
    if (!existing) {
      byId.set(option.id, { ...option });
      continue;
    }
    if (!existing.accountId && option.accountId) existing.accountId = option.accountId;
    if (!existing.businessId && option.businessId) existing.businessId = option.businessId;
    if (!existing.businessName && option.businessName) existing.businessName = option.businessName;
  }
  return Array.from(byId.values());
}

function accountIdForAdAccount(
  accounts: MetaAccount[],
  adAccountId: string,
  fallbackAccountId: string | null,
): string | null {
  const matched = accounts.find(
    (account) => normalizeMetaAdAccountId(account.ad_account_id) === adAccountId,
  );
  return matched?.id ?? fallbackAccountId;
}

function normalizeMetaAdAccountId(value: unknown): string {
  const text = readText(value);
  if (!text) return "";
  return text.startsWith("act_") ? text : `act_${text}`;
}

function metaAccountIdentitySummary(
  account: MetaAccount | null,
  config: FacebookPublishConfig | null,
  selectedPage?: MetaAssetOption | null,
  selectedAdAccount?: MetaAssetOption | null,
  selectedIdentity?: MetaIdentityOption | null,
): {
  accountName: string;
  subtitle: string;
  pageLabel: string;
  adAccountLabel: string;
  businessLabel: string;
  tokenLabel: string;
  expiryLabel: string;
} {
  if (!account) {
    return {
      accountName: ".env 默认账号",
      subtitle: "未选择 OAuth 授权账号，将使用本地环境变量配置",
      pageLabel: config?.page.id || (config?.page.id_configured ? "已配置" : "未配置"),
      adAccountLabel: config?.ads.ad_account_id || (config?.ads.ad_account_configured ? "已配置" : "未配置"),
      businessLabel: "-",
      tokenLabel: [
        config?.page.access_token_configured ? "Page Token 已配置" : "Page Token 未配置",
        config?.ads.access_token_configured ? "Ad Token 已配置" : "Ad Token 未配置",
      ].join(" / "),
      expiryLabel: "环境变量 Token",
    };
  }

  return {
    accountName:
      selectedIdentity?.label ||
      metaIdentityLabel(account) ||
      account.ad_account_name ||
      account.ad_account_id ||
      shortId(account.id),
    subtitle:
      selectedIdentity?.subtitle ||
      `${account.ad_account_name || account.ad_account_id || "未绑定 Ad Account"} / ${
        account.page_name || account.page_id || "未绑定 Page"
      }`,
    pageLabel:
      selectedPage?.label ||
      (account.page_name
        ? `${account.page_name}${account.page_id ? ` (${account.page_id})` : ""}`
        : account.page_id || "未绑定 Page"),
    adAccountLabel:
      selectedAdAccount?.label ||
      (account.ad_account_name
        ? `${account.ad_account_name}${account.ad_account_id ? ` (${account.ad_account_id})` : ""}`
        : account.ad_account_id || "未绑定 Ad Account"),
    businessLabel: selectedAdAccount?.businessName
      ? `${selectedAdAccount.businessName}${
          selectedAdAccount.businessId ? ` (${selectedAdAccount.businessId})` : ""
        }`
      : account.business_name
        ? `${account.business_name}${account.business_id ? ` (${account.business_id})` : ""}`
        : account.business_id || "未绑定 BM",
    tokenLabel: account.access_token_configured
      ? account.page_access_token_configured
        ? "Ad Token / Page Token 可用"
        : "Ad Token 可用，Page Token 未配置"
      : "Token 未配置",
    expiryLabel: metaTokenExpiryLabel(account.token_expires_at),
  };
}

function metaAccountIdentityHealth(
  account: MetaAccount | null,
  config: FacebookPublishConfig | null,
  selectedPageId?: string,
  selectedAdAccountId?: string,
  selectedPage?: MetaAssetOption | null,
  selectedAdAccount?: MetaAssetOption | null,
): { status: PreflightStatus; label: string; notes: string[] } {
  const notes: string[] = [];

  if (!account) {
    if (!config?.page.id_configured) notes.push("缺少 Page ID");
    if (!config?.ads.ad_account_configured) notes.push("缺少 Ad Account ID");
    if (!config?.ads.access_token_configured) notes.push("缺少 Ad Token");
    if (!config?.page.access_token_configured) notes.push("缺少 Page Token");
    if (notes.some((note) => note.includes("缺少"))) {
      return { status: "error", label: "默认账号不完整", notes };
    }
    return { status: "ready", label: "默认账号可用", notes: ["当前使用 .env 默认配置"] };
  }

  const effectivePageId = selectedPageId || account.page_id || "";
  const effectiveAdAccountId =
    normalizeMetaAdAccountId(selectedAdAccountId) || normalizeMetaAdAccountId(account.ad_account_id);
  const hasPageOptions = (account.available_pages ?? []).length > 0;
  const hasAdAccountOptions = (account.available_ad_accounts ?? []).length > 0;

  if (!effectivePageId) notes.push("缺少 Page");
  if (!effectiveAdAccountId) notes.push("缺少 Ad Account");
  if (!account.access_token_configured) notes.push("缺少 Ad Token");
  if (!account.page_access_token_configured) notes.push("Page Token 未确认，发布主页内容会受影响");
  if (selectedPageId && hasPageOptions && !selectedPage) notes.push("所选 Page 不在授权资产列表");
  if (selectedAdAccountId && hasAdAccountOptions && !selectedAdAccount) {
    notes.push("所选 Ad Account 不在授权资产列表");
  }

  const expiry = metaTokenExpiryState(account.token_expires_at);
  if (expiry === "expired") notes.push("Token 已过期");
  if (expiry === "soon") notes.push("Token 即将过期");
  if (!account.business_id && !account.business_name) notes.push("未绑定 BM，可继续但建议核实归属");
  if (account.status && !["active", "connected"].includes(account.status)) {
    notes.push(`账号状态：${account.status}`);
  }

  if (
    notes.some(
      (note) =>
        note.includes("缺少") ||
        note.includes("已过期") ||
        note.includes("不在授权资产列表"),
    )
  ) {
    return { status: "error", label: "账号不可用", notes };
  }
  if (notes.length) {
    return { status: "warning", label: "需要确认", notes };
  }
  return { status: "ready", label: "投放身份可用", notes: ["Page、Ad Account、Token 已准备"] };
}

function metaTokenExpiryState(value: string | null): "unknown" | "valid" | "soon" | "expired" {
  if (!value) return "unknown";
  const expiresAt = Date.parse(value);
  if (!Number.isFinite(expiresAt)) return "unknown";
  const daysLeft = (expiresAt - Date.now()) / 86400000;
  if (daysLeft < 0) return "expired";
  if (daysLeft <= 7) return "soon";
  return "valid";
}

function metaTokenExpiryLabel(value: string | null): string {
  if (!value) return "未提供过期时间";
  const expiresAt = Date.parse(value);
  if (!Number.isFinite(expiresAt)) return value;
  const daysLeft = Math.ceil((expiresAt - Date.now()) / 86400000);
  const date = new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(expiresAt));
  if (daysLeft < 0) return `${date} 已过期`;
  if (daysLeft === 0) return `${date} 今日到期`;
  return `${date} / ${daysLeft} 天后到期`;
}

function metaAccountEnvOptionLabel(config: FacebookPublishConfig | null): string {
  const page = config?.page.id || (config?.page.id_configured ? "Page 已配置" : "Page 未配置");
  const ad = config?.ads.ad_account_id || (config?.ads.ad_account_configured ? "Ad Account 已配置" : "Ad Account 未配置");
  return `${ad} / ${page}`;
}

function metaAccountOptionLabel(account: MetaAccount): string {
  const health = metaAccountIdentityHealth(account, null);
  const adAccount = account.ad_account_name || account.ad_account_id || "未绑定 Ad Account";
  const page = account.page_name || account.page_id || "未绑定 Page";
  return `${health.label} / ${adAccount} / Page: ${page}`;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function campaignOptionLabel(campaign: Campaign, index: number): string {
  const latest = index === 0 ? "（最新）" : "";
  return `${campaign.name}${latest} · ${formatDate(campaign.created_at)} · ${shortId(campaign.id)}`;
}

function shortId(value: string): string {
  return value.slice(0, 8);
}

function isToday(value: string): boolean {
  const date = new Date(value);
  const today = new Date();
  return date.toDateString() === today.toDateString();
}

function stringifyValue(value: unknown): string {
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseOptionalInteger(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number.parseInt(trimmed, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

export default App;
