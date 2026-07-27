# External Storyboard V2 Reference Video Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional reference-video motion analysis to storyboard V2 while preserving the existing no-reference contract.

**Architecture:** A focused media service resolves URL, uploaded, or existing video sources, validates duration and format, and extracts downscaled JPEG frames at 2-second intervals. The existing first/last visual-analysis call becomes a joint analysis call with optional chronological reference frames; its validated private output then constrains the existing storyboard-generation call.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy async, FFprobe, FFmpeg, OpenAI/Gateway Responses-compatible multimodal content, Pytest.

## Global Constraints

- Reference videos are optional and limited to 30 seconds.
- Extract frames at 0, 2, 4, ... seconds and include the exact final frame without duplicate timestamps.
- Send target first, target last, then all reference frames chronologically in one visual-model request.
- Do not analyze reference audio, speech, lyrics, narration, subtitles, or transcripts.
- Target frames are factual truth; subject presence is required; camera, transitions, and effects are preferred.
- Any supplied-reference failure stops storyboard generation; no silent fallback is allowed.
- The no-reference path must retain current behavior.
- Reference analysis is private metadata and must not appear in polling results.
- Uploading a reference creates no Campaign, CreativeAsset, or VideoAsset rows.

---

### Task 1: Request And Analysis Contracts

**Files:**
- Modify: `backend/app/schemas/external_ai_generation.py`
- Modify: `backend/app/schemas/ai.py`
- Test: `tests/test_external_ai_generation.py`

**Interfaces:**
- Produces: discriminated reference source models, `ReferenceVideoFrame`, and strict reference-analysis schemas attached optionally to `FrameAnalysis`.

- [ ] Write failing schema tests for omitted reference video, each valid source, forbidden extra identifiers, and missing matching identifiers.
- [ ] Run `pytest tests/test_external_ai_generation.py -k "reference_video and schema" -v` and confirm failures are caused by missing contracts.
- [ ] Implement discriminated request models and strict private analysis models.
- [ ] Re-run the focused schema tests and confirm they pass.

### Task 2: Reference Media Resolution And Sampling

**Files:**
- Create: `backend/app/services/storyboard_reference_video_service.py`
- Modify: `backend/app/services/video_storage_service.py`
- Modify: `backend/app/core/config.py`
- Test: `tests/test_storyboard_reference_video_service.py`

**Interfaces:**
- Consumes: the reference source schema, `AsyncSession`, and `VideoAsset`.
- Produces: `PreparedReferenceVideo(duration_seconds, sample_interval_seconds, frames, working_dir)` where frames contain timestamps and JPEG data URLs.

- [ ] Write failing tests for timestamps, exact final inclusion, chronological order, local upload resolution, existing `VideoAsset` resolution, public URL download, invalid format, extraction failure, and duration above 30 seconds.
- [ ] Run `pytest tests/test_storyboard_reference_video_service.py -v` and confirm expected failures.
- [ ] Add feature settings for 30-second maximum, 2-second interval, frame width/quality, and FFprobe/FFmpeg timeouts.
- [ ] Implement safe source resolution, FFprobe validation, sequential FFmpeg extraction, downscaling, and data-URL conversion.
- [ ] Re-run the media-service tests and confirm they pass.

### Task 3: Joint Multimodal Provider Contract

**Files:**
- Modify: `backend/app/integrations/llm/base.py`
- Modify: `backend/app/integrations/llm/openai_provider.py`
- Modify: `backend/app/integrations/llm/mock_provider.py`
- Test: `tests/test_frame_anchored_storyboard_provider.py`

**Interfaces:**
- Consumes: optional chronological reference frames and source metadata in `analyze_video_frame_pair`.
- Produces: `FrameAnalysis` with optional validated `reference_video_analysis`.

- [ ] Write a failing provider test proving content order: target first, target last, reference 0s, reference 2s, then final.
- [ ] Add failing prompt assertions for target/reference boundaries, content-copy prohibition, audio exclusions, and required/preferred constraint strengths.
- [ ] Run the focused provider tests and confirm expected failures.
- [ ] Extend provider signatures, content construction, prompts, normalization, and mock behavior.
- [ ] Re-run provider tests and confirm they pass.

### Task 4: Worker Orchestration And Failure Semantics

**Files:**
- Modify: `backend/app/services/external_ai_generation_service.py`
- Test: `tests/test_external_ai_generation.py`

**Interfaces:**
- Consumes: `StoryboardReferenceVideoService.prepare(...)`.
- Produces: the existing public storyboard result plus private reference analysis inside stored `frame_analysis` metadata.

- [ ] Write failing tests proving prepared frames reach call 1, call 2 receives adapted constraints, reference analysis failure prevents call 2, and no-reference behavior remains unchanged.
- [ ] Run focused service tests and confirm expected failures.
- [ ] Prepare optional reference media before call 1, pass frames into joint analysis, and keep analysis stored privately.
- [ ] Re-run focused service tests and confirm they pass.

### Task 5: Reference Upload Endpoint

**Files:**
- Modify: `backend/app/api/v1/endpoints/external_ai_generation.py`
- Modify: `backend/app/services/video_storage_service.py`
- Test: `tests/test_external_ai_generation.py`

**Interfaces:**
- Produces: `POST /storyboard-v2/reference-video` returning `{upload_asset_id}` for later storyboard requests.

- [ ] Write failing multipart endpoint tests for accepted MP4/MOV/WebM, unsupported type, empty file, and configured size limit.
- [ ] Run focused endpoint tests and confirm expected failures.
- [ ] Implement bounded upload reading and private local storage with an opaque identifier.
- [ ] Verify upload plus storyboard creation creates no business asset rows and polling remains private.

### Task 6: Verification

- [ ] Run `pytest tests/test_storyboard_reference_video_service.py tests/test_frame_anchored_storyboard_provider.py tests/test_external_ai_generation.py -v`.
- [ ] Run `ruff check backend tests`.
- [ ] Run the broader relevant backend test suite.
- [ ] Rebuild the local production-like Docker services when feasible.
- [ ] Send one controlled V2 request without a reference and one with a short reference; verify call count, polling privacy, task metadata, and failure behavior.
