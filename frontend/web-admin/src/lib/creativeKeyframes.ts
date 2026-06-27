export const KEYFRAME_VARIANT_OPTIONS = [1, 2, 3] as const;
export const DEFAULT_KEYFRAME_VARIANT_COUNT = 1;
export const KEYFRAME_FRAMES_PER_VARIANT = 2;

export type KeyframeVariantCount = (typeof KEYFRAME_VARIANT_OPTIONS)[number];
export type CreativeGenerationMode = "standard" | "video_keyframe_variants";
export type KeyframeSlotStatus = "loading" | "done" | "error";

export type CreativeGenerationPlan = {
  count: number;
  size: string;
  generationMode: CreativeGenerationMode;
  isKeyframeVariant: boolean;
  variantCount?: KeyframeVariantCount;
  framesPerVariant?: number;
  videoDurationSeconds?: number;
};

export type KeyframeProgressSlot = {
  index: number;
  status: KeyframeSlotStatus;
};

export type KeyframePlanProgress = {
  group: KeyframeVariantCount;
  label: string;
  status: KeyframeSlotStatus;
  doneCount: number;
  total: number;
  slotIndices: number[];
};

export function normalizeKeyframeVariantCount(value: unknown): KeyframeVariantCount {
  const parsed = typeof value === "number" ? value : Number.parseInt(String(value ?? ""), 10);
  return isKeyframeVariantCount(parsed) ? parsed : DEFAULT_KEYFRAME_VARIANT_COUNT;
}

export function creativeGenerationPlan(
  durationSeconds: number,
  aspectRatio: string,
  keyframeVariantCount: KeyframeVariantCount = DEFAULT_KEYFRAME_VARIANT_COUNT,
): CreativeGenerationPlan {
  if (durationSeconds === 12) {
    return {
      count: keyframeVariantCount * KEYFRAME_FRAMES_PER_VARIANT,
      size: aspectRatio || "9:16",
      generationMode: "video_keyframe_variants",
      isKeyframeVariant: true,
      variantCount: keyframeVariantCount,
      framesPerVariant: KEYFRAME_FRAMES_PER_VARIANT,
      videoDurationSeconds: durationSeconds,
    };
  }
  return {
    count: 3,
    size: "1:1",
    generationMode: "standard",
    isKeyframeVariant: false,
  };
}

export function buildKeyframePlanProgress(
  slots: KeyframeProgressSlot[],
  keyframeVariantCount: KeyframeVariantCount,
  framesPerVariant = KEYFRAME_FRAMES_PER_VARIANT,
): KeyframePlanProgress[] {
  return KEYFRAME_VARIANT_OPTIONS.slice(0, keyframeVariantCount).map((group) => {
    const slotIndices = keyframeGroupSlotIndices(group, framesPerVariant);
    const groupSlots = slotIndices.map((index) => slots.find((slot) => slot.index === index));
    const doneCount = groupSlots.filter((slot) => slot?.status === "done").length;
    const hasError = groupSlots.some((slot) => slot?.status === "error");
    const status: KeyframeSlotStatus = hasError
      ? "error"
      : doneCount >= framesPerVariant
        ? "done"
        : "loading";
    return {
      group,
      label: `方案 ${group}`,
      status,
      doneCount,
      total: framesPerVariant,
      slotIndices,
    };
  });
}

export function keyframeGroupSlotIndices(
  group: number,
  framesPerVariant = KEYFRAME_FRAMES_PER_VARIANT,
): number[] {
  const start = (group - 1) * framesPerVariant + 1;
  return Array.from({ length: framesPerVariant }, (_, index) => start + index);
}

function isKeyframeVariantCount(value: number): value is KeyframeVariantCount {
  return KEYFRAME_VARIANT_OPTIONS.includes(value as KeyframeVariantCount);
}
