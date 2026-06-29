import type { CopyDraft, CreativeAsset, Topic, VideoAsset } from "../types/domain";

const KEYFRAME_VARIANT_OPTIONS = [1, 2, 3] as const;
const KEYFRAME_FRAMES_PER_VARIANT = 2;
type KeyframeVariantCount = (typeof KEYFRAME_VARIANT_OPTIONS)[number];

export type CreativeSlotLike = {
  index: number;
  status: "loading" | "done" | "error";
  asset?: CreativeAsset;
  message?: string;
};

export type CreativeReviewKeyframeGroup<TSlot extends CreativeSlotLike = CreativeSlotLike> = {
  group: number;
  label: string;
  slots: TSlot[];
  assets: CreativeAsset[];
  complete: boolean;
  approved: boolean;
  status: "approved" | "rejected" | "generated" | "generating" | "error";
};

export type CreativeReviewState<TSlot extends CreativeSlotLike = CreativeSlotLike> = {
  visibleSlots: TSlot[];
  currentCreatives: CreativeAsset[];
  historyCreatives: CreativeAsset[];
  keyframeGroups: CreativeReviewKeyframeGroup<TSlot>[];
};

export type TopicScopedArtifacts = {
  selectedTopic: Topic | null;
  drafts: CopyDraft[];
  creatives: CreativeAsset[];
  videos: VideoAsset[];
};

export type VideoCreativeReferenceOption = {
  key: string;
  label: string;
  assetIds: string[];
  assets: CreativeAsset[];
  approved: boolean;
};

export function filterWorkflowArtifactsForTopic({
  selectedTopic,
  drafts,
  creatives,
  videos,
}: TopicScopedArtifacts): Omit<TopicScopedArtifacts, "selectedTopic"> {
  if (!selectedTopic) return { drafts, creatives, videos };

  const scopedDrafts = drafts.filter((draft) => draft.topic_id === selectedTopic.id);
  const draftIds = new Set(scopedDrafts.map((draft) => draft.id));
  const scopedCreatives = creatives.filter((asset) => draftIds.has(asset.draft_id));
  const creativeIds = new Set(scopedCreatives.map((asset) => asset.id));
  const scopedVideos = videos.filter((video) => {
    if (video.draft_id && draftIds.has(video.draft_id)) return true;
    return video.source_asset_ids.some((id) => creativeIds.has(id));
  });

  return {
    drafts: scopedDrafts,
    creatives: scopedCreatives,
    videos: scopedVideos,
  };
}

export function buildCreativeReviewState<TSlot extends CreativeSlotLike>(
  creatives: CreativeAsset[],
  activeSlots: TSlot[],
  expectedGroupCount?: KeyframeVariantCount,
): CreativeReviewState<TSlot> {
  const currentCreatives = currentCreativeAssets(creatives);
  const rawVisibleSlots = activeSlots.length ? activeSlots : slotsFromCreatives<TSlot>(currentCreatives);
  const visibleSlots = currentCreativeSlots(rawVisibleSlots);
  const visibleAssetIds = new Set(
    visibleSlots.map((slot) => slot.asset?.id).filter((id): id is string => Boolean(id)),
  );
  const historyCreatives = creatives.filter((asset) => !visibleAssetIds.has(asset.id));

  return {
    visibleSlots,
    currentCreatives: visibleSlots.map((slot) => slot.asset).filter((asset): asset is CreativeAsset => Boolean(asset)),
    historyCreatives,
    keyframeGroups: buildCreativeKeyframeGroups(visibleSlots, expectedGroupCount),
  };
}

export function videoCreativeReferenceOptions(
  approvedCreatives: CreativeAsset[],
): VideoCreativeReferenceOption[] {
  const state = buildCreativeReviewState(approvedCreatives, []);
  const approvedKeyframeGroups = state.keyframeGroups.filter((group) => group.complete && group.approved);
  if (approvedKeyframeGroups.length) {
    return approvedKeyframeGroups.map((group) => ({
      key: `scheme-${group.group}`,
      label: group.label,
      assetIds: group.assets.map((asset) => asset.id),
      assets: group.assets,
      approved: true,
    }));
  }

  return state.currentCreatives
    .filter((asset) => asset.status === "approved")
    .map((asset, index) => ({
      key: asset.id,
      label: `图 ${creativeImageIndex(asset, index + 1)}`,
      assetIds: [asset.id],
      assets: [asset],
      approved: true,
    }));
}

