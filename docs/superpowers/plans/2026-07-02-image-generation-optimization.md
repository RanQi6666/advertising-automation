# Image Generation Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split video keyframe image generation into方案级 tasks, preserve single-image retry, add backend timing logs, and raise image provider concurrency from 2 to 3 safely.

**Architecture:** Backend gains a `target_indices` request field so one image task can generate a two-slot keyframe方案 such as `[3, 4]`. Frontend launches one task per keyframe方案 and merges each task result into the existing six-slot review UI. Backend timing stays in logs and task internals; the UI continues to show business-level方案 and image slots only.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy async sessions, Celery generation tasks, React 19, TypeScript, Node test runner, pytest.

## Global Constraints

- Do not commit `AGENTS.md`, `.env`, `.env.production`, real tokens, API keys, database passwords, or aaPanel credentials.
- Do not show technical statuses like `brief 生成中` or `图片下载保存中` in the frontend.
- Keep `/integrations/material-generation/images` public response shape unchanged.
- Keep `creative_assets` as the generated image asset table.
- Do not set image provider concurrency directly to 6.
- Use `MODEL_PROVIDER_IMAGE_CONCURRENCY=3` as the first concurrency adjustment.
- Preserve existing `target_index` single-image retry compatibility.

---

## File Structure

- Modify `backend/app/schemas/creative.py` to add validated `target_indices`.
- Modify `backend/app/api/v1/endpoints/creatives.py` to persist `target_indices` in generation task metadata.
- Modify `backend/app/services/creative_service.py` to resolve slot indices from `target_indices`, pass optional `task_id`, and log timings.
- Create `backend/app/services/image_generation_timing.py` for structured image timing logging.
- Modify `backend/app/services/generation_task_service.py` to pass `task.id` into image streaming and include timing data in task result when available.
- Modify `tests/test_creative_streaming.py` for multi-slot target generation and keyframe metadata.
- Add `tests/test_image_generation_timing.py` for timing log helper behavior.
- Modify `frontend/web-admin/src/lib/api.ts` to send `target_indices`.
- Create `frontend/web-admin/src/lib/creativeGenerationTasks.ts` for keyframe task grouping and multi-task cache normalization.
- Add `frontend/web-admin/tests/creativeGenerationTasks.test.ts` for the new frontend helpers.
- Modify `frontend/web-admin/src/App.tsx` to launch keyframe方案 tasks, resume multiple image tasks, and add whole方案 retry.
- Modify `frontend/web-admin/tests/reviewUiLayout.test.ts` or add source-level assertions in `frontend/web-admin/tests/creativeGenerationTasks.test.ts` only where behavior is helper-based.
- Modify `.env.example` and `.env.production.example` to document `MODEL_PROVIDER_IMAGE_CONCURRENCY=3`.

---

### Task 1: Backend Multi-Slot Target Support

**Files:**
- Modify: `backend/app/schemas/creative.py`
- Modify: `backend/app/api/v1/endpoints/creatives.py`
- Modify: `backend/app/services/creative_service.py`
- Test: `tests/test_creative_streaming.py`

**Interfaces:**
- Consumes: existing `CreativeGenerateRequest.target_index: int | None`.
- Produces: `CreativeGenerateRequest.target_indices: list[int]`.
- Produces: `_slot_indices(payload: CreativeGenerateRequest) -> list[int]` returns `target_indices`, then `target_index`, then `1..count`.
- Produces: `CreativeService.stream_creatives(session, payload, task_id: str | None = None)`.

- [ ] **Step 1: Add failing backend test for two-slot keyframe方案 generation**

Append this test to `tests/test_creative_streaming.py`:

