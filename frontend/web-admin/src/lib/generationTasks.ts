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
  };
};

export type GenerationTaskMonitorStats = {
  activeCount: number;
  failedCount: number;
  retryableFailedCount: number;
  succeededCount: number;
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
