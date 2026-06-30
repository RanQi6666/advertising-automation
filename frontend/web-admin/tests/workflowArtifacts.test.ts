import assert from "node:assert/strict";
import { test } from "node:test";

import {
  adPreviewCreativeOptions,
  adGenerationJobNumber,
  buildCreativeReviewState,
  filterWorkflowArtifactsForTopic,
  videoCreativeAssetIdsForSelection,
  videoCreativeReferenceOptions,
  workflowRequiresVideo,
} from "../src/lib/workflowArtifacts.ts";
import type { AdGenerationJob, CopyDraft, CreativeAsset, Topic, VideoAsset } from "../src/types/domain.ts";

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

function adGenerationJob(overrides: Partial<AdGenerationJob>): AdGenerationJob {
  return {
    id: "job-1",
    external_order_id: "order-1",
    status: "image_review",
    callback_url: null,
    request_payload: {
      preferences: {
        creative_type: "image",
        video_required: false,
      },
    },
    result_payload: {},
    error_message: null,
    started_at: null,
    completed_at: null,
    metadata_json: {},
    review_url: null,
    return_url: null,
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

test("filters workflow artifacts to the selected campaign before topic scoping", () => {
  const currentDraft = draft({ id: "draft-current", campaign_id: "campaign-current" });
  const otherDraft = draft({ id: "draft-other", campaign_id: "campaign-other" });
  const currentCreative = creative({
    id: "creative-current",
    campaign_id: "campaign-current",
    draft_id: currentDraft.id,
  });
  const otherCreative = creative({
    id: "creative-other",
    campaign_id: "campaign-other",
    draft_id: otherDraft.id,
  });
  const currentVideo = video({
    id: "video-current",
    campaign_id: "campaign-current",
    draft_id: currentDraft.id,
    status: "generated",
  });
  const otherVideo = video({
    id: "video-other-approved",
    campaign_id: "campaign-other",
    draft_id: otherDraft.id,
    source_asset_ids: [otherCreative.id],
    status: "approved",
  });

  const result = filterWorkflowArtifactsForTopic({
    selectedCampaignId: "campaign-current",
    selectedTopic: null,
    drafts: [currentDraft, otherDraft],
    creatives: [currentCreative, otherCreative],
    videos: [otherVideo, currentVideo],
  });

  assert.deepEqual(result.drafts.map((item) => item.id), ["draft-current"]);
  assert.deepEqual(result.creatives.map((item) => item.id), ["creative-current"]);
  assert.deepEqual(result.videos.map((item) => item.id), ["video-current"]);
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

test("expands partial keyframe selection into the full approved video scheme", () => {
  const group1First = keyframeCreative("group-1-first", 1, 1, 1, "approved");
  const group1Last = keyframeCreative("group-1-last", 1, 2, 1, "approved");
  const group2First = keyframeCreative("group-2-first", 2, 1, 1, "approved");
  const group2Last = keyframeCreative("group-2-last", 2, 2, 1, "generated");

  const selected = videoCreativeAssetIdsForSelection(
    [group1First, group1Last, group2First, group2Last],
    ["group-1-first"],
    2,
  );

  assert.deepEqual(selected, ["group-1-first", "group-1-last"]);
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

test("video keyframe assets keep the workflow in the video step even when job preferences default to image", () => {
  const firstFrame = keyframeCreative("group-1-first", 1, 1, 1, "approved");
  const lastFrame = keyframeCreative("group-1-last", 1, 2, 1, "approved");

  assert.equal(workflowRequiresVideo(adGenerationJob({}), [firstFrame, lastFrame]), true);
});

test("normal image-only jobs can still skip the video step", () => {
  const approvedImage = creative({
    id: "approved-normal",
    status: "approved",
    metadata_json: { image_index: 1 },
  });

  assert.equal(workflowRequiresVideo(adGenerationJob({}), [approvedImage]), false);
});

test("formats work order numbers as chronological three digit serials", () => {
  const first = adGenerationJob({
    id: "abc0bc6e-0000",
    created_at: "2026-06-27T00:00:00.000Z",
  });
  const second = adGenerationJob({
    id: "7cfd301c-0000",
    created_at: "2026-06-27T00:01:00.000Z",
  });

  assert.equal(adGenerationJobNumber(first, [second, first]), "001");
  assert.equal(adGenerationJobNumber(second, [second, first]), "002");
});