```python
@pytest.mark.asyncio
async def test_stream_creatives_generates_selected_keyframe_pair(monkeypatch) -> None:
    _patch_image_provider(monkeypatch, FakeImageProvider())
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        campaign = Campaign(id="campaign-1", name="Campaign", metadata_json={})
        draft = CopyDraft(
            id="draft-1",
            campaign_id=campaign.id,
            topic_id="topic-1",
            body="Ad copy",
            headline="Headline",
            metadata_json={},
        )
        session.add_all([campaign, draft])
        await session.commit()

        fake_llm = FakeLLMProvider()
        service = CreativeService()
        service.llm = fake_llm  # type: ignore[assignment]

        events = [
            event
            async for event in service.stream_creatives(
                session,
                CreativeGenerateRequest(
                    draft_id=draft.id,
                    count=2,
                    size="9:16",
                    target_indices=[3, 4],
                    generation_mode="video_keyframe_variants",
                    variant_count=3,
                    frames_per_variant=2,
                    video_duration_seconds=12,
                ),
            )
        ]

        asset_events = [event for event in events if event["type"] == "asset"]
        assert events[0] == {"type": "start", "limit": 2, "indices": [3, 4]}
        assert fake_llm.calls[-1]["count"] == 2
        assert [event["index"] for event in asset_events] == [3, 4]

        metadata_by_index = {
            event["index"]: event["asset"]["metadata_json"] for event in asset_events
        }
        assert metadata_by_index[3]["keyframe_group"] == 2
        assert metadata_by_index[3]["keyframe_role"] == "first_frame"
        assert metadata_by_index[4]["keyframe_group"] == 2
        assert metadata_by_index[4]["keyframe_role"] == "last_frame"

    await engine.dispose()
```

- [ ] **Step 2: Run the focused failing test**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_creative_streaming.py::test_stream_creatives_generates_selected_keyframe_pair -q
```

Expected: fail with Pydantic validation or unexpected argument around `target_indices`.

- [ ] **Step 3: Add `target_indices` to the schema**

In `backend/app/schemas/creative.py`, update imports and class:

```python
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
```

Add these members inside `CreativeGenerateRequest`:

```python
    target_indices: list[int] = Field(default_factory=list, max_length=6)

    @field_validator("target_indices")
    @classmethod
    def validate_target_indices(cls, value: list[int]) -> list[int]:
        cleaned: list[int] = []
        for raw_index in value:
            index = int(raw_index)
            if index < 1 or index > 6:
                raise ValueError("target_indices entries must be between 1 and 6.")
            if index not in cleaned:
                cleaned.append(index)
        return cleaned

    @model_validator(mode="after")
    def validate_single_target_mode(self) -> "CreativeGenerateRequest":
        if self.target_index is not None and self.target_indices:
            raise ValueError("Use either target_index or target_indices, not both.")
        return self
```

- [ ] **Step 4: Wire `target_indices` through endpoints**

In `backend/app/api/v1/endpoints/creatives.py`, add this to both task metadata blocks:

```python
            "target_indices": payload.target_indices,
```

Keep existing `"target_index": payload.target_index`.

- [ ] **Step 5: Resolve multiple slot indices in creative service**

In `backend/app/services/creative_service.py`, update signatures:

```python
    async def build_creative_assets_without_commit(
        self,
        session: AsyncSession,
        draft_id: str,
        count: int,
        size: str,
        extra_metadata: dict,
        target_index: int | None = None,
        target_indices: list[int] | None = None,
        image_model_id: str | None = None,
        storyboard: list[dict] | None = None,
        storyboard_text: str | None = None,
        keyframe_plan: dict | None = None,
        task_id: str | None = None,
    ) -> list[CreativeAsset]:
```

Pass `target_indices=payload.target_indices` from `generate_creatives`.

Replace local slot resolution in `build_creative_assets_without_commit` with:

```python
        slot_indices = _target_slot_indices(
            count=count,
            target_index=target_index,
            target_indices=target_indices or [],
        )
```

Update `stream_creatives` signature:

```python
    async def stream_creatives(
        self,
        session: AsyncSession,
        payload: CreativeGenerateRequest,
        task_id: str | None = None,
    ) -> AsyncIterator[dict]:
```

Replace `_slot_indices` with:

```python
def _slot_indices(payload: CreativeGenerateRequest) -> list[int]:
    return _target_slot_indices(
        count=payload.count,
        target_index=payload.target_index,
        target_indices=payload.target_indices,
    )


def _target_slot_indices(
    *,
    count: int,
    target_index: int | None,
    target_indices: list[int],
) -> list[int]:
    if target_indices:
        return list(target_indices)
    if target_index is not None:
        return [target_index]
    return list(range(1, count + 1))
```

- [ ] **Step 6: Run focused backend tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_creative_streaming.py -q
```

Expected: all tests in `test_creative_streaming.py` pass.

- [ ] **Step 7: Commit Task 1**

Run:

```powershell
git add backend/app/schemas/creative.py backend/app/api/v1/endpoints/creatives.py backend/app/services/creative_service.py tests/test_creative_streaming.py
git commit -m "feat: support multi-slot image generation targets"
```

---

### Task 2: Backend Image Timing Logs

