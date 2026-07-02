import assert from "node:assert/strict";
import test from "node:test";

import {
  activeImageGenerationTaskCachePayload,
  keyframeTaskTargetGroups,
  normalizeActiveImageGenerationTaskCache,
  targetIndicesMax,
} from "../src/lib/creativeGenerationTasks.ts";

test("builds one target index pair per keyframe scheme", () => {
  assert.deepEqual(keyframeTaskTargetGroups(3), [
    [1, 2],
    [3, 4],
    [5, 6],
  ]);
  assert.deepEqual(keyframeTaskTargetGroups(2), [
    [1, 2],
    [3, 4],
  ]);
});

test("normalizes active image task cache with backwards compatibility", () => {
  assert.deepEqual(
    normalizeActiveImageGenerationTaskCache({
      taskId: "legacy-task",
      draftId: "draft-1",
    }),
    { taskIds: ["legacy-task"], draftId: "draft-1" },
  );
  assert.deepEqual(
    normalizeActiveImageGenerationTaskCache({
      taskIds: ["task-1", "task-2", "task-1"],
      draftId: "draft-1",
    }),
    { taskIds: ["task-1", "task-2"], draftId: "draft-1" },
  );
});

test("serializes active image task cache", () => {
  assert.equal(
    activeImageGenerationTaskCachePayload({
      taskIds: ["task-1", "task-2"],
      draftId: "draft-1",
    }),
    JSON.stringify({ taskIds: ["task-1", "task-2"], draftId: "draft-1" }),
  );
});

test("reads target index max from arrays", () => {
  assert.equal(targetIndicesMax([3, 4]), 4);
  assert.equal(targetIndicesMax(["5", "6"]), 6);
  assert.equal(targetIndicesMax([]), 0);
  assert.equal(targetIndicesMax(null), 0);
});
