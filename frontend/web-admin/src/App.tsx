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
  RefreshCw,
  Send,
  Sparkles,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { ApiError, api } from "./lib/api";
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
  Topic,
  VideoAsset,
  WorkOrder,
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
type PublishMediaType = "text" | "image" | "video";

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
  videos: "基于图片配置视频任务，并提交 Seedance 真实生成",
  publishing: "创建 Facebook 发布任务，并先用 dry-run 验证",
};

const VIDEO_MAX_REFERENCE_IMAGES = 2;

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
  const [activeView, setActiveView] = useState<ViewKey>("dashboard");
  const [workOrders, setWorkOrders] = useState<WorkOrder[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [selectedWorkOrderId, setSelectedWorkOrderId] = useState<string | null>(null);
  const [selectedCampaignId, setSelectedCampaignId] = useState<string | null>(null);
  const [snapshots, setSnapshots] = useState<LandingPageSnapshot[]>([]);
  const [topics, setTopics] = useState<Topic[]>([]);
  const [drafts, setDrafts] = useState<CopyDraft[]>([]);
  const [creatives, setCreatives] = useState<CreativeAsset[]>([]);
  const [videos, setVideos] = useState<VideoAsset[]>([]);
  const [publishJobs, setPublishJobs] = useState<PublishJob[]>([]);
  const [facebookConfig, setFacebookConfig] = useState<FacebookPublishConfig | null>(null);
  const [adCreativeDraft, setAdCreativeDraft] = useState<AdCreativeDraft | null>(null);
  const [adsPlanDraft, setAdsPlanDraft] = useState<AdsPlanDraft | null>(null);
  const [metaAdsDraftResult, setMetaAdsDraftResult] = useState<MetaAdsDraftCreateResult | null>(null);
  const [rawWorkOrder, setRawWorkOrder] = useState(sampleWorkOrder);
  const [selectedTopicId, setSelectedTopicId] = useState<string | null>(null);
  const [selectedDraftId, setSelectedDraftId] = useState<string | null>(null);
  const [selectedCreativeIds, setSelectedCreativeIds] = useState<string[]>([]);
  const [copyFeedback, setCopyFeedback] = useState("");
  const [publishMessage, setPublishMessage] = useState("");
  const [publishImageUrl, setPublishImageUrl] = useState("");
  const [publishVideoAssetId, setPublishVideoAssetId] = useState("");
  const [publishChannel, setPublishChannel] = useState<PublishChannelKey>("facebook_page");
  const [publishMediaType, setPublishMediaType] = useState<PublishMediaType>("video");
  const [publishPageId, setPublishPageId] = useState("dry-run-page");
  const [publishAdAccountId, setPublishAdAccountId] = useState("dry-run-ad-account");
  const [metaDailyBudget, setMetaDailyBudget] = useState("");
  const [metaPixelId, setMetaPixelId] = useState("");
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [creativeConfirmOpen, setCreativeConfirmOpen] = useState(false);
  const [videoConfirmId, setVideoConfirmId] = useState<string | null>(null);
  const [metaAdsConfirmOpen, setMetaAdsConfirmOpen] = useState(false);
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
  const publishableImages = useMemo(
    () => creatives.filter((item) => Boolean(item.url)),
    [creatives],
  );
  const publishableVideos = useMemo(
    () => videos.filter((item) => item.url && ["generated", "approved"].includes(item.status)),
    [videos],
  );

  useEffect(() => {
    void refreshBaseData();
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
    if (publishMediaType === "video" && !publishableVideos.length) {
      setPublishMediaType(publishableImages.length ? "image" : "text");
    }
    if (publishMediaType === "image" && !publishableImages.length) {
      setPublishMediaType(publishableVideos.length ? "video" : "text");
    }
  }, [publishMediaType, publishableImages.length, publishableVideos.length]);

  useEffect(() => {
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
      const [nextWorkOrders, nextCampaigns, nextPublishJobs, nextFacebookConfig] = await Promise.all([
        api.listWorkOrders(100),
        api.listCampaigns(100),
        api.listPublishJobs(),
        api.getMetaPublishConfig(),
      ]);
      setWorkOrders(nextWorkOrders);
      setCampaigns(nextCampaigns);
      setPublishJobs(nextPublishJobs);
      setFacebookConfig(nextFacebookConfig);
      if (nextFacebookConfig.page.id) {
        setPublishPageId(nextFacebookConfig.page.id);
      }
      if (nextFacebookConfig.ads.ad_account_id) {
        setPublishAdAccountId(nextFacebookConfig.ads.ad_account_id);
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
      setSelectedTopicId((current) =>
        current && nextTopics.some((item) => item.id === current) ? current : nextTopics[0]?.id ?? null,
      );
      setSelectedDraftId((current) =>
        current && nextDrafts.some((item) => item.id === current) ? current : nextDrafts[0]?.id ?? null,
      );
      setSelectedCreativeIds((current) => {
        const availableIds = new Set(nextCreatives.map((item) => item.id));
        const retainedIds = current.filter((id) => availableIds.has(id));
        return retainedIds.length ? retainedIds : nextCreatives.slice(0, 3).map((item) => item.id);
      });
    });
  }

  async function handleCreateWorkOrder() {
    const created = await run(
      "create-work-order",
      () => api.createWorkOrder(rawWorkOrder),
      "工单已创建",
    );
    if (created) {
      setWorkOrders((current) => [created, ...current]);
      setSelectedWorkOrderId(created.id);
      setActiveView("work-orders");
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

  async function handleGenerateTopics() {
    if (!selectedCampaign) return;
    const generated = await run(
      "topics",
      () => api.generateTopics(selectedCampaign.id, 3),
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
      setTopics((current) => current.map((item) => (item.id === topic.id ? topic : item)));
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
    const generated = await run(
      "creatives",
      () => api.generateCreatives(selectedDraft.id, 3, "1:1"),
      "图片已生成",
    );
    if (generated) {
      setCreatives((current) => [...generated, ...current]);
      setSelectedCreativeIds(generated.slice(0, 3).map((item) => item.id));
      setActiveView("creatives");
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
    if (selectedCreativeIds.length === 0) {
      setError("请先选择至少一张图片，再创建视频任务");
      return;
    }
    setActiveView("videos");
  }

  async function handleGenerateVideoStoryboard() {
    if (!selectedCampaign) return;
    if (selectedCreativeIds.length === 0) {
      setError("请先选择至少一张图片，再生成视频脚本");
      return;
    }
    if (selectedCreativeIds.length > VIDEO_MAX_REFERENCE_IMAGES) {
      setError(`Seedance 1.5 pro 当前最多支持 ${VIDEO_MAX_REFERENCE_IMAGES} 张首尾帧图片，请减少选择后再生成视频脚本`);
      return;
    }
    const generated = await run(
      "video-storyboard",
      () =>
        api.generateVideoStoryboard(
          selectedCampaign.id,
          selectedCreativeIds,
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
    if (!selectedCampaign || selectedCreativeIds.length === 0) return;
    if (selectedCreativeIds.length > VIDEO_MAX_REFERENCE_IMAGES) {
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
          creativeAssetIds: selectedCreativeIds,
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
      "真实视频生成任务已提交",
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
    if (publishMediaType === "image" && !publishImageUrl) {
      setError("请选择一张可发布图片，或切换为文字/视频发布。");
      return;
    }
    if (publishMediaType === "video" && !publishVideoAssetId) {
      setError("请选择一个已生成的视频，或切换为文字/图片发布。");
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
          draftId: selectedDraft?.id ?? null,
          channel: publishChannel,
          mediaType: publishMediaType,
          message,
          pageId: publishPageId,
          adAccountId: publishAdAccountId,
          accessTokenRef,
          imageUrl: publishMediaType === "image" ? publishImageUrl : undefined,
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
          draftId: selectedDraft?.id ?? null,
          topicId: selectedTopic?.id ?? null,
          creativeAssetId:
            publishMediaType === "image"
              ? creatives.find((asset) => asset.url === publishImageUrl)?.id ?? null
              : null,
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
          draftId: selectedDraft?.id ?? null,
          topicId: selectedTopic?.id ?? null,
          creativeAssetId:
            publishMediaType === "image"
              ? creatives.find((asset) => asset.url === publishImageUrl)?.id ?? null
              : null,
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

  function requestPrepareMetaAdsPackage() {
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
          draftId: selectedDraft.id,
          topicId: selectedTopic?.id ?? null,
          creativeAssetId:
            publishMediaType === "image"
              ? creatives.find((asset) => asset.url === publishImageUrl)?.id ?? null
              : null,
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

  async function handlePublish(jobId: string) {
    const job = await run("publish", () => api.publishJob(jobId), "dry-run 发布已完成");
    if (job) {
      setPublishJobs((current) => current.map((item) => (item.id === job.id ? job : item)));
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
            creatives={creatives}
            selectedCreativeIds={selectedCreativeIds}
            setSelectedCreativeIds={setSelectedCreativeIds}
            onGenerate={requestGenerateCreatives}
            onReview={handleReview}
            onCreateVideo={handleOpenVideoConfig}
            loading={loading}
          />
        )}

        {activeView === "videos" && (
          <VideosView
            videos={videos}
            creatives={creatives}
            selectedCreativeIds={selectedCreativeIds}
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
            creatives={publishableImages}
            videos={publishableVideos}
            onCreateJob={handleCreatePublishJob}
            onBuildAdCreativeDraft={handleBuildAdCreativeDraft}
            onBuildAdsPlanDraft={handleBuildAdsPlanDraft}
            onPrepareMetaAdsPackage={requestPrepareMetaAdsPackage}
            onReview={handleReview}
            onPublish={handlePublish}
            loading={loading}
          />
        )}

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
          title="确认真实生成视频？"
          description="这一步会调用火山方舟 Doubao-Seedance-1.5-pro 视频生成 API，可能消耗额度。视频 URL 通常有有效期，生成后需要及时刷新并转存。"
          confirmLabel="确认生成视频"
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
  return (
    <section className="three-column">
      <section className="panel wide">
        <div className="panel-header">
          <h2>创建工单</h2>
          <button className="primary-button" onClick={onCreateWorkOrder} disabled={loading === "create-work-order"}>
            {loading === "create-work-order" ? <Loader2 size={16} className="spin" /> : <Check size={16} />}
            <span>创建</span>
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
  onGenerateTopics: () => void;
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
            <button className="primary-button" onClick={onGenerateTopics} disabled={loading === "topics"}>
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
  onGenerate: () => void;
  onSelect: (id: string) => void;
  onGenerateCopy: () => void;
  loading: string | null;
}) {
  return (
    <section className="view-stack">
      <section className="panel">
        <div className="panel-header">
          <h2>AI 选题</h2>
          <button className="primary-button" onClick={onGenerate} disabled={loading === "topics"}>
            {loading === "topics" ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />}
            <span>生成 1-3 个</span>
          </button>
        </div>
        <DataList emptyText="暂无选题">
          {topics.map((topic) => (
            <article className={`topic-item ${selectedTopic?.id === topic.id ? "active" : ""}`} key={topic.id}>
              <div className="item-head">
                <h3>{topic.title}</h3>
                <StatusPill status={topic.status} />
              </div>
              <p>{topic.angle}</p>
              <div className="tag-row">
                {topic.selling_points.slice(0, 4).map((point) => (
                  <span className="tag" key={point}>
                    {point}
                  </span>
                ))}
              </div>
              <div className="button-row">
                <button className="secondary-button" onClick={() => onSelect(topic.id)}>
                  <Check size={16} />
                  <span>选择</span>
                </button>
              </div>
            </article>
          ))}
        </DataList>
      </section>
      {selectedTopic && (
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

function CopyView({
  drafts,
  selectedDraft,
  setSelectedDraftId,
  selectedTopic,
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
  feedback: string;
  setFeedback: (value: string) => void;
  onGenerateCopy: () => void;
  onReviseCopy: () => void;
  onReview: (entityType: string, entityId: string, decision: "approved" | "rejected" | "needs_revision") => void;
  onGenerateCreatives: () => void;
  loading: string | null;
}) {
  return (
    <section className="two-column copy-layout">
      <section className="panel">
        <div className="panel-header">
          <h2>文案版本</h2>
          <button className="primary-button" onClick={onGenerateCopy} disabled={!selectedTopic || loading === "copy"}>
            {loading === "copy" ? <Loader2 size={16} className="spin" /> : <FileText size={16} />}
            <span>生成文案</span>
          </button>
        </div>
        <DataList emptyText="暂无文案">
          {drafts.map((draft) => (
            <button
              className={`list-button ${selectedDraft?.id === draft.id ? "active" : ""}`}
              key={draft.id}
              onClick={() => setSelectedDraftId(draft.id)}
            >
              <strong>{draft.headline || `版本 ${draft.version}`}</strong>
              <span>{draft.status} · v{draft.version}</span>
            </button>
          ))}
        </DataList>
      </section>

      <section className="panel wide">
        <div className="panel-header">
          <h2>文案内容</h2>
          {selectedDraft && <StatusPill status={selectedDraft.status} />}
        </div>
        {selectedDraft ? (
          <div className="copy-content">
            <KeyValueTable
              data={{
                标题: selectedDraft.headline,
                CTA: selectedDraft.cta,
                描述: selectedDraft.description,
              }}
            />
            <div className="text-block">{selectedDraft.body}</div>
            <textarea
              className="feedback-input"
              value={feedback}
              onChange={(event) => setFeedback(event.target.value)}
              placeholder="修改意见"
            />
            <div className="button-row">
              <button className="secondary-button" onClick={() => onReview("copy_draft", selectedDraft.id, "approved")}>
                <Check size={16} />
                <span>通过</span>
              </button>
              <button className="secondary-button" onClick={() => onReview("copy_draft", selectedDraft.id, "needs_revision")}>
                <RefreshCw size={16} />
                <span>需修改</span>
              </button>
              <button className="secondary-button danger" onClick={() => onReview("copy_draft", selectedDraft.id, "rejected")}>
                <X size={16} />
                <span>拒绝</span>
              </button>
              <button className="primary-button" onClick={onReviseCopy} disabled={!feedback.trim()}>
                <Sparkles size={16} />
                <span>按意见重写</span>
              </button>
              <button className="primary-button" onClick={onGenerateCreatives}>
                <Image size={16} />
                <span>生成图片</span>
              </button>
            </div>
          </div>
        ) : (
          <EmptyState text="暂无文案" />
        )}
      </section>
    </section>
  );
}

function CreativesView({
  creatives,
  selectedCreativeIds,
  setSelectedCreativeIds,
  onGenerate,
  onReview,
  onCreateVideo,
  loading,
}: {
  creatives: CreativeAsset[];
  selectedCreativeIds: string[];
  setSelectedCreativeIds: (ids: string[]) => void;
  onGenerate: () => void;
  onReview: (entityType: string, entityId: string, decision: "approved" | "rejected" | "needs_revision") => void;
  onCreateVideo: () => void;
  loading: string | null;
}) {
  return (
    <section className="view-stack">
      <section className="panel">
        <div className="panel-header">
          <h2>图片素材</h2>
          <div className="button-row">
            <button className="primary-button" onClick={onGenerate} disabled={loading === "creatives"}>
              {loading === "creatives" ? <Loader2 size={16} className="spin" /> : <Image size={16} />}
              <span>生成图片</span>
            </button>
            <button className="secondary-button" onClick={onCreateVideo} disabled={!selectedCreativeIds.length}>
              <Film size={16} />
              <span>创建视频任务</span>
            </button>
          </div>
        </div>
        <div className="asset-grid">
          {creatives.map((asset) => {
            const checked = selectedCreativeIds.includes(asset.id);
            return (
              <article className="asset-item" key={asset.id}>
                <label className="check-row">
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
                  <StatusPill status={asset.status} />
                </label>
                <ImagePreview asset={asset} />
                {asset.url && (
                  <a className="asset-url" href={asset.url} target="_blank" rel="noreferrer">
                    {asset.url}
                  </a>
                )}
                <div className="asset-prompt">{asset.prompt}</div>
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
              </article>
            );
          })}
        </div>
        {!creatives.length && <EmptyState text="暂无图片" />}
      </section>
    </section>
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

  return (
    <section className="two-column video-layout">
      <section className="panel wide">
        <div className="panel-header">
          <div>
            <h2>视频任务配置</h2>
            <span className="panel-note">先生成脚本，再创建任务；真实生成需单独确认</span>
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
            <span className="field-hint">Doubao-Seedance-1.5-pro 真实生成当前支持 4-12 秒。</span>
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
            <label>视频脚本 / storyboard</label>
            <textarea
              className="storyboard-input"
              value={storyboardText}
              onChange={(event) => setStoryboardText(event.target.value)}
              placeholder="点击 AI 生成视频脚本，或在这里手动填写每个镜头要展示什么。"
            />
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2>视频任务</h2>
        </div>
        <DataList emptyText="暂无视频任务">
          {videos.map((video) => {
            const generateKey = `video-generate-${video.id}`;
            const refreshKey = `video-refresh-${video.id}`;
            const providerStarted = Boolean(video.provider_job_id);
            const durationSupported =
              !video.duration_seconds || (video.duration_seconds >= 4 && video.duration_seconds <= 12);
            const referenceImageCount = video.source_asset_ids.length;
            const referenceImageCountSupported =
              referenceImageCount <= VIDEO_MAX_REFERENCE_IMAGES;
            const canSubmitProvider =
              (!providerStarted || video.status === "failed") &&
              referenceImageCountSupported &&
              video.status !== "rejected" &&
              video.status !== "generating" &&
              video.status !== "generated";

            return (
              <article className="video-item" key={video.id}>
                <div className="item-head">
                  <strong>{video.aspect_ratio} · {video.duration_seconds ?? "-"}s</strong>
                  <StatusPill status={video.status} />
                </div>
                <p>{video.prompt}</p>
                {video.provider_job_id && (
                  <div className="video-meta">
                    <span>上游任务 ID</span>
                    <code>{video.provider_job_id}</code>
                  </div>
                )}
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
                {video.url && (
                  <div className="video-preview">
                    <video controls src={video.url} />
                    <a className="asset-url" href={video.url} target="_blank" rel="noreferrer">
                      打开视频链接
                    </a>
                  </div>
                )}
                {video.storyboard.length > 0 && (
                  <pre className="video-storyboard-preview">
                    {formatStoryboard(video.storyboard as Record<string, unknown>[])}
                  </pre>
                )}
                <div className="button-row">
                  {canSubmitProvider && (
                    <button
                      className="secondary-button"
                      onClick={() => onStartGeneration(video.id)}
                      disabled={!durationSupported || loading === generateKey}
                    >
                      {loading === generateKey ? <Loader2 size={16} className="spin" /> : <Film size={16} />}
                      <span>{providerStarted ? "重新生成视频" : "真实生成视频"}</span>
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
                  {!isReviewedStatus(video.status) && (
                    <>
                      <button
                        className="secondary-button"
                        onClick={() => onReview("video_asset", video.id, "approved")}
                        disabled={video.status === "generating"}
                      >
                        <Check size={16} />
                        <span>通过</span>
                      </button>
                      <button
                        className="secondary-button danger"
                        onClick={() => onReview("video_asset", video.id, "rejected")}
                        disabled={video.status === "generating"}
                      >
                        <X size={16} />
                        <span>拒绝</span>
                      </button>
                    </>
                  )}
                </div>
                {isReviewedStatus(video.status) && (
                  <div className="review-complete">
                    {video.status === "approved" ? <Check size={16} /> : <X size={16} />}
                    <span>审核完成：{statusLabel(video.status)}</span>
                  </div>
                )}
              </article>
            );
          })}
        </DataList>
      </section>
    </section>
  );
}

function PublishingView({
  publishJobs,
  facebookConfig,
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
  creatives,
  videos,
  onCreateJob,
  onBuildAdCreativeDraft,
  onBuildAdsPlanDraft,
  onPrepareMetaAdsPackage,
  onReview,
  onPublish,
  loading,
}: {
  publishJobs: PublishJob[];
  facebookConfig: FacebookPublishConfig | null;
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
  creatives: CreativeAsset[];
  videos: VideoAsset[];
  onCreateJob: () => void;
  onBuildAdCreativeDraft: () => void;
  onBuildAdsPlanDraft: () => void;
  onPrepareMetaAdsPackage: () => void;
  onReview: (entityType: string, entityId: string, decision: "approved" | "rejected" | "needs_revision") => void;
  onPublish: (jobId: string) => void;
  loading: string | null;
}) {
  const selectedVideo = videos.find((video) => video.id === publishVideoAssetId) ?? null;
  const message = publishMessage || selectedDraft?.primary_text || selectedDraft?.body || "";
  const accessTokenRef =
    publishChannel === "facebook_page"
      ? facebookConfig?.page.access_token_ref
      : facebookConfig?.ads.access_token_ref;
  const isDryRunMode = facebookConfig?.dry_run ?? true;
  const activeCredentialReady =
    publishChannel === "facebook_page"
      ? Boolean(facebookConfig?.page.id_configured && facebookConfig.page.access_token_configured)
      : Boolean(
          facebookConfig?.ads.ad_account_configured && facebookConfig.ads.access_token_configured,
        );
  const mediaReady =
    publishMediaType === "text" ||
    (publishMediaType === "image" && Boolean(publishImageUrl)) ||
    (publishMediaType === "video" && Boolean(publishVideoAssetId));
  const credentialReadyForMode = isDryRunMode ? true : activeCredentialReady;
  const canCreate = Boolean(selectedCampaign && message.trim() && mediaReady && credentialReadyForMode);
  const metaDailyBudgetValue = parseOptionalInteger(metaDailyBudget);
  const metaAdsCredentialReady = Boolean(
    facebookConfig?.ads.ad_account_configured &&
      facebookConfig.ads.access_token_configured &&
      facebookConfig.page.id_configured,
  );
  const canCreateMetaAds = Boolean(
    selectedCampaign &&
      selectedDraft &&
      mediaReady &&
      metaDailyBudgetValue &&
      metaAdsCredentialReady,
  );
  const previewPayload = {
    campaign_id: selectedCampaign?.id ?? null,
    draft_id: selectedDraft?.id ?? null,
    channel: publishChannel,
    payload: {
      media_type: publishMediaType,
      page_id: publishPageId || "dry-run-page",
      ad_account_id: publishAdAccountId || "dry-run-ad-account",
      message,
      access_token_ref: accessTokenRef ?? null,
      ...(publishMediaType === "image" && publishImageUrl ? { image_url: publishImageUrl } : {}),
      ...(publishMediaType === "video" && publishVideoAssetId
        ? {
            video_asset_id: publishVideoAssetId,
            video_url_preview: selectedVideo?.url ?? null,
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
                {(["text", "image", "video"] as PublishMediaType[]).map((value) => (
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
                <JsonBlock value={adCreativeDraft.meta_payload} />
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
              <h3>准备 Meta 投流包</h3>
              <button
                className="primary-button"
                onClick={onPrepareMetaAdsPackage}
                disabled={!canCreateMetaAds || loading === "meta-ads-prepare"}
              >
                {loading === "meta-ads-prepare" ? <Loader2 size={16} className="spin" /> : <Megaphone size={16} />}
                <span>准备待审核投流包</span>
              </button>
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
                  <input
                    className="text-field"
                    value={metaPixelId}
                    onChange={(event) => setMetaPixelId(event.target.value)}
                    placeholder="例如：1234567890"
                  />
                  <span className="field-hint">不填写则按流量/链接点击创建；填写后购物事件可映射为 PURCHASE 转化。</span>
                </div>
              </div>
              {!metaAdsCredentialReady && (
                <div className="inline-warning">
                  需要完整配置 Page ID、Ad Account ID 和 Ad Token 后才能准备投流包。
                </div>
              )}
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
                  <JsonBlock value={metaAdsDraftResult.ids} />
                </div>
              )}
            </div>
          </div>

          <div className="payload-preview">
            <div className="payload-preview-head">
              <h3>dry-run payload 预览</h3>
              <span>点击创建任务后，后端会补全 video_url 和 Meta endpoint</span>
            </div>
            <JsonBlock value={previewPayload} />
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2>发布任务</h2>
        </div>
        <DataList emptyText="暂无发布任务">
          {publishJobs.map((job) => {
            const isMetaPackage = isMetaAdsPackageJob(job);
            const reviewStatus = publishJobReviewStatus(job);
            return (
              <article className="publish-item" key={job.id}>
                <div className="item-head">
                  <strong>
                    {isMetaPackage ? "Meta 投流包" : publishChannelLabel(String(job.channel) as PublishChannelKey)} /{" "}
                    {publishMediaTypeLabel(String(job.payload.media_type ?? "text") as PublishMediaType)}
                  </strong>
                  <div className="button-row">
                    {isMetaPackage && <StatusPill status={reviewStatus} />}
                    <StatusPill status={job.status} />
                  </div>
                </div>
                <span>{job.external_id || job.id}</span>
                {typeof job.payload.video_url === "string" && (
                  <a className="asset-url" href={job.payload.video_url} target="_blank" rel="noreferrer">
                    视频 URL
                  </a>
                )}
                <JsonBlock value={job.payload.meta_request ?? job.payload.plan ?? job.payload} />
                {isMetaPackage ? (
                  <div className="button-row">
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
                  </div>
                ) : (
                  <button className="secondary-button" onClick={() => onPublish(job.id)} disabled={loading === "publish"}>
                    <Send size={16} />
                    <span>dry-run 发布</span>
                  </button>
                )}
              </article>
            );
          })}
        </DataList>
      </section>
    </section>
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

function StatusPill({ status }: { status: string }) {
  return <span className={`status ${status}`}>{statusLabel(status)}</span>;
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

function ImagePreview({ asset }: { asset: CreativeAsset }) {
  if (!asset.url) {
    return (
      <div className="image-placeholder">
        <Image size={28} />
        <span>{asset.storage_key || "等待图片 URL"}</span>
      </div>
    );
  }
  return <img className="asset-image" src={asset.url} alt={asset.alt_text || "creative"} />;
}

function workOrderFields(workOrder: WorkOrder): Record<string, unknown> {
  return {
    项目名称: workOrder.project_name,
    投放国家: workOrder.country,
    投放媒体: workOrder.media,
    投放事件: workOrder.event_name,
    投放人群: workOrder.audience_description,
    产品名称: workOrder.product_name,
    投放链接: workOrder.landing_url,
    日报时区: workOrder.report_timezone,
  };
}

function viewTitle(view: ViewKey): string {
  return navItems.find((item) => item.key === view)?.label ?? "工作台";
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
    text: "文字",
    image: "图片",
    video: "视频",
  };
  return labels[value] ?? value;
}

function isMetaAdsPackageJob(job: PublishJob): boolean {
  return job.payload.ad_operation === "create_meta_ads_draft";
}

function publishJobReviewStatus(job: PublishJob): string {
  const status = job.metadata_json.review_status;
  return typeof status === "string" ? status : "pending";
}

function imageOptionLabel(asset: CreativeAsset): string {
  return asset.alt_text || asset.prompt || asset.url || shortId(asset.id);
}

function videoOptionLabel(video: VideoAsset): string {
  const duration = video.duration_seconds ? `${video.duration_seconds}s` : "-";
  return `${video.aspect_ratio} / ${duration} / ${statusLabel(video.status)} / ${shortId(video.id)}`;
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

function parseOptionalInteger(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number.parseInt(trimmed, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

export default App;