export function videoCreativeAssetIdsForSelection(
  approvedCreatives: CreativeAsset[],
  selectedCreativeIds: string[],
  maxReferenceImages: number,
): string[] {
  const approvedIds = new Set(
    approvedCreatives.filter((asset) => asset.status === "approved").map((asset) => asset.id),
  );
  const selected = selectedCreativeIds.filter((id) => approvedIds.has(id));
  const selectedSet = new Set(selected);
  const referenceOptions = videoCreativeReferenceOptions(approvedCreatives);
  const selectedKeyframeScheme = referenceOptions.find(
    (option) => option.assetIds.length > 1 && option.assetIds.some((id) => selectedSet.has(id)),
  );

  if (selectedKeyframeScheme) {
    return selectedKeyframeScheme.assetIds.slice(0, maxReferenceImages);
  }
  if (selected.length) return selected.slice(0, maxReferenceImages);

  const fallbackKeyframeScheme = referenceOptions.find((option) => option.assetIds.length > 1);
  if (fallbackKeyframeScheme) {
    return fallbackKeyframeScheme.assetIds.slice(0, maxReferenceImages);
  }
  return approvedCreatives
    .filter((asset) => asset.status === "approved")
    .slice(0, maxReferenceImages)
    .map((asset) => asset.id);
}

export function adPreviewCreativeOptions(
  creatives: CreativeAsset[],
  selectedDraft: CopyDraft | null,
): CreativeAsset[] {
  const draftId = selectedDraft?.id ?? null;
  return currentCreativeAssets(
    creatives.filter(
      (asset) =>
        Boolean(asset.url) &&
        asset.status === "approved" &&
        !isKeyframeVariantAsset(asset) &&
        (!draftId || asset.draft_id === draftId),
    ),
  );
}

export function currentCreativeAssets(creatives: CreativeAsset[]): CreativeAsset[] {
  const latestByKey = new Map<string, CreativeAsset>();
  for (const asset of creatives.slice().sort(compareCreativeNewestFirst)) {
    const key = creativeVersionKey(asset);
    if (!latestByKey.has(key)) latestByKey.set(key, asset);
  }
  return [...latestByKey.values()].sort(compareCreativeDisplayOrder);
}

export function isKeyframeVariantAsset(asset: CreativeAsset): boolean {
  return readText(asset.metadata_json.generation_mode) === "video_keyframe_variants";
}

export function creativeImageIndex(asset: CreativeAsset, fallback: number): number {
  return positiveInteger(asset.metadata_json.image_index) || fallback;
}

export function creativeKeyframeGroup(asset: CreativeAsset): number {
  return positiveInteger(asset.metadata_json.keyframe_group);
}

export function creativeKeyframePosition(asset?: CreativeAsset): number {
  if (!asset) return Number.MAX_SAFE_INTEGER;
  return positiveInteger(asset.metadata_json.keyframe_position) || Number.MAX_SAFE_INTEGER;
}

export function creativeKeyframeGroupSize(asset?: CreativeAsset): number {
  if (!asset) return KEYFRAME_FRAMES_PER_VARIANT;
  return positiveInteger(asset.metadata_json.keyframe_group_size) || KEYFRAME_FRAMES_PER_VARIANT;
}

function slotsFromCreatives<TSlot extends CreativeSlotLike>(creatives: CreativeAsset[]): TSlot[] {
  return creatives.map((asset, index) => ({
    index: creativeImageIndex(asset, index + 1),
    status: "done",
    asset,
  }) as TSlot);
}

function currentCreativeSlots<TSlot extends CreativeSlotLike>(slots: TSlot[]): TSlot[] {
  const latestByAssetKey = new Map<string, TSlot>();
  const emptySlots = new Map<number, TSlot>();

  for (const slot of slots) {
    if (!slot.asset) {
      if (!emptySlots.has(slot.index)) emptySlots.set(slot.index, slot);
      continue;
    }

    const key = creativeVersionKey(slot.asset);
    const current = latestByAssetKey.get(key);
    if (!current || compareCreativeNewestFirst(slot.asset, current.asset) < 0) {
      latestByAssetKey.set(key, slot);
    }
  }

  return [...latestByAssetKey.values(), ...emptySlots.values()].sort((left, right) => {
    const leftOrder = left.asset ? creativeDisplayOrder(left.asset, left.index) : left.index;
    const rightOrder = right.asset ? creativeDisplayOrder(right.asset, right.index) : right.index;
    if (leftOrder !== rightOrder) return leftOrder - rightOrder;
    return left.index - right.index;
  });
}