**Files:**
- Create: `backend/app/services/image_generation_timing.py`
- Modify: `backend/app/services/creative_service.py`
- Modify: `backend/app/services/generation_task_service.py`
- Test: `tests/test_image_generation_timing.py`
- Test: `tests/test_creative_streaming.py`

**Interfaces:**
- Produces: `record_image_generation_timing(**fields: object) -> None`.
- Produces: `image_generation_timer(**fields: object) -> Iterator[Callable[..., int]]`.
- Consumes: optional `task_id` passed from `GenerationTaskService._run_image_task()` into `CreativeService.stream_creatives()`.

- [ ] **Step 1: Add failing timing helper tests**

Create `tests/test_image_generation_timing.py`:

```python
import logging

from backend.app.services.image_generation_timing import image_generation_timer


def test_image_generation_timer_logs_success(caplog) -> None:
    caplog.set_level(logging.INFO, logger="backend.app.services.image_generation_timing")

    with image_generation_timer(
        task_id="task-1",
        draft_id="draft-1",
        campaign_id="campaign-1",
        image_index=3,
        keyframe_group=2,
        keyframe_role="first_frame",
        stage="provider_request",
        provider="gateway",
        model="gpt-image-2",
    ) as finish:
        duration_ms = finish(status="succeeded")

    assert duration_ms >= 0
    record = caplog.records[-1]
    assert record.message == "image_generation_timing"
    assert record.image_generation["task_id"] == "task-1"
    assert record.image_generation["stage"] == "provider_request"
    assert record.image_generation["status"] == "succeeded"
    assert record.image_generation["duration_ms"] >= 0


def test_image_generation_timer_logs_failure(caplog) -> None:
    caplog.set_level(logging.INFO, logger="backend.app.services.image_generation_timing")

    try:
        with image_generation_timer(stage="download", image_index=4):
            raise RuntimeError("download failed")
    except RuntimeError:
        pass

    record = caplog.records[-1]
    assert record.image_generation["stage"] == "download"
    assert record.image_generation["status"] == "failed"
    assert record.image_generation["error_message"] == "download failed"
```

- [ ] **Step 2: Run failing timing helper tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_image_generation_timing.py -q
```

Expected: fail because `backend.app.services.image_generation_timing` does not exist.

- [ ] **Step 3: Create timing helper**

Create `backend/app/services/image_generation_timing.py`:

```python
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from time import perf_counter
from typing import Any

logger = logging.getLogger(__name__)


def record_image_generation_timing(**fields: object) -> None:
    clean_fields = {key: value for key, value in fields.items() if value is not None}
    logger.info(
        "image_generation_timing",
        extra={"image_generation": clean_fields},
    )


@contextmanager
def image_generation_timer(**fields: object) -> Iterator[Callable[..., int]]:
    started = perf_counter()
    finished = False

    def finish(**finish_fields: object) -> int:
        nonlocal finished
        duration_ms = max(int((perf_counter() - started) * 1000), 0)
        record_image_generation_timing(
            **fields,
            **finish_fields,
            duration_ms=duration_ms,
        )
        finished = True
        return duration_ms

    try:
        yield finish
    except Exception as exc:
        if not finished:
            finish(
                status="failed",
                error_type=exc.__class__.__name__,
                error_message=str(exc),
            )
        raise
    else:
        if not finished:
            finish(status="succeeded")
```

- [ ] **Step 4: Instrument creative service**

In `backend/app/services/creative_service.py`, import:

```python
from backend.app.services.image_generation_timing import image_generation_timer
```

Add `task_id: str | None = None` parameters to `_generate_image_briefs_via_text_queue`, `_generate_asset_result`, `_generate_asset_from_brief`, and `_asset_from_generated_image`.

Wrap brief generation:

```python
        with image_generation_timer(
            task_id=task_id,
            draft_id=draft.id,
            campaign_id=draft.campaign_id,
            stage="image_brief",
            status="running",
        ) as finish:
            briefs = await self._generate_image_briefs_via_text_queue(
                draft=draft,
                count=len(slot_indices),
                size=payload.size,
                storyboard_context=storyboard_context,
                streamed=True,
                task_id=task_id,
            )
            finish(status="succeeded", count=len(briefs))
```

Wrap provider request inside `_generate_asset_from_brief`:

```python
        with image_generation_timer(
            task_id=task_id,
            draft_id=draft.id,
            campaign_id=draft.campaign_id,
            image_index=brief.image_index,
            stage="provider_request",
            provider=image_settings.image_provider,
            model=effective_image_model(image_settings),
        ):
            generated_images = await self.text_tasks.run_in_image_queue(
                lambda: image_provider.generate_images([brief])
            )
