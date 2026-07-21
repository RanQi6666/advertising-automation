# External Video Prompt Passthrough Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing external video-generation API send `storyboard_text.strip()` unchanged to the configured video provider and skip final-text overlay processing, while preserving all current internal-video augmentation behavior and the complete external API contract.

**Architecture:** Keep the public route, schema, authentication, queue, retry, polling, and storage flow unchanged. Use the existing `VideoAsset.metadata_json.source == "external_video_generation"` marker inside `VideoService` to select a passthrough prompt branch and to bypass final overlay application; stop extracting overlay directives when new external video records are created.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy async ORM, Celery generation tasks, pytest/pytest-asyncio, Ruff, existing `VideoService` and Volcengine/placeholder provider adapters.

## Global Constraints

- Execute this plan in an isolated worktree created from the current approved-design commit; recommended branch: `codex/external-video-prompt-passthrough`.
- Do not modify the external Pixel project under `/www/wwwroot/pixel_project`.
- Do not change `POST /api/v1/integrations/video-generation/videos` or `GET /api/v1/integrations/video-generation/jobs/{job_id}`.
- Do not add a public `prompt_mode`, a new endpoint, a new Token, a new environment variable, or an IP allowlist.
- Continue using the existing `AI_ADS_ACCESS_TOKEN` dependency and current request/response envelopes.
- Define passthrough exactly as `provider_prompt == storyboard_text.strip()`; do not perform semantic rewriting, safety substitution, prompt-block injection, tag removal, or format reconstruction.
- Use only `metadata_json.source == "external_video_generation"` as the business gate. Do not infer passthrough from an empty `creative_strategy`.
- Keep internal video safety rewriting, Creative Safety blocks, keyframe/3A timing rules, style packs, creative strategy, and internal overlay processing unchanged.
- Keep all technical validation, idempotency, queueing, provider polling, retries, file transfer, and provider-side safety behavior unchanged.
- Do not add or alter database tables or columns; no Alembic migration is permitted.
- Do not stage or commit `.env`, `.env.production`, `AGENTS.md`, credentials, runtime media, or any unrelated pre-existing working-tree files.
- Do not push, deploy, or restart the test server without separate user approval after local implementation review.

## File Map

- Modify: `backend/app/services/video_service.py`
  - Owns provider request construction and final overlay application.
  - Add one source-classification helper used by both behaviors.
- Modify: `backend/app/services/external_video_generation_service.py`
  - Owns external video persistence and task creation.
  - Stop extracting and storing overlay directives for new external tasks.
- Modify: `tests/test_external_video_generation.py`
  - Add end-to-end provider-request capture for prompt passthrough.
  - Replace the old external overlay-persistence expectation.
  - Add old-record compatibility coverage proving external overlays are skipped.
  - Retain internal overlay regression coverage.
- Validate, but do not modify unless a regression requires it: `tests/test_video_storyboard.py`
  - Existing tests prove normal/internal direct prompts still receive timing and safety augmentation.
- Validate, but do not modify unless a regression requires it: `tests/test_video_provider.py`
  - Existing tests prove the provider adapter preserves `request.prompt` in `content[0].text` and keeps technical payload fields unchanged.

---

### Task 1: Route external video prompts through a source-gated passthrough branch

**Files:**
- Modify: `backend/app/services/video_service.py:38-45,673-737`
- Test: `tests/test_external_video_generation.py:469-540`
- Regression test: `tests/test_video_storyboard.py:635-664`

**Interfaces:**
- Consumes: `VideoAsset.metadata_json: dict | None`, `VideoAsset.prompt: str | None`, and `EXTERNAL_VIDEO_GENERATION_SOURCE` from `backend.app.services.external_sources`.
- Produces: `_is_external_video_generation(video: VideoAsset) -> bool` and an external branch in `VideoService._build_provider_request()` whose `VideoGenerationRequest.prompt` is exactly `(video.prompt or "").strip()`.
- Preserves: the existing internal `_prompt_with_creative_strategy()` and `_storyboard_to_prompt()` paths.

- [ ] **Step 1: Add a failing end-to-end test that captures the provider request**

In `tests/test_external_video_generation.py`, add this test after `test_external_video_generation_start_task_then_polling_returns_url`:

