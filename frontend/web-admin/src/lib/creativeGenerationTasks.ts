export type ActiveImageGenerationTaskCache = {
  taskIds: string[];
  draftId: string;
};

export function keyframeTaskTargetGroups(
  variantCount: number,
  framesPerVariant = 2,
): number[][] {
  return Array.from({ length: Math.max(variantCount, 0) }, (_, index) => {
    const start = index * framesPerVariant + 1;
    return Array.from({ length: framesPerVariant }, (_, frameIndex) => start + frameIndex);
  });
}

export function normalizeActiveImageGenerationTaskCache(
  value: unknown,
): ActiveImageGenerationTaskCache | null {
  if (!isRecord(value)) return null;
  const draftId = typeof value.draftId === "string" ? value.draftId : "";
  const rawTaskIds = Array.isArray(value.taskIds)
    ? value.taskIds
    : typeof value.taskId === "string"
      ? [value.taskId]
      : [];
  const taskIds = rawTaskIds
    .filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    .filter((item, index, items) => items.indexOf(item) === index);
  return draftId && taskIds.length ? { taskIds, draftId } : null;
}

export function activeImageGenerationTaskCachePayload(
  entry: ActiveImageGenerationTaskCache,
): string {
  return JSON.stringify(entry);
}

export function targetIndicesMax(value: unknown): number {
  if (!Array.isArray(value)) return 0;
  return value.reduce((max, item) => {
    const numeric = typeof item === "number" ? item : Number(item);
    return Number.isFinite(numeric) ? Math.max(max, numeric) : max;
  }, 0);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