```

Wrap provider image transfer inside `_asset_from_generated_image`:

```python
            with image_generation_timer(
                task_id=task_id,
                draft_id=draft.id,
                campaign_id=draft.campaign_id,
                image_index=brief.image_index,
                stage="download_storage",
                provider=metadata.get("provider"),
                model=metadata.get("model"),
            ):
                image_url, storage_key = await self.image_storage.transfer_provider_image(
                    source_url=image.url,
                    campaign_id=draft.campaign_id,
                    image_id=image_id,
                )
```

Wrap DB commit in `stream_creatives`:

```python
                with image_generation_timer(
                    task_id=task_id,
                    draft_id=draft.id,
                    campaign_id=draft.campaign_id,
                    image_index=index,
                    stage="db_commit",
                ):
                    session.add(asset)
                    await session.commit()
                    await session.refresh(asset)
```

- [ ] **Step 5: Pass task id from generation task service**

In `backend/app/services/generation_task_service.py`, change:

```python
        async for event in service.stream_creatives(session, request):
```

to:

```python
        async for event in service.stream_creatives(session, request, task_id=task.id):
```

- [ ] **Step 6: Run timing and streaming tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_image_generation_timing.py tests/test_creative_streaming.py -q
```

Expected: all listed tests pass.

- [ ] **Step 7: Commit Task 2**

Run:

```powershell
git add backend/app/services/image_generation_timing.py backend/app/services/creative_service.py backend/app/services/generation_task_service.py tests/test_image_generation_timing.py tests/test_creative_streaming.py
git commit -m "feat: log image generation stage timings"
```

---

### Task 3: Frontend API and Helper Support for方案 Tasks

**Files:**
- Modify: `frontend/web-admin/src/lib/api.ts`
- Create: `frontend/web-admin/src/lib/creativeGenerationTasks.ts`
- Test: `frontend/web-admin/tests/creativeGenerationTasks.test.ts`

**Interfaces:**
- Produces: `keyframeTaskTargetGroups(variantCount: number, framesPerVariant?: number) -> number[][]`.
- Produces: `normalizeActiveImageGenerationTaskCache(value: unknown) -> ActiveImageGenerationTaskCache | null`.
- Produces: `activeImageGenerationTaskCachePayload(entry: ActiveImageGenerationTaskCache) -> string`.
- Produces: `targetIndicesMax(value: unknown) -> number`.
- Consumes: existing `keyframeGroupSlotIndices(group, framesPerVariant)`.

- [ ] **Step 1: Add failing frontend helper tests**

Create `frontend/web-admin/tests/creativeGenerationTasks.test.ts`:

```ts
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
```

- [ ] **Step 2: Run failing frontend helper tests**

Run:

```powershell
cd frontend\web-admin
npm test -- creativeGenerationTasks.test.ts
```

Expected: fail because `src/lib/creativeGenerationTasks.ts` does not exist.

- [ ] **Step 3: Create frontend helper module**

Create `frontend/web-admin/src/lib/creativeGenerationTasks.ts`:

```ts
import { keyframeGroupSlotIndices } from "./creativeKeyframes";

export type ActiveImageGenerationTaskCache = {
  taskIds: string[];
  draftId: string;
};

export function keyframeTaskTargetGroups(
  variantCount: number,
  framesPerVariant = 2,
): number[][] {
  return Array.from({ length: Math.max(variantCount, 0) }, (_, index) =>
    keyframeGroupSlotIndices(index + 1, framesPerVariant),
  );
}

export function normalizeActiveImageGenerationTaskCache(
  value: unknown,
): ActiveImageGenerationTaskCache | null {
  if (!isRecord(value)) return null;
  const draftId = typeof value.draftId === "string" ? value.draftId : "";
  const rawTaskIds = Array.isArray(value.taskIds)
    ? value.taskIds
    : typeof value.taskId === "string"
      ? [value.taskId]
      : [];
  const taskIds = rawTaskIds
    .filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    .filter((item, index, items) => items.indexOf(item) === index);
  return draftId && taskIds.length ? { taskIds, draftId } : null;
}

export function activeImageGenerationTaskCachePayload(
  entry: ActiveImageGenerationTaskCache,
): string {
  return JSON.stringify(entry);
}

export function targetIndicesMax(value: unknown): number {
  if (!Array.isArray(value)) return 0;
  return value.reduce((max, item) => {
    const numeric = typeof item === "number" ? item : Number(item);
    return Number.isFinite(numeric) ? Math.max(max, numeric) : max;
  }, 0);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
```