```python
@pytest.mark.asyncio
async def test_external_video_start_passes_storyboard_text_without_backend_augmentation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERATION_TASK_EXECUTION_BACKEND", "celery")
    get_settings.cache_clear()
    enqueued: list[tuple[str, str, int]] = []
    captured_prompts: list[str] = []
    captured_metadata: list[dict] = []

    class CapturingVideoProvider:
        async def start_generation(self, request):
            captured_prompts.append(request.prompt)
            captured_metadata.append(request.metadata)
            return VideoGenerationStart(
                provider_job_id="provider-passthrough-job",
                provider_status="queued",
                raw_response={"id": "provider-passthrough-job", "status": "queued"},
                request_payload={"prompt": request.prompt},
            )

    def capture_enqueue(
        task_id: str,
        queue_name: str,
        priority: int,
        countdown_seconds: int = 0,
    ) -> None:
        assert countdown_seconds == 0
        enqueued.append((task_id, queue_name, priority))

    monkeypatch.setattr(
        video_service_module,
        "get_video_provider",
        lambda settings: CapturingVideoProvider(),
    )
    monkeypatch.setattr(
        "backend.app.services.generation_task_dispatcher._enqueue_celery_generation_task",
        capture_enqueue,
    )
    client, engine, app, session_factory = await _client_with_db(
        tmp_path,
        monkeypatch,
        token="video-token",
    )
    monkeypatch.setattr(task_module, "AsyncSessionLocal", session_factory)
    storyboard_text = """
777 casino lobby opens with a cash balance and withdraw button.
Continue the exact first-frame action, then resolve into the supplied last frame.
"""

    try:
        response = client.post(
            "/api/v1/integrations/video-generation/videos",
            headers=_authorized_headers(),
            json=_video_payload(
                external_request_id="external-video-passthrough",
                storyboard_text=storyboard_text,
            ),
        )
        job_id = response.json()["data"]["job_id"]
        start_task_id, start_queue_name, _priority = enqueued[0]
        assert start_queue_name == VIDEO_QUEUE_NAME

        async with session_factory() as session:
            video = await session.get(VideoAsset, job_id)
            assert video is not None
            video.metadata_json = {
                **(video.metadata_json or {}),
                "creative_strategy": {
                    "style_pack_id": "must-not-be-injected",
                    "style_pack": {"visual_direction": "must-not-be-injected"},
                    "market_game_style_pack": {
                        "video_guidance": "must-not-be-injected"
                    },
                },
            }
            await session.commit()

        await GenerationTaskService().process_task(start_task_id)
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert response.status_code == 202
    assert captured_prompts == [storyboard_text.strip()]
    assert len(captured_metadata) == 1
    assert "creative_strategy" not in captured_metadata[0]
    await engine.dispose()
```

This test deliberately includes terms currently changed by `sanitize_creative_safety_text()` and injects a stored `creative_strategy` to prove external-source classification overrides all backend prompt augmentation.

- [ ] **Step 2: Run the new test and verify the current implementation fails**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_external_video_generation.py::test_external_video_start_passes_storyboard_text_without_backend_augmentation -v
```

Expected: `FAIL` because `captured_prompts[0]` contains sanitized text plus Creative Safety, keyframe/3A timing, and creative-strategy blocks instead of exactly `storyboard_text.strip()`.

- [ ] **Step 3: Add the external-source helper to `video_service.py`**

Add the constant import near the existing service imports:

```python
from backend.app.services.external_sources import EXTERNAL_VIDEO_GENERATION_SOURCE
```

Add this module-level helper close to the other video metadata helpers:

```python
def _is_external_video_generation(video: VideoAsset) -> bool:
    metadata = video.metadata_json or {}
    return metadata.get("source") == EXTERNAL_VIDEO_GENERATION_SOURCE
```

Do not add a `prompt_mode` requirement and do not use the task type as a second source of truth.

- [ ] **Step 4: Branch provider prompt construction without changing internal behavior**

Replace the prompt-building portion of `VideoService._build_provider_request()` with:

```python
        is_external_video = _is_external_video_generation(video)
        creative_strategy = (
            None if is_external_video else _strategy_from_metadata(video.metadata_json)
        )
        if is_external_video:
            prompt = (video.prompt or "").strip()
        else:
            prompt = (
                _prompt_with_creative_strategy(video.prompt, creative_strategy)
                or _storyboard_to_prompt(
                    video.storyboard or [],
                    creative_strategy=creative_strategy,
                )
            ).strip()
        if not prompt:
            raise AppError("Video prompt is required.")
```

Keep the existing `VideoGenerationRequest` construction unchanged except that external tasks naturally omit `creative_strategy` because the local value is `None`:

```python
        return VideoGenerationRequest(
            prompt=prompt,
            source_images=source_images,
            duration_seconds=duration_seconds,
            aspect_ratio=video.aspect_ratio,
            metadata={
                "campaign_id": video.campaign_id,
                "draft_id": video.draft_id,
                "video_id": video.id,
                **({"creative_strategy": creative_strategy} if creative_strategy else {}),
            },
        )