function buildCreativeKeyframeGroups<TSlot extends CreativeSlotLike>(
  slots: TSlot[],
  expectedGroupCount?: KeyframeVariantCount,
): CreativeReviewKeyframeGroup<TSlot>[] {
  const groups = new Map<number, TSlot[]>();
  const expectedGroups = expectedGroupCount
    ? new Set<number>(KEYFRAME_VARIANT_OPTIONS.slice(0, expectedGroupCount))
    : null;

  for (const slot of slots) {
    let group = 0;
    if (slot.asset) {
      if (!isKeyframeVariantAsset(slot.asset)) continue;
      group = creativeKeyframeGroup(slot.asset);
    } else if (expectedGroups) {
      const groupFromIndex = Math.ceil(slot.index / KEYFRAME_FRAMES_PER_VARIANT);
      group = expectedGroups.has(groupFromIndex) ? groupFromIndex : 0;
    }
    if (!group) continue;
    groups.set(group, [...(groups.get(group) ?? []), slot]);
  }

  return [...groups.entries()]
    .sort(([left], [right]) => left - right)
    .map(([group, groupSlots]) => {
      const slotsByPosition = groupSlots.slice().sort((left, right) => {
        const leftPosition = left.asset ? creativeKeyframePosition(left.asset) : left.index;
        const rightPosition = right.asset ? creativeKeyframePosition(right.asset) : right.index;
        return leftPosition - rightPosition;
      });
      const assets = slotsByPosition
        .map((slot) => slot.asset)
        .filter((asset): asset is CreativeAsset => Boolean(asset));
      const groupSize = assets[0] ? creativeKeyframeGroupSize(assets[0]) : KEYFRAME_FRAMES_PER_VARIANT;
      const complete = assets.length >= groupSize;
      const approved = complete && assets.every((asset) => asset.status === "approved");
      const hasRejected = assets.some((asset) => asset.status === "rejected");
      const hasError = slotsByPosition.some((slot) => slot.status === "error");
      const hasLoading = slotsByPosition.some((slot) => slot.status === "loading");
      return {
        group,
        label: `方案 ${group}`,
        slots: slotsByPosition,
        assets,
        complete,
        approved,
        status: approved
          ? "approved"
          : hasRejected
            ? "rejected"
            : hasError
              ? "error"
              : hasLoading
                ? "generating"
                : "generated",
      };
    });
}

function creativeVersionKey(asset: CreativeAsset): string {
  if (isKeyframeVariantAsset(asset)) {
    const group = creativeKeyframeGroup(asset);
    const position = creativeKeyframePosition(asset);
    if (group && Number.isFinite(position)) return `keyframe:${group}:${position}`;
  }
  const index = creativeImageIndex(asset, 0);
  return index ? `standard:${index}` : `asset:${asset.id}`;
}

function compareCreativeNewestFirst(left: CreativeAsset, right?: CreativeAsset): number {
  if (!right) return -1;
  const versionDiff = right.version - left.version;
  if (versionDiff !== 0) return versionDiff;
  return creativeTimestamp(right) - creativeTimestamp(left);
}

function compareCreativeDisplayOrder(left: CreativeAsset, right: CreativeAsset): number {
  const leftOrder = creativeDisplayOrder(left, 0);
  const rightOrder = creativeDisplayOrder(right, 0);
  if (leftOrder !== rightOrder) return leftOrder - rightOrder;
  return compareCreativeNewestFirst(left, right);
}

function creativeDisplayOrder(asset: CreativeAsset, fallback: number): number {
  if (isKeyframeVariantAsset(asset)) {
    const group = creativeKeyframeGroup(asset);
    const position = creativeKeyframePosition(asset);
    if (group && Number.isFinite(position)) return group * 100 + position;
  }
  return creativeImageIndex(asset, fallback);
}

function creativeTimestamp(asset: CreativeAsset): number {
  return Math.max(Date.parse(asset.updated_at), Date.parse(asset.created_at), 0);
}

function positiveInteger(value: unknown): number {
  const parsed = typeof value === "number" ? value : Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
}

function readText(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}
