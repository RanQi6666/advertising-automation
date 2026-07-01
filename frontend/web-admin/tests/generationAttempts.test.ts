import assert from "node:assert/strict";
import { test } from "node:test";

import {
  generationAttemptIsRecoverable,
  generationAttemptSummary,
  type GenerationAttempt,
} from "../src/lib/generationAttempts.ts";

function attempt(overrides: Partial<GenerationAttempt>): GenerationAttempt {
  return {
    id: "attempt-1",
    business_type: "image",
    business_id: "draft-1",
    job_id: null,
    campaign_id: "campaign-1",
    stage: "creative_image_generation",
    status: "running",
    total_count: 6,
    success_count: 0,
    failed_count: 0,
    provider: null,
    model: null,
    error_code: null,
    error_message: null,
    retryable: false,
    metadata: {},
    started_at: "2026-07-01T00:00:00Z",
    finished_at: null,
    duration_ms: null,
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
    ...overrides,
  };
}

test("treats succeeded and partial attempts as recoverable after stream interruption", () => {
  assert.equal(generationAttemptIsRecoverable(attempt({ status: "succeeded" })), true);
  assert.equal(
    generationAttemptIsRecoverable(
      attempt({ status: "partial_succeeded", success_count: 4, failed_count: 2 }),
    ),
    true,
  );
  assert.equal(generationAttemptIsRecoverable(attempt({ status: "failed" })), false);
});

test("summarizes partial image attempts without calling the whole generation failed", () => {
  assert.equal(
    generationAttemptSummary(
      attempt({ status: "partial_succeeded", success_count: 4, failed_count: 2 }),
      "图片",
    ),
    "后台已生成 4/6 个图片，未完成部分可重试。",
  );
});