```

Do not edit `_prompt_with_creative_strategy()`, `sanitize_creative_safety_text()`, `creative_safety_prompt_block()`, `_keyframe_brand_aaa_video_rules()`, or `_creative_strategy_prompt_block()`.

- [ ] **Step 5: Run the passthrough and internal-regression tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest `
  tests/test_external_video_generation.py::test_external_video_start_passes_storyboard_text_without_backend_augmentation `
  tests/test_video_storyboard.py::test_direct_video_prompt_includes_keyframe_brand_aaa_timing_rules `
  tests/test_video_storyboard.py::test_video_prompt_with_existing_safety_block_preserves_prompt_text `
  -v
```

Expected: all three tests `PASS`. The first proves external passthrough; the latter two prove the shared internal augmentation function was not weakened.

- [ ] **Step 6: Run the complete external video test file**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_external_video_generation.py -v
```

Expected at this stage: the new prompt test passes. Existing overlay tests may still pass with old behavior because overlay removal is intentionally handled in Task 2.

- [ ] **Step 7: Commit Task 1 only**

Verify the staged set before committing:

```powershell
git status --short
git add -- backend/app/services/video_service.py tests/test_external_video_generation.py
git diff --cached --name-status
git diff --cached --check
git commit -m "feat: passthrough external video prompts"
```

Expected staged files:

```text
M backend/app/services/video_service.py
M tests/test_external_video_generation.py
```

---

### Task 2: Disable external overlay extraction and skip legacy external overlay metadata

**Files:**
- Modify: `backend/app/services/external_video_generation_service.py:23-29,52-78,143-160`
- Modify: `backend/app/services/video_service.py:540-563`
- Test: `tests/test_external_video_generation.py:353-466`

**Interfaces:**
- Consumes: `_is_external_video_generation(video: VideoAsset) -> bool` from Task 1.
- Produces: new external records without `final_text_overlay_locks`, and `_apply_final_text_overlay_locks()` behavior that immediately returns for every external video source, including records created before deployment.
- Preserves: overlay extraction helpers and overlay application for non-external/internal workflows.

- [ ] **Step 1: Replace the old external overlay-persistence test with the approved expectation**

Replace `test_external_video_generation_persists_final_text_overlay_locks_from_storyboard` with:

```python
@pytest.mark.asyncio
async def test_external_video_generation_does_not_parse_final_text_overlay_locks(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERATION_TASK_EXECUTION_BACKEND", "celery")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "backend.app.services.generation_task_dispatcher._enqueue_celery_generation_task",
        lambda *args, **kwargs: None,
    )
    client, engine, app, session_factory = await _client_with_db(
        tmp_path,
        monkeypatch,
        token="video-token",
    )
    storyboard_text = "\n".join(
        (
            "The final reward is held over the last frame.",
            "[FINAL_TEXT_OVERLAY_LOCKS]",
            '[{"kind":"text","text":"x200,000","show_from_second":9.35,"placement":"lower_center"}]',
            "[/FINAL_TEXT_OVERLAY_LOCKS]",
        )
    )
    try:
        response = client.post(
            "/api/v1/integrations/video-generation/videos",
            headers=_authorized_headers(),
            json=_video_payload(
                external_request_id="video-final-text-lock",
                duration_seconds=10,
                storyboard_text=storyboard_text,
            ),
        )
        job_id = response.json()["data"]["job_id"]
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert response.status_code == 202
    async with session_factory() as session:
        video = await session.get(VideoAsset, job_id)

    assert video is not None
    assert video.prompt == storyboard_text
    assert "final_text_overlay_locks" not in video.metadata_json
    await engine.dispose()
