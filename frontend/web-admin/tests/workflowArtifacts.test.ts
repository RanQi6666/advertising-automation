import assert from "node:assert/strict";
import { test } from "node:test";

import {
  adPreviewCreativeOptions,
  buildCreativeReviewState,
  filterWorkflowArtifactsForTopic,
  videoCreativeReferenceOptions,
} from "../src/lib/workflowArtifacts.ts";
import type { CopyDraft, CreativeAsset, Topic, VideoAsset } from "../src/types/domain.ts";

function topic(overrides: Partial<Topic>): Topic {
  return {
    id: "topic-1",
    campaign_id: "campaign-1",
    title: "Topic",
    angle: "Angle",
    audience: null,
    selling_points: [],
    risk_notes: null,
    rationale: null,
    status: "selected",
    score: null,
    source_data: {},
    created_at: "2026-06-27T00:00:00.000Z",
    updated_at: "2026-06-27T00:00:00.000Z",
    ...overrides,
  };
}

function draft(overrides: Partial<CopyDraft>): CopyDraft {
  return {
    id: "draft-1",
    campaign_id: "campaign-1",
    topic_id: "topic-1",
    body: "body",
    primary_text: "primary",
    headline: "headline",
    description: "description",
    cta: "Learn More",
    status: "approved",
    version: 1,
    model_name: null,
    prompt_version: null,
    metadata_json: {},
    created_at: "2026-06-27T00:00:00.000Z",
    updated_at: "2026-06-27T00:00:00.000Z",
    ...overrides,
  };
}

function creative(overrides: Partial<CreativeAsset>): CreativeAsset {
  return {
    id: "creative-1",
    campaign_id: "campaign-1",
    draft_id: "draft-1",
    kind: "image",
    url: "local://creative-1.png",
    storage_key: null,
    prompt: "prompt",
    alt_text: null,
    size: "9:16",
    status: "generated",
    version: 1,
    metadata_json: {},
    created_at: "2026-06-27T00:00:00.000Z",
    updated_at: "2026-06-27T00:00:00.000Z",
    ...overrides,
  };
}

function keyframeCreative(
  id: string,
  group: number,
  position: number,
  version: number,
  status = "generated",
): CreativeAsset {
  return creative({
    id,
    status,
    version,
    created_at: `2026-06-27T00:0${version}:0${position}.000Z`,
    updated_at: `2026-06-27T00:0${version}:0${position}.000Z`,
    metadata_json: {
      image_index: (group - 1) * 2 + position,
      generation_mode: "video_keyframe_variants",
      keyframe_group: group,
      keyframe_position: position,
      keyframe_group_size: 2,
    },
  });
}

function video(overrides: Partial<VideoAsset>): VideoAsset {
  return {
    id: "video-1",
    campaign_id: "campaign-1",
    draft_id: "draft-1",
    source_asset_ids: [],
    url: null,
    storage_key: null,
    prompt: null,
    storyboard: [],
    duration_seconds: 12,
    aspect_ratio: "9:16",
    status: "generated",
    provider_job_id: null,
    error_message: null,
    version: 1,
    metadata_json: {},
    created_at: "2026-06-27T00:00:00.000Z",
    updated_at: "2026-06-27T00:00:00.000Z",
    ...overrides,
  };
}

test("keeps only the newest two images in a keyframe scheme and moves older versions to history", () => {
  const olderFirst = keyframeCreative("scheme-2-first-v1", 2, 1, 1, "approved");
  const olderLast = keyframeCreative("scheme-2-last-v1", 2, 2, 1, "approved");
  const newestFirst = keyframeCreative("scheme-2-first-v2", 2, 1, 2);
  const newestLast = keyframeCreative("scheme-2-last-v2", 2, 2, 2);

  const state = buildCreativeReviewState([olderFirst, newestFirst, olderLast, newestLast], []);
  const scheme = state.keyframeGroups.find((item) => item.group === 2);

  assert.ok(scheme);
  assert.deepEqual(
    scheme.assets.map((asset) => asset.id),
    ["scheme-2-first-v2", "scheme-2-last-v2"],
  );
  assert.deepEqual(
    state.historyCreatives.map((asset) => asset.id).sort(),
    ["scheme-2-first-v1", "scheme-2-last-v1"],
  );
});

test("filters drafts creatives and videos to the selected topic only", () => {
  const selected = topic({ id: "topic-selected", status: "selected" });
  const hidden = topic({ id: "topic-hidden", status: "proposed" });
  const selectedDraft = draft({ id: "draft-selected", topic_id: selected.id });
  const hiddenDraft = draft({ id: "draft-hidden", topic_id: hidden.id });
  const selectedCreative = creative({ id: "creative-selected", draft_id: selectedDraft.id });
  const hiddenCreative = creative({ id: "creative-hidden", draft_id: hiddenDraft.id });
  const selectedVideo = video({
    id: "video-selected",
    draft_id: selectedDraft.id,
    source_asset_ids: [selectedCreative.id],
  });
  const hiddenVideo = video({
    id: "video-hidden",
    draft_id: hiddenDraft.id,
    source_asset_ids: [hiddenCreative.id],
  });

  const result = filterWorkflowArtifactsForTopic({
    selectedTopic: selected,
    drafts: [selectedDraft, hiddenDraft],
    creatives: [selectedCreative, hiddenCreative],
    videos: [selectedVideo, hiddenVideo],
  });

  assert.deepEqual(result.drafts.map((item) => item.id), ["draft-selected"]);
  assert.deepEqual(result.creatives.map((item) => item.id), ["creative-selected"]);
  assert.deepEqual(result.videos.map((item) => item.id), ["video-selected"]);
});

test("builds video reference options as approved keyframe schemes", () => {
  const group1First = keyframeCreative("group-1-first", 1, 1, 1, "approved");
  const group1Last = keyframeCreative("group-1-last", 1, 2, 1, "approved");
  const group2First = keyframeCreative("group-2-first", 2, 1, 1, "approved");
  const group2Last = keyframeCreative("group-2-last", 2, 2, 1, "generated");

  const options = videoCreativeReferenceOptions([group2First, group1Last, group1First, group2Last]);

  assert.deepEqual(options, [
    {
      key: "scheme-1",
      label: "方案 1",
      assetIds: ["group-1-first", "group-1-last"],
      assets: [group1First, group1Last],
      approved: true,
    },
  ]);
});

test("ad preview options include approved normal images and exclude keyframe assets", () => {
  const selectedDraft = draft({ id: "draft-preview" });
  const approvedNormal = creative({
    id: "approved-normal",
    draft_id: selectedDraft.id,
    status: "approved",
    metadata_json: { image_index: 1 },
  });
  const generatedNormal = creative({
    id: "generated-normal",
    draft_id: selectedDraft.id,
    status: "generated",
    metadata_json: { image_index: 2 },
  });
  const keyframe = keyframeCreative("approved-keyframe", 1, 1, 1, "approved");

  const options = adPreviewCreativeOptions([keyframe, generatedNormal, approvedNormal], selectedDraft);

  assert.deepEqual(options.map((asset) => asset.id), ["approved-normal"]);
});
