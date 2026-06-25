# Keyframe Variant Groups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate three selectable first-frame/last-frame image groups for 12-second video workflows.

**Architecture:** Keep the database unchanged and store grouping on each `CreativeAsset.metadata_json`. The frontend requests six images using a new optional creative generation mode, then groups returned assets by metadata and lets operators choose one two-image group for video.

**Tech Stack:** FastAPI/Pydantic backend, async creative streaming service, React/TypeScript frontend, pytest and Vite build verification.

---

### Task 1: Backend Request And Metadata

**Files:**
- Modify: `backend/app/schemas/creative.py`
- Modify: `backend/app/services/creative_service.py`
- Test: `tests/test_creative_streaming.py`

- [ ] **Step 1: Write the failing test**

Add a test that streams six assets with `generation_mode="video_keyframe_variants"` and asserts asset metadata contains `keyframe_group`, `keyframe_role`, `keyframe_group_size`, and `video_duration_seconds`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests\test_creative_streaming.py::test_stream_creatives_marks_video_keyframe_variant_groups -q`
Expected: failure because the request fields and metadata do not exist yet.

- [ ] **Step 3: Implement request fields and metadata helper**

Add optional `generation_mode`, `variant_count`, `frames_per_variant`, and `video_duration_seconds` fields. In creative service, when mode is `video_keyframe_variants`, add group metadata by image index: odd positions are `first_frame`, even positions are `last_frame`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests\test_creative_streaming.py::test_stream_creatives_marks_video_keyframe_variant_groups -q`
Expected: pass.

### Task 2: Prompt Context

**Files:**
- Modify: `backend/app/services/creative_service.py`
- Modify: `backend/app/integrations/llm/openai_provider.py`
- Test: `tests/test_creative_streaming.py`

- [ ] **Step 1: Extend storyboard context assertions**

Assert the fake LLM receives `keyframe_plan` with three variants, two frames per variant, and 12 seconds.

- [ ] **Step 2: Implement prompt context**

Attach `keyframe_plan` to `storyboard_context` and update the LLM prompt to tell the model each pair is one first/last-frame option.

### Task 3: Frontend Grouped Selection

**Files:**
- Modify: `frontend/web-admin/src/lib/api.ts`
- Modify: `frontend/web-admin/src/App.tsx`
- Modify: `frontend/web-admin/src/styles.css`
- Modify: `frontend/web-admin/src/types/domain.ts`

- [ ] **Step 1: Send keyframe generation options**

When `videoDurationSeconds === 12`, call `generateCreativesStream` with count `6` and `generationMode="video_keyframe_variants"`.

- [ ] **Step 2: Group visible assets**

Group `CreativeAsset` records by `metadata_json.keyframe_group`. Show each pair as “方案 1/2/3” and add a button that selects both image IDs for video.

- [ ] **Step 3: Preserve fallback behavior**

If assets do not have keyframe metadata, keep the existing single-card grid behavior.

### Task 4: Verification

**Files:**
- No production changes.

- [ ] **Step 1: Run backend checks**

Run: `.venv\Scripts\python.exe -m ruff check backend tests`
Expected: `All checks passed!`

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all tests pass.

- [ ] **Step 2: Run frontend build**

Run from `frontend/web-admin`: `npm run build`
Expected: TypeScript and Vite build pass.

- [ ] **Step 3: Rebuild local Docker**

Run: `docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build`
Expected: backend is healthy and web starts.
