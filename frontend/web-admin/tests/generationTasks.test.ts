import assert from "node:assert/strict";
import test from "node:test";

import * as generationTaskHelpers from "../src/lib/generationTasks.ts";
import {
  creativeAssetsFromGenerationTask,
  creativeSlotsFromGenerationTask,
  generationTaskIsActive,
  generationTaskListHasActiveTasks,
  generationTaskMonitorStats,
  generationTaskIsFinal,
  generationTaskIsSuccessful,
  filterGenerationTasksForDeletedWorkOrder,
  generationTaskQueueRiskClass,
  generationTaskQueueRiskLabel,
  generationTaskQueueLabel,
  generationTaskSummary,
  generationTaskStatusDisplay,
  generationTaskStatusLabel,
  mergeCreativeGenerationTaskSlots,
  videoStoryboardFromGenerationTask,
  videoStoryboardTextFromGenerationTask,
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
    display_context: {},
    reused_existing: false,
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
  };
}

test("generation task helpers summarize text queue statuses", () => {
  assert.equal(generationTaskIsActive(task("queued")), true);
  assert.equal(generationTaskIsActive(task("running")), true);
  assert.equal(generationTaskIsActive(task("succeeded")), false);
  assert.equal(generationTaskIsActive(task("failed")), false);
  assert.equal(generationTaskIsFinal(task("queued")), false);
  assert.equal(generationTaskIsFinal(task("succeeded")), true);
  assert.equal(generationTaskIsSuccessful(task("succeeded")), true);
  assert.equal(generationTaskSummary(task("queued"), "选题"), "选题已进入文本队列，等待处理。");
  assert.equal(generationTaskSummary(task("running"), "选题"), "选题正在生成中，请稍候。");
  assert.equal(generationTaskSummary(task("failed", "provider timeout"), "选题"), "选题生成失败：provider timeout");
  assert.equal(
    generationTaskSummary({ ...task("running"), reused_existing: true }, "选题"),
    "选题已有生成任务在处理，正在继续跟进原任务。",
  );
});

