import assert from "node:assert/strict";
import test from "node:test";

import {
  creativeAssetsFromGenerationTask,
  creativeSlotsFromGenerationTask,
  generationTaskIsFinal,
  generationTaskIsSuccessful,
  generationTaskSummary,
  videoAssetFromGenerationTask,
  type GenerationTask,
} from "../src/lib/generationTasks.ts";

function task(status: GenerationTask["status"], errorMessage: string | null = null): GenerationTask {
  return {
    id: "task-1",
    queue_name: "text_queue",
    task_type: "topic_generate",
    business_type: "campaign",
    business_id: "campaign-1",
    campaign_id: "campaign-1",
    status,
    priority: 0,
    payload: {},
    result: null,
    error_code: null,
    error_message: errorMessage,
    retryable: false,
    attempt_count: 0,
    max_attempts: 2,
    queued_at: "2026-07-01T00:00:00Z",
    started_at: null,
    finished_at: null,
    duration_ms: null,
    metadata: {},
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
  };
}

test("generation task helpers summarize text queue statuses", () => {
  assert.equal(generationTaskIsFinal(task("queued")), false);
  assert.equal(generationTaskIsFinal(task("succeeded")), true);
  assert.equal(generationTaskIsSuccessful(task("succeeded")), true);
  assert.equal(generationTaskSummary(task("queued"), "选题"), "选题已进入文本队列，等待处理。");
  assert.equal(generationTaskSummary(task("running"), "选题"), "选题正在生成中，请稍候。");
  assert.equal(generationTaskSummary(task("failed", "provider timeout"), "选题"), "选题生成失败：provider timeout");
});

test("generation task helpers restore image slots from task result", () => {
  const imageTask = task("succeeded");
  imageTask.queue_name = "image_queue";
  imageTask.task_type = "image_generate";
  imageTask.result = {
    total_count: 3,
    generated_count: 2,
    failed_count: 1,
    assets: [
      {
        index: 1,
        asset: {
          id: "creative-1",
          campaign_id: "campaign-1",
          draft_id: "draft-1",
          kind: "image",
          url: "fake://image-1",
          storage_key: "fake://image-1",
          prompt: "Prompt 1",
          alt_text: "Image 1",
          size: "1:1",
          status: "generated",
          version: 1,
          metadata_json: { image_index: 1 },
          created_at: "2026-07-01T00:00:00Z",
          updated_at: "2026-07-01T00:00:00Z",
        },
      },
      {
        index: 3,
        asset: {
          id: "creative-3",
          campaign_id: "campaign-1",
          draft_id: "draft-1",
          kind: "image",
          url: "fake://image-3",
          storage_key: "fake://image-3",
          prompt: "Prompt 3",
          alt_text: "Image 3",
          size: "1:1",
          status: "generated",
          version: 1,
          metadata_json: { image_index: 3 },
          created_at: "2026-07-01T00:00:00Z",
          updated_at: "2026-07-01T00:00:00Z",
        },
      },
    ],
    slots: [
      { index: 1, status: "done", asset_id: "creative-1" },
      { index: 2, status: "error", message: "provider failed for slot 2" },
      { index: 3, status: "done", asset_id: "creative-3" },
    ],
  };

  const assets = creativeAssetsFromGenerationTask(imageTask);
  const slots = creativeSlotsFromGenerationTask(imageTask);

  assert.deepEqual(assets.map((asset) => asset.id), ["creative-1", "creative-3"]);
  assert.equal(slots.length, 3);
  assert.equal(slots[0].status, "done");
  assert.equal(slots[0].asset?.id, "creative-1");
  assert.equal(slots[1].status, "error");
  assert.equal(slots[1].message, "provider failed for slot 2");
  assert.equal(slots[2].status, "done");
  assert.equal(slots[2].asset?.id, "creative-3");
});

test("generation task helpers restore video asset from task result", () => {
  const videoTask = task("queued");
  videoTask.queue_name = "video_queue";
  videoTask.task_type = "video_generate";
  videoTask.business_type = "video_asset";
  videoTask.business_id = "video-1";

  assert.equal(generationTaskSummary(videoTask, "视频"), "视频已进入视频队列，等待处理。");

  videoTask.status = "succeeded";
  videoTask.result = {
    video_id: "video-1",
    status: "generating",
    provider_job_id: "provider-video-job-1",
    video: {
      id: "video-1",
      campaign_id: "campaign-1",
      draft_id: "draft-1",
      source_asset_ids: ["creative-1", "creative-2"],
      url: null,
      storage_key: null,
      prompt: "Create a short ad video",
      storyboard: [],
      duration_seconds: 12,
      aspect_ratio: "9:16",
      status: "generating",
      provider_job_id: "provider-video-job-1",
      error_message: null,
      version: 1,
      metadata_json: { video_provider: "fake" },
      created_at: "2026-07-01T00:00:00Z",
      updated_at: "2026-07-01T00:00:00Z",
    },
  };

  const video = videoAssetFromGenerationTask(videoTask);

  assert.equal(video?.id, "video-1");
  assert.equal(video?.status, "generating");
  assert.equal(video?.provider_job_id, "provider-video-job-1");
});