```

This test keeps the lock tags inside `video.prompt`; passthrough means the backend does not parse or remove them.

- [ ] **Step 2: Add a failing compatibility test for old external records that already contain overlays**

Keep the existing `test_video_transfer_applies_persisted_final_text_overlay_locks` as the internal regression test. Rename it to `test_internal_video_transfer_applies_persisted_final_text_overlay_locks` for clarity without changing its assertions.

Then add:

```python
@pytest.mark.asyncio
async def test_external_video_transfer_skips_persisted_final_text_overlay_locks(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, session_factory = await _session_factory(
        tmp_path,
        "external-video-final-overlay-transfer.db",
    )
    storage_path = tmp_path / "storage" / "videos" / "external-overlay-test" / "provider.mp4"
    storage_path.parent.mkdir(parents=True)
    storage_path.write_bytes(b"saved-provider-video")
    calls: list[tuple[object, list[dict[str, object]]]] = []

    async def fake_apply_final_text_overlay_locks(video_path, *, overlays):
        calls.append((video_path, overlays))
        return overlays

    monkeypatch.setattr(
        video_service_module,
        "apply_final_text_overlay_locks",
        fake_apply_final_text_overlay_locks,
        raising=False,
    )
    async with session_factory() as session:
        campaign = Campaign(name="External overlay transfer campaign")
        session.add(campaign)
        await session.flush()
        video = VideoAsset(
            campaign_id=campaign.id,
            storage_key="local://videos/external-overlay-test/provider.mp4",
            status=VideoStatus.GENERATED.value,
            metadata_json={
                "source": EXTERNAL_SOURCE,
                "final_text_overlay_locks": [
                    {
                        "kind": "text",
                        "text": "x200,000",
                        "show_from_second": 9.35,
                        "placement": "lower_center",
                    }
                ],
            },
        )
        session.add(video)
        await session.commit()
        await session.refresh(video)

        transferred = await VideoService().transfer_completed_video(session, video.id)

    assert calls == []
    assert "final_text_overlay_status" not in transferred.metadata_json
    assert "final_text_overlay_applied_locks" not in transferred.metadata_json
    await engine.dispose()
```

- [ ] **Step 3: Run both new expectations and verify they fail before implementation**

Run:

```powershell
.venv\Scripts\python.exe -m pytest `
  tests/test_external_video_generation.py::test_external_video_generation_does_not_parse_final_text_overlay_locks `
  tests/test_external_video_generation.py::test_external_video_transfer_skips_persisted_final_text_overlay_locks `
  -v
```

Expected: both tests `FAIL`. The creation test finds `final_text_overlay_locks` in metadata, and the transfer test records one overlay application call.

- [ ] **Step 4: Stop extracting overlays when external video records are created**

In `backend/app/services/external_video_generation_service.py`, remove this import:

```python
from backend.app.services.video_final_overlay_service import (
    extract_final_text_overlay_locks,
)
```

Remove this block from `create_video()`:

```python
        final_text_overlay_locks = extract_final_text_overlay_locks(
            storyboard_text,
            duration_seconds=payload.duration_seconds,
        )
```

Remove only this metadata entry from the external `VideoAsset` constructor:

```python
                    "final_text_overlay_locks": final_text_overlay_locks,
```

Keep `source`, `external_request_id`, `storyboard_text`, `duration_seconds`, `aspect_ratio`, and `implementation_status` unchanged.

- [ ] **Step 5: Add the defensive external-source guard to final overlay application**

At the start of `VideoService._apply_final_text_overlay_locks()`, add:

```python
    async def _apply_final_text_overlay_locks(self, video: VideoAsset) -> None:
        if _is_external_video_generation(video):
            return
        metadata = video.metadata_json or {}
```

Do not change the remaining internal validation or `apply_final_text_overlay_locks()` call.

- [ ] **Step 6: Run the overlay tests and the internal regression test**

Run:

```powershell
.venv\Scripts\python.exe -m pytest `
  tests/test_external_video_generation.py::test_external_video_generation_does_not_parse_final_text_overlay_locks `
  tests/test_external_video_generation.py::test_external_video_transfer_skips_persisted_final_text_overlay_locks `
  tests/test_external_video_generation.py::test_internal_video_transfer_applies_persisted_final_text_overlay_locks `
  tests/test_video_final_overlay_service.py `
  -v
```

Expected: all tests `PASS`. The two external tests skip parsing/application, while the internal transfer test and overlay utility tests remain unchanged.

- [ ] **Step 7: Run all external-video tests after both behavior changes**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_external_video_generation.py -v
```

Expected: all tests `PASS`, including authentication, exactly-two-images validation, idempotency, retry/requeue, polling, and transfer behavior.

- [ ] **Step 8: Commit Task 2 only**

```powershell
git status --short
git add -- `
  backend/app/services/external_video_generation_service.py `
  backend/app/services/video_service.py `
  tests/test_external_video_generation.py
git diff --cached --name-status
git diff --cached --check
git commit -m "feat: disable external video overlay processing"
```

Expected staged files:

```text
M backend/app/services/external_video_generation_service.py
M backend/app/services/video_service.py
M tests/test_external_video_generation.py
```

---

### Task 3: Verify provider payload compatibility and full backend regression safety

**Files:**
- Verify: `backend/app/integrations/video/volcengine_provider.py`
- Verify: `tests/test_video_provider.py`
- Verify: `tests/test_video_storyboard.py`
- Verify: `tests/test_external_video_generation.py`
- No new production files are expected.

**Interfaces:**
- Consumes: the source-gated provider prompt and overlay behavior delivered by Tasks 1 and 2.
- Produces: a locally verified feature branch ready for user review, without push or deployment.

- [ ] **Step 1: Run focused provider-payload tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_video_provider.py -v
```

Expected: all tests `PASS`. Confirm the adapter still maps `VideoGenerationRequest.prompt` directly to `content[0].text` and retains model, resolution, ratio, duration, audio, watermark, last-frame, expiry, priority, and optional `safety_identifier` behavior.

- [ ] **Step 2: Run all storyboard and prompt-augmentation regressions**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_video_storyboard.py -v
```

Expected: all tests `PASS`. In particular, internal prompts still contain the existing keyframe/3A timing and Creative Safety behavior.

- [ ] **Step 3: Run all final-overlay utility regressions**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_video_final_overlay_service.py -v
```

Expected: all tests `PASS`. The generic overlay parser and renderer remain available for internal workflows even though the external service no longer invokes them.

- [ ] **Step 4: Run Ruff on all backend and test code**

Run:

```powershell
.venv\Scripts\python.exe -m ruff check backend tests
```

Expected: exit code `0` and no lint errors.

- [ ] **Step 5: Run the complete Python test suite**

Run:

```powershell
.venv\Scripts\python.exe -m pytest
```

Expected: exit code `0`; all tests pass. Do not accept unrelated new failures without diagnosing them. If a pre-existing failure is reproducible on the base commit, document it separately instead of weakening this feature's assertions.

- [ ] **Step 6: Review the complete implementation diff against the approved design**

Run:

```powershell
git diff HEAD~2..HEAD -- `
  backend/app/services/video_service.py `
  backend/app/services/external_video_generation_service.py `
  tests/test_external_video_generation.py
git diff HEAD~2..HEAD --check
git status --short
```

Confirm all of the following manually:

```text
external provider prompt == storyboard_text.strip()
external creative_strategy is ignored
external overlay directives are not parsed
old external overlay metadata is not applied
internal prompt augmentation is unchanged
internal overlay application is unchanged
public API/schema/authentication are unchanged
no database migration exists
no external Pixel-system file changed
no .env, .env.production, AGENTS.md, credentials, or runtime media is staged
```

- [ ] **Step 7: Prepare the implementation handoff without pushing or deploying**

Record:

```powershell
git branch --show-current
git log -3 --oneline --decorate
git status -sb
```

Expected branch: `codex/external-video-prompt-passthrough` or the isolated execution branch approved at start.

Report to the user:

- exact files changed;
- the two implementation commit hashes;
- focused and full test results;
- no database/config/frontend/external-system changes;
- Backend and Video Worker rebuild requirement;
- explicit statement that supplier-side moderation remains active;
- explicit statement that nothing was pushed or deployed.

Stop and obtain separate approval before pushing to `team/main`, updating `/www/wwwroot/advertising-automation`, rebuilding containers, or running a real provider-billed video test.

## Deployment Checklist After Separate Approval

This section is not part of local implementation execution. Use it only after the user separately approves push and test-server deployment.

1. Verify intended branch commits and company-repository relationship.
2. Confirm no secrets or unrelated files are part of the outgoing commits.
3. Push only the approved implementation commits using the repository's agreed integration flow.
4. On `/www/wwwroot/advertising-automation`, back up `.env.production` without committing it.
5. Pull the approved revision.
6. Rebuild/restart the FastAPI Backend and Video Celery Worker; do not run `down -v`.
7. Verify local and public health endpoints.
8. Submit one new external video task with a distinctive prompt and a new `external_request_id`.
9. Verify the stored/redacted provider request shows the exact trimmed prompt and no injected safety/3A/style text.
10. Verify the transferred video did not receive final overlay post-processing.
11. Verify one internal video prompt still receives the existing augmentation path.
12. Verify the external polling response and final storage URL remain compatible.

## Rollback Checklist

If the approved deployment must be rolled back:

1. Revert or redeploy the two implementation commits.
2. Rebuild/restart Backend and Video Worker.
3. Do not run a database downgrade because this feature has no migration.
4. Verify health and both external/internal video paths.
5. Remember that already-submitted provider jobs keep the prompt they originally received, and already-finished videos are not automatically reprocessed.