test("generation task helpers show automatic retry state before final failure", () => {
  const retryingTask = {
    ...task("queued", "Gateway image API request timed out"),
    queue_name: "image_queue",
    task_type: "image_generate",
    attempt_count: 1,
    max_attempts: 3,
    metadata: {
      auto_retry: {
        status: "scheduled",
        next_attempt: 2,
        max_attempts: 3,
        remaining_attempts: 2,
        delay_seconds: 10,
        last_error_code: "provider_timeout",
        last_error_message: "Gateway image API request timed out",
      },
    },
  } satisfies GenerationTask;

  assert.equal(generationTaskStatusDisplay(retryingTask), "\u81ea\u52a8\u91cd\u8bd5\u4e2d");
  assert.equal(
    generationTaskSummary(retryingTask, "\u56fe\u7247"),
    "\u56fe\u7247\u4e0a\u6b21\u751f\u6210\u9047\u5230\u6a21\u578b\u54cd\u5e94\u8d85\u65f6\uff0c\u5df2\u81ea\u52a8\u6392\u961f\u7b2c 2/3 \u6b21\u5c1d\u8bd5\u3002",
  );
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

test("generation task helper merges single-slot retry without dropping other keyframe slots", () => {
  const currentSlots = Array.from({ length: 6 }, (_, index) => ({
    index: index + 1,
    status: index === 1 ? ("error" as const) : ("done" as const),
    message: index === 1 ? "provider failed" : undefined,
  }));
  const taskSlots = [
    {
      index: 2,
      status: "loading" as const,
    },
  ];

  const merged = mergeCreativeGenerationTaskSlots(currentSlots, taskSlots, {
    minimumSlotCount: 6,
  });

  assert.equal(merged.length, 6);
  assert.equal(merged[0].index, 1);
  assert.equal(merged[0].status, "done");
  assert.equal(merged[1].index, 2);
  assert.equal(merged[1].status, "loading");
  assert.deepEqual(
    merged.map((slot) => slot.index),
    [1, 2, 3, 4, 5, 6],
  );
});

test("generation task helper keeps six keyframe slots for pair task target indices", () => {
  const currentSlots = [
    { index: 1, status: "loading" as const },
    { index: 2, status: "loading" as const },
    { index: 3, status: "loading" as const },
    { index: 4, status: "loading" as const },
    { index: 5, status: "loading" as const },
    { index: 6, status: "loading" as const },
  ];
  const taskSlots = [
    { index: 3, status: "done" as const },
    { index: 4, status: "done" as const },
  ];

  const merged = mergeCreativeGenerationTaskSlots(currentSlots, taskSlots, {
    minimumSlotCount: 6,
  });

  assert.deepEqual(
    merged.map((slot) => [slot.index, slot.status]),
    [
      [1, "loading"],
      [2, "loading"],
      [3, "done"],
      [4, "done"],
      [5, "loading"],
      [6, "loading"],
    ],
  );
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

test("generation task helpers restore video storyboard from task result", () => {
  const storyboardTask = task("succeeded");
  storyboardTask.task_type = "video_storyboard_generate";
  storyboardTask.business_type = "copy_draft";
  storyboardTask.business_id = "draft-1";
  storyboardTask.result = {
    video_storyboard: {
      campaign_id: "campaign-1",
      draft_id: "draft-1",
      creative_asset_ids: [],
      duration_seconds: 12,
      aspect_ratio: "9:16",
      storyboard: [{ scene_index: 1, visual: "Open with the app benefit." }],
      prompt: "Scene 1: Open with the app benefit.",
      metadata_json: { provider: "fake" },
    },
    storyboard_text: "镜头 1\n画面：Open with the app benefit.",
  };

  const storyboard = videoStoryboardFromGenerationTask(storyboardTask);

  assert.equal(storyboard?.campaign_id, "campaign-1");
  assert.equal(storyboard?.duration_seconds, 12);
  assert.equal(storyboard?.storyboard[0].visual, "Open with the app benefit.");
  assert.equal(
    videoStoryboardTextFromGenerationTask(storyboardTask),
    "镜头 1\n画面：Open with the app benefit.",
  );
});

test("generation task monitor helpers label queues and count retryable failures", () => {
  const tasks = [
    task("queued"),
    { ...task("running"), id: "task-2", queue_name: "image_queue" },
    {
      ...task("failed", "provider timeout"),
      id: "task-3",
      queue_name: "image_queue",
      retryable: true,
    },
    {
      ...task("succeeded"),
      id: "task-4",
      queue_name: "callback_queue",
      task_type: "ad_generation_callback",
    },
  ];

  assert.equal(generationTaskQueueLabel("text_queue"), "文本队列");
  assert.equal(generationTaskQueueLabel("image_queue"), "图片队列");
  assert.equal(generationTaskQueueLabel("video_queue"), "视频队列");
  assert.equal(generationTaskQueueLabel("callback_queue"), "回调队列");
  assert.equal(generationTaskStatusLabel("queued"), "等待中");
  assert.equal(generationTaskStatusLabel("running"), "运行中");
  assert.equal(generationTaskStatusLabel("failed"), "失败");

  assert.deepEqual(generationTaskMonitorStats(tasks), {
    activeCount: 2,
    failedCount: 1,
    retryableFailedCount: 1,
    succeededCount: 1,
  });
});

test("generation task list active helper only treats queued and running as active", () => {
  assert.equal(
    generationTaskListHasActiveTasks([
      task("succeeded"),
      { ...task("failed"), id: "task-failed" },
    ]),
    false,
  );
  assert.equal(
    generationTaskListHasActiveTasks([
      task("succeeded"),
      { ...task("queued"), id: "task-queued" },
    ]),
    true,
  );
});

test("filters tasks linked to a deleted work order or campaign", () => {
  const tasks = [
    { ...task("running"), id: "job-business", business_id: "job-deleted" },
    { ...task("queued"), id: "payload-job", payload: { job_id: "job-deleted" } },
    {
      ...task("failed"),
      id: "display-job",
      display_context: { ad_generation_job_id: "job-deleted" },
    },
    { ...task("running"), id: "campaign-direct", campaign_id: "campaign-deleted" },
    {
      ...task("queued"),
      id: "display-campaign",
      display_context: { campaign_id: "campaign-deleted" },
    },
    { ...task("running"), id: "other", business_id: "job-other", campaign_id: "campaign-other" },
  ];

  const remaining = filterGenerationTasksForDeletedWorkOrder(tasks, {
    jobId: "job-deleted",
    campaignId: "campaign-deleted",
  });

  assert.deepEqual(remaining.map((item) => item.id), ["other"]);
});

test("generation task monitor helpers label queue pressure risk", () => {
  assert.equal(generationTaskQueueRiskLabel("high"), "\u9ad8\u98ce\u9669");
  assert.equal(generationTaskQueueRiskLabel("medium"), "\u6709\u79ef\u538b");
  assert.equal(generationTaskQueueRiskLabel("low"), "\u6b63\u5e38");
  assert.equal(generationTaskQueueRiskLabel(undefined), "\u6b63\u5e38");
  assert.equal(generationTaskQueueRiskClass("high"), "high");
  assert.equal(generationTaskQueueRiskClass("medium"), "medium");
  assert.equal(generationTaskQueueRiskClass("unknown"), "low");
});

test("generation task helpers label task types in business language", () => {
  const typeLabel = generationTaskHelpers.generationTaskTypeLabel as
    | ((taskType: string) => string)
    | undefined;

  assert.equal(typeof typeLabel, "function");
  assert.equal(typeLabel?.("topic_generate"), "\u9009\u9898\u751f\u6210");
  assert.equal(typeLabel?.("copy_generate"), "\u6587\u6848\u751f\u6210");
  assert.equal(typeLabel?.("copy_revise"), "\u6587\u6848\u6539\u5199");
  assert.equal(typeLabel?.("video_storyboard_generate"), "\u811a\u672c\u751f\u6210");
  assert.equal(typeLabel?.("video_storyboard_rewrite"), "\u811a\u672c\u6539\u5199");
  assert.equal(typeLabel?.("image_generate"), "\u56fe\u7247\u751f\u6210");
  assert.equal(typeLabel?.("video_generate"), "\u89c6\u9891\u751f\u6210");
  assert.equal(typeLabel?.("ad_generation_callback"), "\u56de\u8c03\u5916\u90e8\u7cfb\u7edf");
  assert.equal(typeLabel?.("unknown_task"), "unknown_task");
});

test("generation task helpers explain failure codes with operator actions", () => {
  const failureAdvice = generationTaskHelpers.generationTaskFailureAdvice as
    | ((
        task: Pick<GenerationTask, "error_code" | "error_message" | "retryable">,
      ) => { title: string; detail: string; action: string })
    | undefined;

  assert.equal(typeof failureAdvice, "function");
  assert.deepEqual(
    failureAdvice?.({
      error_code: "provider_429",
      error_message: "rate limit",
      retryable: true,
    }),
    {
      title: "\u6a21\u578b\u9650\u6d41",
      detail: "\u5f53\u524d\u751f\u6210\u5e76\u53d1\u8f83\u9ad8\uff0c\u4f9b\u5e94\u5546\u6682\u65f6\u62d2\u7edd\u5904\u7406\u3002",
      action: "\u53ef\u4ee5\u7a0d\u7b49\u540e\u70b9\u51fb\u91cd\u8bd5\uff0c\u6216\u964d\u4f4e\u540c\u65f6\u751f\u6210\u7684\u4efb\u52a1\u6570\u3002",
    },
  );
  assert.deepEqual(
    failureAdvice?.({
      error_code: "external_url_unreachable",
      error_message: "landing page failed",
      retryable: false,
    }),
    {
      title: "\u5916\u90e8\u94fe\u63a5\u4e0d\u53ef\u8fbe",
      detail: "\u7cfb\u7edf\u65e0\u6cd5\u8bbf\u95ee\u5de5\u5355\u3001\u7d20\u6750\u6216\u843d\u5730\u9875\u91cc\u7684\u5916\u90e8\u5730\u5740\u3002",
      action: "\u5148\u68c0\u67e5 URL \u662f\u5426\u53ef\u6253\u5f00\uff0c\u4fee\u6b63\u540e\u91cd\u65b0\u751f\u6210\u6216\u91cd\u8bd5\u4efb\u52a1\u3002",
    },
  );
  assert.deepEqual(
    failureAdvice?.({
      error_code: "task_interrupted",
      error_message: "backend restarted",
      retryable: true,
    }),
    {
      title: "\u540e\u53f0\u4efb\u52a1\u88ab\u4e2d\u65ad",
      detail: "\u540e\u7aef\u53ef\u80fd\u5728\u4efb\u52a1\u6267\u884c\u65f6\u91cd\u542f\uff0c\u7cfb\u7edf\u5df2\u5c06\u8fd9\u4e2a\u8fd0\u884c\u4e2d\u7684\u4efb\u52a1\u6807\u8bb0\u4e3a\u53ef\u91cd\u8bd5\u5931\u8d25\u3002",
      action: "\u5148\u786e\u8ba4\u5bf9\u5e94\u7684\u56fe\u7247\u3001\u9009\u9898\u6216\u6587\u6848\u662f\u5426\u5df2\u7ecf\u751f\u6210\uff1b\u5982\u679c\u672a\u751f\u6210\uff0c\u518d\u70b9\u51fb\u91cd\u8bd5\u3002",
    },
  );
});
