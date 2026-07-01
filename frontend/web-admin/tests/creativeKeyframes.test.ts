import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DEFAULT_KEYFRAME_VARIANT_COUNT,
  buildKeyframePlanProgress,
  creativeGenerationPlan,
  imageGenerationPlanForMode,
  keyframeGroupSlotIndices,
  normalizeKeyframeVariantCount,
} from "../src/lib/creativeKeyframes.ts";

test("defaults video keyframe generation to three two-frame方案", () => {
  const plan = creativeGenerationPlan(12, "9:16");

  assert.equal(DEFAULT_KEYFRAME_VARIANT_COUNT, 3);
  assert.equal(plan.isKeyframeVariant, true);
  assert.equal(plan.variantCount, 3);
  assert.equal(plan.framesPerVariant, 2);
  assert.equal(plan.count, 6);
});

test("supports one two or three keyframe方案 options", () => {
  assert.equal(normalizeKeyframeVariantCount(1), 1);
  assert.equal(normalizeKeyframeVariantCount(2), 2);
  assert.equal(normalizeKeyframeVariantCount(3), 3);
  assert.equal(normalizeKeyframeVariantCount(6), 3);

  assert.equal(creativeGenerationPlan(12, "9:16", 3).count, 6);
});

test("maps image slots into方案 level progress", () => {
  const progress = buildKeyframePlanProgress(
    [
      { index: 1, status: "done" },
      { index: 2, status: "done" },
      { index: 3, status: "loading" },
      { index: 4, status: "loading" },
      { index: 5, status: "error" },
      { index: 6, status: "loading" },
    ],
    3,
  );

  assert.deepEqual(
    progress.map((item) => ({
      group: item.group,
      label: item.label,
      status: item.status,
      doneCount: item.doneCount,
      total: item.total,
      slotIndices: item.slotIndices,
    })),
    [
      { group: 1, label: "方案 1", status: "done", doneCount: 2, total: 2, slotIndices: [1, 2] },
      { group: 2, label: "方案 2", status: "loading", doneCount: 0, total: 2, slotIndices: [3, 4] },
      { group: 3, label: "方案 3", status: "error", doneCount: 0, total: 2, slotIndices: [5, 6] },
    ],
  );
});

test("returns the slot indices for regenerating one方案 as a group", () => {
  assert.deepEqual(keyframeGroupSlotIndices(1), [1, 2]);
  assert.deepEqual(keyframeGroupSlotIndices(2), [3, 4]);
  assert.deepEqual(keyframeGroupSlotIndices(3), [5, 6]);
});

test("copy image mode generates exactly two standard images without keyframe metadata", () => {
  const plan = imageGenerationPlanForMode("copy_images", 12, "9:16", 3);

  assert.equal(plan.count, 2);
  assert.equal(plan.size, "1:1");
  assert.equal(plan.generationMode, "standard");
  assert.equal(plan.isKeyframeVariant, false);
  assert.equal(plan.variantCount, undefined);
  assert.equal(plan.framesPerVariant, undefined);
});

test("video keyframe mode preserves selectable first and last frame schemes", () => {
  const plan = imageGenerationPlanForMode("video_keyframes", 12, "9:16", 3);

  assert.equal(plan.count, 6);
  assert.equal(plan.size, "9:16");
  assert.equal(plan.generationMode, "video_keyframe_variants");
  assert.equal(plan.isKeyframeVariant, true);
  assert.equal(plan.variantCount, 3);
  assert.equal(plan.framesPerVariant, 2);
  assert.equal(plan.videoDurationSeconds, 12);
});
