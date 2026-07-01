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