- [ ] **Step 4: Add `targetIndices` to API client**

In `frontend/web-admin/src/lib/api.ts`, add `targetIndices?: number[];` to both `generateCreativesTask` and `generateCreativesStream` options objects.

In the request body for each, add:

```ts
      ...(options.targetIndices?.length ? { target_indices: options.targetIndices } : {}),
```

Keep existing `target_index` for single-image retry.

- [ ] **Step 5: Run frontend tests**

Run:

```powershell
cd frontend\web-admin
npm test
```

Expected: all frontend unit tests pass.

- [ ] **Step 6: Commit Task 3**

Run:

```powershell
git add frontend/web-admin/src/lib/api.ts frontend/web-admin/src/lib/creativeGenerationTasks.ts frontend/web-admin/tests/creativeGenerationTasks.test.ts
git commit -m "feat: add keyframe image task helpers"
```

---

### Task 4: Frontend方案-Level Image Task Execution

**Files:**
- Modify: `frontend/web-admin/src/App.tsx`
- Modify: `frontend/web-admin/src/lib/generationTasks.ts`
- Test: `frontend/web-admin/tests/generationTasks.test.ts`

**Interfaces:**
- Consumes: `keyframeTaskTargetGroups()` from Task 3.
- Consumes: `normalizeActiveImageGenerationTaskCache()` from Task 3.
- Consumes: `targetIndicesMax()` from Task 3.
- Produces: image generation cache with `{ taskIds: string[], draftId: string }`.
- Produces: keyframe generation flow that launches one task per方案.

- [ ] **Step 1: Add failing frontend task helper test for target_indices slot count**

Append to `frontend/web-admin/tests/generationTasks.test.ts`:

```ts
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
```

- [ ] **Step 2: Update App imports and cache type**

In `frontend/web-admin/src/App.tsx`, remove the local `ActiveImageGenerationTaskCache` type and import:

```ts
import {
  activeImageGenerationTaskCachePayload,
  keyframeTaskTargetGroups,
  normalizeActiveImageGenerationTaskCache,
  targetIndicesMax,
  type ActiveImageGenerationTaskCache,
} from "./lib/creativeGenerationTasks";
```

- [ ] **Step 3: Replace active image cache helpers**

Replace `loadActiveImageGenerationTaskCache`, `saveActiveImageGenerationTaskCache`, and `clearActiveImageGenerationTaskCache` with:

```ts
function loadActiveImageGenerationTaskCache(): ActiveImageGenerationTaskCache | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(ACTIVE_IMAGE_GENERATION_TASK_CACHE_KEY);
    if (!raw) return null;
    return normalizeActiveImageGenerationTaskCache(JSON.parse(raw));
  } catch {
    return null;
  }
}

function saveActiveImageGenerationTaskCache(entry: ActiveImageGenerationTaskCache) {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(
      ACTIVE_IMAGE_GENERATION_TASK_CACHE_KEY,
      activeImageGenerationTaskCachePayload(entry),
    );
  } catch {
    // Local cache is an optimization only.
  }
}

function clearActiveImageGenerationTaskCache(taskId?: string) {
  if (typeof window === "undefined") return;
  const cached = loadActiveImageGenerationTaskCache();
  if (taskId && cached && cached.taskIds.length > 1) {
    const remainingTaskIds = cached.taskIds.filter((id) => id !== taskId);
    saveActiveImageGenerationTaskCache({ taskIds: remainingTaskIds, draftId: cached.draftId });
    return;
  }
  if (taskId && cached && !cached.taskIds.includes(taskId)) return;
  try {
    window.sessionStorage.removeItem(ACTIVE_IMAGE_GENERATION_TASK_CACHE_KEY);
  } catch {
    // Local cache is an optimization only.
  }
}
```

- [ ] **Step 4: Update resume effect for multiple task ids**

In the image task resume `useEffect`, replace the single `cached.taskId` path with a loop over `cached.taskIds`. Use this structure:

```ts
    const resumeTasks = async () => {
      try {
        for (const taskId of cached.taskIds) {
          if (cancelled) return;
          imageTaskResumeRef.current = taskId;
          const currentTask = await api.getGenerationTask(taskId);
          if (cancelled) return;
          if (currentTask.business_id !== cached.draftId) {
            clearActiveImageGenerationTaskCache(taskId);
            continue;
          }
          applyCreativeGenerationTask(currentTask);
          if (generationTaskIsFinal(currentTask)) {
            clearActiveImageGenerationTaskCache(currentTask.id);
            continue;
          }
          const completedTask = await waitForGenerationTask(
            currentTask.id,
            "图片",
            "image",
            applyCreativeGenerationTask,
          );
          if (cancelled) return;
          if (completedTask) {
            applyCreativeGenerationTask(completedTask);
            if (generationTaskIsFinal(completedTask)) {
              clearActiveImageGenerationTaskCache(completedTask.id);
            }
          }
        }
      } catch (caught) {
        if (!cancelled && !isTransientApiError(caught)) {
          clearActiveImageGenerationTaskCache();
        }
      }
    };

    void resumeTasks();
```

- [ ] **Step 5: Launch one task per keyframe方案**

Inside `handleGenerateCreativesTask`, after storyboard validation and before the old single `api.generateCreativesTask` call, add a keyframe branch:

```ts
      if (generationPlan.isKeyframeVariant) {
        const targetGroups = keyframeTaskTargetGroups(
          generationPlan.variantCount ?? DEFAULT_KEYFRAME_VARIANT_COUNT,
          generationPlan.framesPerVariant ?? KEYFRAME_FRAMES_PER_VARIANT,
        );
        const tasks = await Promise.all(
          targetGroups.map((targetIndices) =>
            api.generateCreativesTask(
              draft.id,
              targetIndices.length,
              generationPlan.size,
              undefined,
              {
                modelId: selectedImageModelId,
                storyboard: storyboardContext?.storyboard,
                storyboardText: storyboardContext?.storyboardText,
                generationMode: generationPlan.generationMode,
                variantCount: generationPlan.variantCount,
                framesPerVariant: generationPlan.framesPerVariant,
                videoDurationSeconds: generationPlan.videoDurationSeconds,
                targetIndices,
              },
            ),
          ),
        );
        saveActiveImageGenerationTaskCache({
          taskIds: tasks.map((task) => task.id),
          draftId: draft.id,
        });
        for (const task of tasks) {
          applyCreativeGenerationTask(task);
        }
        const completedTasks = await Promise.all(
          tasks.map((task) =>
            waitForGenerationTask(task.id, "图片", "image", applyCreativeGenerationTask),
          ),
        );
        for (const completedTask of completedTasks) {
          if (!completedTask) continue;
          applyCreativeGenerationTask(completedTask);
          if (generationTaskIsFinal(completedTask)) {
            clearActiveImageGenerationTaskCache(completedTask.id);
          }
        }
        const generatedAssets = completedTasks.flatMap((task) =>
          task ? applyCreativeGenerationTask(task) : [],
        );
        if (!generatedAssets.length) {
          setError("图片生成失败，请稍后重试。", "image");
          markLoadingCreativeSlotsFailed("图片生成失败，请重新生成。");
          return;
        }
        setNotice(
          generatedAssets.length === generationPlan.count
            ? `${generationPlan.variantCount ?? DEFAULT_KEYFRAME_VARIANT_COUNT} 组关键帧方案已生成`
            : `已生成 ${generatedAssets.length} 张关键帧，失败图片可单独重试`,
        );
        clearError("image");
        void saveWorkflowStage("image_review");
        return;
      }
```

Keep the existing non-keyframe code path after this branch.

- [ ] **Step 6: Update single retry cache writes**

In `handleRetryCreativeSlotTask`, replace:

```ts
      saveActiveImageGenerationTaskCache({ taskId: task.id, draftId: draft.id });
```

with:

```ts
      saveActiveImageGenerationTaskCache({ taskIds: [task.id], draftId: draft.id });
```

- [ ] **Step 7: Use target_indices when computing expected slot count**

In `expectedCreativeTaskSlotCount`, add:

```ts
  const targetIndicesMaximum = targetIndicesMax(
    task.payload.target_indices ?? task.metadata.target_indices,
  );
```

Then replace `targetIndex` in `Math.max` calls with `targetIndex ?? targetIndicesMaximum`.

- [ ] **Step 8: Run frontend tests and build**

Run:

```powershell
cd frontend\web-admin
npm test
npm run build
```

Expected: tests pass and Vite build succeeds.

