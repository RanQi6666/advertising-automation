import assert from "node:assert/strict";
import test from "node:test";

import {
  generationTaskIsFinal,
  generationTaskIsSuccessful,
  generationTaskSummary,
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
