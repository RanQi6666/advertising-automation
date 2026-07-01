import type { CreativeAsset, VideoAsset } from "../types/domain";

export type GenerationTaskStatus = "queued" | "running" | "succeeded" | "failed";

export type CreativeGenerationTaskSlot = {
  index: number;
  status: "loading" | "done" | "error";
  asset?: CreativeAsset;
  message?: string;
};

export type GenerationTask = {
  id: string;
  queue_name: string;
  task_type: string;
  business_type: string;
  business_id: string;
  campaign_id: string | null;
  status: GenerationTaskStatus;
  priority: number;
  payload: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error_code: string | null;
  error_message: string | null;
  retryable: boolean;
  attempt_count: number;
  max_attempts: number;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type GenerationTaskListResponse = {
  items: GenerationTask[];
  total: number;
  limit: number;
  offset: number;
  summary: {
    total?: number;
    by_status?: Record<string, number>;
    by_queue?: Record<string, number>;
    retryable_failed_count?: number;
    active_count?: number;
    resumable_queued_count?: number;
    stale_running_count?: number;
    interrupted_failed_count?: number;
  };
};

export type GenerationTaskMonitorStats = {
  activeCount: number;
  failedCount: number;
  retryableFailedCount: number;
  succeededCount: number;
};

export type GenerationTaskFailureAdvice = {
  title: string;
  detail: string;
  action: string;
};

const generationTaskQueueLabels: Record<string, string> = {
  text_queue: "文本队列",
  image_queue: "图片队列",
  video_queue: "视频队列",
  callback_queue: "回调队列",
};

const generationTaskStatusLabels: Record<string, string> = {
  queued: "等待中",
  running: "运行中",
  succeeded: "已完成",
  failed: "失败",
};

const generationTaskTypeLabels: Record<string, string> = {
  topic_generate: "选题生成",
  copy_generate: "文案生成",
  copy_revise: "文案改写",
  image_generate: "图片生成",
  video_generate: "视频生成",
  ad_generation_callback: "回调外部系统",
};

export function generationTaskQueueLabel(queueName: string): string {
  return generationTaskQueueLabels[queueName] ?? queueName;
}

export function generationTaskStatusLabel(status: string): string {
  return generationTaskStatusLabels[status] ?? status;
}

export function generationTaskTypeLabel(taskType: string): string {
  return generationTaskTypeLabels[taskType] ?? taskType;
}

export function generationTaskFailureAdvice(
  task: Pick<GenerationTask, "error_code" | "error_message" | "retryable">,
): GenerationTaskFailureAdvice {
  const fallbackAction = task.retryable
    ? "可以先点击重试；如果连续失败，再检查模型服务、外部链接或输入内容。"
    : "请先检查输入内容或后端日志，修正后重新发起生成。";
  const fallbackDetail = task.error_message || "任务执行失败，但后台没有返回更具体的错误信息。";
  const adviceByCode: Record<string, GenerationTaskFailureAdvice> = {
    provider_timeout: {
      title: "模型响应超时",
      detail: "模型供应商在限定时间内没有返回结果，任务可能已经排队过久或请求内容较重。",
      action: "可以稍后点击重试；如果频繁出现，优先降低同一时间的图片或视频生成数量。",
    },
    provider_429: {
      title: "模型限流",
      detail: "当前生成并发较高，供应商暂时拒绝处理。",
      action: "可以稍等后点击重试，或降低同时生成的任务数。",
    },
    external_url_unreachable: {
      title: "外部链接不可达",
      detail: "系统无法访问工单、素材或落地页里的外部地址。",
      action: "先检查 URL 是否可打开，修正后重新生成或重试任务。",
    },
    unknown_provider_error: {
      title: "模型服务异常",
      detail: "供应商返回了未分类错误，通常与模型服务、网络或请求内容有关。",
      action: "可以先重试一次；如果仍失败，再查看详情里的错误原文和请求参数。",
    },
    callback_failed: {
      title: "回调外部系统失败",
      detail: "AI 结果已经生成，但回传外部系统时失败。",
      action: "检查外部系统回调地址、鉴权和网络连通性，然后重试回调任务。",
    },
    task_interrupted: {
      title: "后台任务被中断",
      detail: "后端可能在任务执行时重启，系统已将这个运行中的任务标记为可重试失败。",
      action: "先确认对应的图片、选题或文案是否已经生成；如果未生成，再点击重试。",
    },
    task_stale: {
      title: "后台任务疑似卡死",
      detail: "任务运行时间超过后端恢复阈值，系统已将它标记为可重试失败。",
      action: "先查看详情里的结果和对应素材；如果没有产出，再点击重试。",
    },
  };
  return task.error_code ? adviceByCode[task.error_code] ?? {
    title: "任务执行失败",
    detail: fallbackDetail,
    action: fallbackAction,
  } : {
    title: "任务执行失败",
    detail: fallbackDetail,
    action: fallbackAction,
  };
}

export function generationTaskMonitorStats(tasks: GenerationTask[]): GenerationTaskMonitorStats {
  return tasks.reduce<GenerationTaskMonitorStats>(
    (stats, task) => {
      if (task.status === "queued" || task.status === "running") stats.activeCount += 1;
      if (task.status === "failed") {
        stats.failedCount += 1;
        if (task.retryable) stats.retryableFailedCount += 1;
      }
      if (task.status === "succeeded") stats.succeededCount += 1;
      return stats;
    },
    {
      activeCount: 0,
      failedCount: 0,
      retryableFailedCount: 0,
      succeededCount: 0,
    },
  );
}

export function generationTaskIsFinal(task: GenerationTask): boolean {
  return task.status === "succeeded" || task.status === "failed";
}

export function generationTaskIsSuccessful(task: GenerationTask): boolean {
  return task.status === "succeeded";
}

export function generationTaskSummary(task: GenerationTask, label: string): string {
  const queueLabel =
    task.queue_name === "image_queue"
      ? "图片队列"
      : task.queue_name === "video_queue"
        ? "视频队列"
        : "文本队列";
  if (task.status === "queued") return `${label}已进入${queueLabel}，等待处理。`;
  if (task.status === "running") return `${label}正在生成中，请稍候。`;
  if (task.status === "succeeded") return `${label}已生成。`;
  if (task.error_message) return `${label}生成失败：${task.error_message}`;
  return `${label}生成失败，请重试。`;
}

export function videoAssetFromGenerationTask(task: GenerationTask): VideoAsset | null {
  const video = task.result?.video;
  return isVideoAsset(video) ? video : null;
}

export function creativeAssetsFromGenerationTask(task: GenerationTask): CreativeAsset[] {
  const assets = task.result?.assets;
  if (!Array.isArray(assets)) return [];
  return assets
    .map((item) => (isRecord(item) ? item.asset : null))
    .filter((asset): asset is CreativeAsset => isCreativeAsset(asset));
}

export function creativeSlotsFromGenerationTask(task: GenerationTask): CreativeGenerationTaskSlot[] {
  const assets = creativeAssetsFromGenerationTask(task);
  const assetsById = new Map(assets.map((asset) => [asset.id, asset]));
  const assetsByIndex = new Map<number, CreativeAsset>();
  for (const asset of assets) {
    const index = creativeImageIndex(asset);
    if (index > 0) assetsByIndex.set(index, asset);
  }
  const slots = task.result?.slots;
  if (!Array.isArray(slots)) {
    return assets.map((asset) => ({
      index: creativeImageIndex(asset),
      status: "done" as const,
      asset,
    }));
  }

  return slots
    .filter(isRecord)
    .map((slot) => {
      const index = numericValue(slot.index) ?? 0;
      const status: CreativeGenerationTaskSlot["status"] =
        slot.status === "done" ? "done" : slot.status === "error" ? "error" : "loading";
      const assetId = typeof slot.asset_id === "string" ? slot.asset_id : null;
      const embeddedAsset: CreativeAsset | undefined = isCreativeAsset(slot.asset)
        ? slot.asset
        : undefined;
      const asset =
        embeddedAsset ?? (assetId ? assetsById.get(assetId) : undefined) ?? assetsByIndex.get(index);
      const message = typeof slot.message === "string" ? slot.message : undefined;
      return {
        index,
        status,
        ...(asset ? { asset } : {}),
        ...(message ? { message } : {}),
      };
    })
    .filter((slot) => slot.index > 0)
    .sort((left, right) => left.index - right.index);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isCreativeAsset(value: unknown): value is CreativeAsset {
  return isRecord(value) && typeof value.id === "string";
}

function isVideoAsset(value: unknown): value is VideoAsset {
  return isRecord(value) && typeof value.id === "string";
}

function numericValue(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function creativeImageIndex(asset: CreativeAsset): number {
  const metadata = isRecord(asset.metadata_json) ? asset.metadata_json : {};
  return numericValue(metadata.image_index) ?? 0;
}