- [ ] **Step 9: Commit Task 4**

Run:

```powershell
git add frontend/web-admin/src/App.tsx frontend/web-admin/src/lib/generationTasks.ts frontend/web-admin/tests/generationTasks.test.ts
git commit -m "feat: split keyframe image generation into scheme tasks"
```

---

### Task 5: Whole-Scheme Retry Without Technical UI States

**Files:**
- Modify: `frontend/web-admin/src/App.tsx`
- Test: `frontend/web-admin/tests/reviewUiLayout.test.ts`

**Interfaces:**
- Consumes: `keyframeGroupSlotIndices(group, framesPerVariant)`.
- Produces: `handleRetryKeyframeGroupTask(group: CreativeReviewKeyframeGroup<CreativeGenerationSlot>) -> Promise<void>`.
- Keeps: existing `handleRegenerateKeyframeGroup()` for feedback-based regeneration.

- [ ] **Step 1: Add source-level test for group retry action**

Append to `frontend/web-admin/tests/reviewUiLayout.test.ts`:

```ts
test("keyframe groups expose whole-scheme retry separately from feedback regeneration", () => {
  const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");

  assert.match(appSource, /handleRetryKeyframeGroupTask/);
  assert.match(appSource, /onRetryGroup/);
  assert.match(appSource, /重生方案/);
  assert.match(appSource, /按意见重生此方案/);
  assert.doesNotMatch(appSource, /brief 生成中/);
  assert.doesNotMatch(appSource, /图片下载保存中/);
});
```

- [ ] **Step 2: Add new handler**

In `frontend/web-admin/src/App.tsx`, add near `handleRegenerateKeyframeGroup`:

```ts
  async function handleRetryKeyframeGroupTask(group: CreativeReviewKeyframeGroup<CreativeGenerationSlot>) {
    const draft = approvedDraft ?? selectedDraft;
    if (!draft) {
      setError("请先生成并审核通过一条文案。", "image");
      return;
    }
    if (imageGenerationActionIsBusy(loading)) {
      setNotice("已有图片任务正在处理，请稍后再重生此方案。");
      return;
    }
    const generationPlan = imageGenerationPlanForMode(
      creativeGenerationMode,
      videoDurationSeconds,
      videoAspectRatio,
      keyframeVariantCount,
    );
    const storyboardContext = currentStoryboardContextForKeyframes();
    if (!storyboardContext) {
      setError("请先生成或粘贴视频脚本，再生成关键帧。", "image");
      return;
    }

    const targetIndices = keyframeGroupSlotIndices(
      group.group,
      generationPlan.framesPerVariant ?? KEYFRAME_FRAMES_PER_VARIANT,
    );
    const loadingKey = `creative-retry-group-${group.group}`;
    setLoading(loadingKey);
    clearError("image");
    setNotice(null);
    for (const index of targetIndices) {
      updateCreativeGenerationSlot(index, {
        status: "loading",
        asset: undefined,
        message: undefined,
      });
    }

    try {
      const task = await api.generateCreativesTask(
        draft.id,
        targetIndices.length,
        generationPlan.size,
        undefined,
        {
          modelId: selectedImageModelId,
          storyboard: storyboardContext.storyboard,
          storyboardText: storyboardContext.storyboardText,
          generationMode: generationPlan.generationMode,
          variantCount: generationPlan.variantCount,
          framesPerVariant: generationPlan.framesPerVariant,
          videoDurationSeconds: generationPlan.videoDurationSeconds,
          targetIndices,
        },
      );
      saveActiveImageGenerationTaskCache({ taskIds: [task.id], draftId: draft.id });
      applyCreativeGenerationTask(task);
      const completedTask = await waitForGenerationTask(
        task.id,
        `方案 ${group.group}`,
        "image",
        applyCreativeGenerationTask,
      );
      if (completedTask && generationTaskIsFinal(completedTask)) {
        clearActiveImageGenerationTaskCache(completedTask.id);
      }
      const generatedAssets = completedTask ? applyCreativeGenerationTask(completedTask) : [];
      if (!generatedAssets.length) {
        for (const index of targetIndices) {
          updateCreativeGenerationSlot(index, {
            status: "error",
            message: "方案重生未返回图片，请重试。",
          });
        }
        return;
      }
      setNotice(`方案 ${group.group} 已重生`);
      clearError("image");
      void saveWorkflowStage("image_review");
    } catch (caught) {
      const message = setCaughtError("image", caught, `方案 ${group.group} 重生失败`);
      for (const index of targetIndices) {
        updateCreativeGenerationSlot(index, {
          status: "error",
          message,
        });
      }
    } finally {
      setLoading((current) => (current === loadingKey ? null : current));
    }
  }
```

- [ ] **Step 3: Thread handler through `CreativesView` props**

Add prop:

```ts
onRetryGroup: (group: CreativeReviewKeyframeGroup<CreativeGenerationSlot>) => void;
```

Pass it from top-level:

```tsx
onRetryGroup={(group) => void handleRetryKeyframeGroupTask(group)}
```

- [ ] **Step 4: Add button to keyframe group UI**

Near the existing feedback regeneration button, add:

```tsx
                    <button
                      className="secondary-button"
                      onClick={() => onRetryGroup(group)}
                      disabled={Boolean(loading)}
                    >
                      {loading === `creative-retry-group-${group.group}` ? (
                        <Loader2 size={16} className="spin" />
                      ) : (
                        <RefreshCw size={16} />
                      )}
                      <span>重生方案</span>
                    </button>
```

Keep the existing `按意见重生此方案` button unchanged.

- [ ] **Step 5: Run frontend tests and build**

Run:

```powershell
cd frontend\web-admin
npm test
npm run build
```

Expected: all frontend tests pass and build succeeds.

- [ ] **Step 6: Commit Task 5**

Run:

```powershell
git add frontend/web-admin/src/App.tsx frontend/web-admin/tests/reviewUiLayout.test.ts
git commit -m "feat: add keyframe scheme retry"
```

---

### Task 6: Concurrency Defaults, Full Verification, and Local Runtime Check

**Files:**
- Modify: `.env.example`
- Modify: `.env.production.example`
- Verify: no changes to `.env`, `.env.production`, or `AGENTS.md`

**Interfaces:**
- Produces: documented example setting `MODEL_PROVIDER_IMAGE_CONCURRENCY=3`.

- [ ] **Step 1: Update example env files**

In `.env.example` and `.env.production.example`, set or add:

```env
MODEL_PROVIDER_IMAGE_CONCURRENCY=3
```

Do not edit `.env` or `.env.production` in this task.

- [ ] **Step 2: Run backend focused tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_creative_streaming.py tests/test_generation_tasks.py tests/test_image_generation_timing.py -q
```

Expected: all listed tests pass.

- [ ] **Step 3: Run backend lint and full tests**

Run:

```powershell
.venv\Scripts\python.exe -m ruff check backend tests
.venv\Scripts\python.exe -m pytest
```

Expected: ruff passes and full pytest passes.

- [ ] **Step 4: Run frontend tests and build**

Run:

```powershell
cd frontend\web-admin
npm test
npm run build
```

Expected: tests pass and build succeeds.

- [ ] **Step 5: Verify git safety before commit**

Run:

```powershell
git status --short
```

Expected: changed files include implementation files and example env files only. Output must not include `AGENTS.md`, `.env`, or `.env.production`.

- [ ] **Step 6: Commit Task 6**

Run:

```powershell
git add .env.example .env.production.example
git commit -m "chore: document image provider concurrency default"
```

- [ ] **Step 7: Local Docker runtime validation after all commits**

Run:

```powershell
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build backend worker_image web
docker compose -f docker-compose.prod.yml --env-file .env.production ps
curl.exe -i http://127.0.0.1/api/v1/health/live
```

Expected:

```text
HTTP/1.1 200 OK
{"status":"ok"}
```

Then run one real keyframe generation from the UI and check:

```powershell
docker compose -f docker-compose.prod.yml --env-file .env.production logs --tail=200 worker_image
```

Expected: logs contain `image_generation_timing` entries for `image_brief`, `provider_request`, `download_storage`, and `db_commit`.

---

## Final Review Checklist

- [ ] `git status --short` does not show `AGENTS.md`, `.env`, or `.env.production`.
- [ ] Keyframe generation creates 3 image tasks for 3 schemes.
- [ ] Each方案 shows two slots and can display one completed image while the other is loading or failed.
- [ ] Single image retry still sends `target_index`.
- [ ] Whole方案 retry sends `target_indices`.
- [ ] Backend logs timing stages without exposing technical statuses in the UI.
- [ ] `MODEL_PROVIDER_IMAGE_CONCURRENCY=3` is documented in example env files only.
- [ ] Backend tests, frontend tests, frontend build, and local Docker health check pass.
