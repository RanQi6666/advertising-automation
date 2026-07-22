# External Video Text-to-Video Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the trusted external video endpoint so omitted or empty `images` creates a pure text-to-video job while exactly two images retains first/last-frame generation.

**Architecture:** Infer one private mode at request creation, persist it in existing JSON metadata, and let the worker accept zero source images only for the double marker `source=external_video_generation` plus `generation_mode=text_to_video`. Keep the existing external prompt passthrough and provider code so Volcengine receives `storyboard_text.strip()` with either zero or two image items.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy async ORM, Celery, pytest, Ruff, Volcengine Seedance.

## Global Constraints

- Keep `POST /api/v1/integrations/video-generation/videos`; no new endpoint or public mode field.
- `images` omitted or `[]` means `text_to_video`; exactly two means `first_last_frame`.
- One or more than two images returns HTTP 400 / code `4001`; `images: null` remains schema HTTP 400 / code `4001`.
- Persist mode only in existing `VideoAsset.metadata_json` and `GenerationTask.metadata_json`; no migration.
- Text mode creates no `CreativeAsset` and stores `source_asset_ids=[]`.
- Only the external source plus text-mode double marker may run with zero source images.
- Internal, legacy, missing-mode, and unknown-mode zero-image jobs still fail with `Video task has no source images.`
- Provider prompt for both external modes remains exactly `(video.prompt or "").strip()`.
- Do not inject safety rewrite, 3A rules, style packs, or `creative_strategy`; provider-native moderation remains active.
- Never fall back from failed frame mode to text mode. Same `external_request_id` always returns the original job/mode; switching mode requires a new ID.
- Keep auth, polling, response envelopes, success URL, and failure shape unchanged.
- No frontend, Nginx, `.env.production`, Pixel-system, or paid real-provider execution changes.
- Stage literal files only; never stage `AGENTS.md`, `.env*`, credentials, backups, or unrelated dirt.

## File Map

- `backend/app/services/external_sources.py`: source and private mode constants.
- `backend/app/services/external_video_generation_service.py`: infer, validate, persist, and requeue with the original mode.
- `backend/app/services/video_service.py`: external-only zero-image gate and unchanged prompt passthrough.
- `tests/test_external_video_generation.py`: API, persistence, worker, idempotency, and no-fallback tests.
- `tests/test_video_provider.py`: text-only Volcengine payload characterization.

---

### Task 1: Infer and persist the private mode

**Files:**
- Modify: `backend/app/services/external_sources.py:1-6`
- Modify: `backend/app/services/external_video_generation_service.py:23-170,310-349`
- Test: `tests/test_external_video_generation.py:251-349,875-900`

**Interfaces:**
- Produces `_generation_mode_for_images(images: list[str]) -> str`.
- Produces `EXTERNAL_VIDEO_GENERATION_MODE_TEXT_TO_VIDEO = "text_to_video"`.
- Produces `EXTERNAL_VIDEO_GENERATION_MODE_FIRST_LAST_FRAME = "first_last_frame"`.

- [ ] **Step 1: Write failing creation/persistence tests**

Import the two constants and add this test. It covers both the Pydantic default and explicit empty array and proves the private mode is absent from the response.

```python
from backend.app.services.external_sources import (
    EXTERNAL_VIDEO_GENERATION_MODE_FIRST_LAST_FRAME,
    EXTERNAL_VIDEO_GENERATION_MODE_TEXT_TO_VIDEO,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("image_input", ["omitted", "empty"])
async def test_external_video_generation_creates_text_mode_without_assets(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    image_input: str,
) -> None:
    monkeypatch.setenv("GENERATION_TASK_EXECUTION_BACKEND", "celery")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "backend.app.services.generation_task_dispatcher._enqueue_celery_generation_task",
        lambda task_id, queue_name, priority, countdown_seconds=0: None,
    )
    client, engine, app, _ = await _client_with_db(
        tmp_path, monkeypatch, token="video-token"
    )
    payload = _video_payload(external_request_id=f"external-text-{image_input}")
    if image_input == "omitted":
        payload.pop("images")
    else:
        payload["images"] = []
    try:
        response = client.post(
            "/api/v1/integrations/video-generation/videos",
            headers=_authorized_headers(),
            json=payload,
        )
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert response.status_code == 202
    assert response.json()["code"] == 1001
    assert "generation_mode" not in response.json()["data"]
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        assets = (await session.execute(select(CreativeAsset))).scalars().all()
        videos = (await session.execute(select(VideoAsset))).scalars().all()
        tasks = (await session.execute(select(GenerationTask))).scalars().all()
    assert assets == []
    assert len(videos) == 1
    assert videos[0].source_asset_ids == []
    assert videos[0].metadata_json["generation_mode"] == EXTERNAL_VIDEO_GENERATION_MODE_TEXT_TO_VIDEO
    assert len(tasks) == 1
    assert tasks[0].metadata_json["generation_mode"] == EXTERNAL_VIDEO_GENERATION_MODE_TEXT_TO_VIDEO
    await engine.dispose()
```

Extend `test_external_video_generation_create_returns_job_without_starting_provider`:

```python
assert videos[0].metadata_json["generation_mode"] == EXTERNAL_VIDEO_GENERATION_MODE_FIRST_LAST_FRAME
assert tasks[0].metadata_json["generation_mode"] == EXTERNAL_VIDEO_GENERATION_MODE_FIRST_LAST_FRAME
assert "generation_mode" not in created
assert "generation_mode" not in polled
```

Replace the old image-count test and add null coverage:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "images",
    [[ONE_PIXEL_PNG_BASE64], [ONE_PIXEL_PNG_BASE64] * 3],
)
async def test_external_video_generation_rejects_unsupported_image_counts(
    tmp_path, monkeypatch: pytest.MonkeyPatch, images: list[str]
) -> None:
    client, engine, app, _ = await _client_with_db(tmp_path, monkeypatch, token="video-token")
    try:
        response = client.post(
            "/api/v1/integrations/video-generation/videos",
            headers=_authorized_headers(),
            json=_video_payload(images=images),
        )
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()
    assert response.status_code == 400
    assert response.json()["code"] == 4001
    assert response.json()["message"] == "images must be omitted, empty, or contain exactly 2 base64 images"


@pytest.mark.asyncio
async def test_external_video_generation_rejects_null_images(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine, app, _ = await _client_with_db(tmp_path, monkeypatch, token="video-token")
    try:
        response = client.post(
            "/api/v1/integrations/video-generation/videos",
            headers=_authorized_headers(),
            json=_video_payload(images=None),
        )
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()
    assert response.status_code == 400
    assert response.json()["code"] == 4001
```

- [ ] **Step 2: Run tests to verify the current exact-two guard fails text mode**

```powershell
& "C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe" -m pytest tests/test_external_video_generation.py::test_external_video_generation_creates_text_mode_without_assets tests/test_external_video_generation.py::test_external_video_generation_rejects_unsupported_image_counts tests/test_external_video_generation.py::test_external_video_generation_rejects_null_images tests/test_external_video_generation.py::test_external_video_generation_create_returns_job_without_starting_provider -v
```

Expected: omitted/empty creation and mode metadata assertions fail before implementation.

- [ ] **Step 3: Add constants and the only mode inference branch**

`backend/app/services/external_sources.py`:

```python
EXTERNAL_VIDEO_GENERATION_SOURCE = "external_video_generation"
EXTERNAL_VIDEO_GENERATION_MODE_TEXT_TO_VIDEO = "text_to_video"
EXTERNAL_VIDEO_GENERATION_MODE_FIRST_LAST_FRAME = "first_last_frame"
EXTERNAL_MATERIAL_GENERATION_SOURCE = "external_material_generation"
EXTERNAL_PLACEHOLDER_CAMPAIGN_SOURCES = (
    EXTERNAL_MATERIAL_GENERATION_SOURCE,
    EXTERNAL_VIDEO_GENERATION_SOURCE,
)
```

Import the three video constants in `external_video_generation_service.py`, then add before `_decode_base64_image`:

```python
def _generation_mode_for_images(images: list[str]) -> str:
    if len(images) == 0:
        return EXTERNAL_VIDEO_GENERATION_MODE_TEXT_TO_VIDEO
    if len(images) == 2:
        return EXTERNAL_VIDEO_GENERATION_MODE_FIRST_LAST_FRAME
    raise AppError("images must be omitted, empty, or contain exactly 2 base64 images")
```

- [ ] **Step 4: Infer once and persist in the initial video/task**

Replace the exact-two guard with:

```python
storyboard_text = payload.storyboard_text.strip()
if not storyboard_text:
    raise AppError("storyboard_text is required")
generation_mode = _generation_mode_for_images(payload.images)
```

Keep `decoded_images = [_decode_base64_image(image) for image in payload.images]`; an empty list naturally creates no assets. Add this entry to `VideoAsset.metadata_json` and initial `GenerationTask.metadata`:

```python
"generation_mode": generation_mode,
```

Do not add it to `ExternalVideoGenerationJobRead` or any response serializer.

- [ ] **Step 5: Preserve the stored mode on safe requeue**

In `_requeue_failed_external_video_if_safe`:

```python
metadata = video.metadata_json or {}
generation_mode = metadata.get("generation_mode")
retry_count = _external_retry_count(metadata)
```

Add to replacement task metadata:

```python
"generation_mode": generation_mode,
```

Never infer retry mode from the duplicate request body.

- [ ] **Step 6: Verify and commit Task 1**

```powershell
& "C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe" -m pytest tests/test_external_video_generation.py::test_external_video_generation_creates_text_mode_without_assets tests/test_external_video_generation.py::test_external_video_generation_rejects_unsupported_image_counts tests/test_external_video_generation.py::test_external_video_generation_rejects_null_images tests/test_external_video_generation.py::test_external_video_generation_create_returns_job_without_starting_provider -v
& "C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe" -m ruff check backend/app/services/external_sources.py backend/app/services/external_video_generation_service.py tests/test_external_video_generation.py
git add -- backend/app/services/external_sources.py backend/app/services/external_video_generation_service.py tests/test_external_video_generation.py
git diff --cached --check
git diff --cached --name-only
git commit -m "feat: infer external video generation mode"
```

Expected: tests pass, Ruff exits 0, and only those three files are staged.

---
### Task 2: Enforce the external-only zero-image worker gate

**Files:**
- Modify: `backend/app/services/video_service.py:32,646-718,1490-1510`
- Test: `tests/test_external_video_generation.py:509-597` plus worker-gate tests

**Interfaces:**
- Consumes Task 1 mode metadata.
- Produces `_is_external_text_to_video_generation(video: VideoAsset) -> bool`.
- Preserves `_is_external_video_generation(video)` for prompt passthrough in both external modes.

- [ ] **Step 1: Make the passthrough test exercise text mode**

In `test_external_video_start_passes_storyboard_text_without_backend_augmentation`, import `VideoSourceImage`, add `captured_source_images: list[list[VideoSourceImage]] = []`, and capture `request.source_images`:

```python
class CapturingVideoProvider:
    async def start_generation(self, request):
        captured_prompts.append(request.prompt)
        captured_metadata.append(request.metadata)
        captured_source_images.append(request.source_images)
        return VideoGenerationStart(
            provider_job_id="provider-passthrough-job",
            provider_status="queued",
            raw_response={"id": "provider-passthrough-job", "status": "queued"},
            request_payload={"prompt": request.prompt},
        )
```

Create with no images while retaining the deliberately injected `creative_strategy` metadata:

```python
json=_video_payload(
    external_request_id="external-video-passthrough",
    images=[],
    storyboard_text=storyboard_text,
),
```

Assert:

```python
assert captured_source_images == [[]]
assert captured_prompts == [storyboard_text.strip()]
assert len(captured_metadata) == 1
assert "creative_strategy" not in captured_metadata[0]
```

- [ ] **Step 2: Add failing zero-image boundary tests**

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata_json",
    [
        {},
        {"source": EXTERNAL_SOURCE},
        {"source": EXTERNAL_SOURCE, "generation_mode": "unknown"},
        {"source": "internal", "generation_mode": EXTERNAL_VIDEO_GENERATION_MODE_TEXT_TO_VIDEO},
    ],
)
async def test_video_service_rejects_zero_images_without_external_text_double_marker(
    tmp_path, metadata_json: dict
) -> None:
    engine, session_factory = await _session_factory(tmp_path, "zero-image-gate.db")
    async with session_factory() as session:
        campaign = Campaign(name="Zero image gate", metadata_json={})
        session.add(campaign)
        await session.flush()
        video = VideoAsset(
            campaign_id=campaign.id,
            source_asset_ids=[],
            prompt="Generate directly from text.",
            storyboard=[],
            duration_seconds=12,
            aspect_ratio="9:16",
            status=VideoStatus.REQUESTED.value,
            metadata_json=metadata_json,
        )
        session.add(video)
        await session.commit()
        await session.refresh(video)
        with pytest.raises(AppError, match="Video task has no source images"):
            await VideoService()._build_provider_request(session, video)
    await engine.dispose()


@pytest.mark.asyncio
async def test_video_service_rejects_text_mode_record_with_source_images(tmp_path) -> None:
    engine, session_factory = await _session_factory(tmp_path, "corrupt-text-mode.db")
    async with session_factory() as session:
        campaign = Campaign(name="Corrupt text mode", metadata_json={})
        session.add(campaign)
        await session.flush()
        video = VideoAsset(
            campaign_id=campaign.id,
            source_asset_ids=["unexpected-source-asset"],
            prompt="Generate directly from text.",
            storyboard=[],
            duration_seconds=12,
            aspect_ratio="9:16",
            status=VideoStatus.REQUESTED.value,
            metadata_json={
                "source": EXTERNAL_SOURCE,
                "generation_mode": EXTERNAL_VIDEO_GENERATION_MODE_TEXT_TO_VIDEO,
            },
        )
        session.add(video)
        await session.commit()
        await session.refresh(video)
        with pytest.raises(
            AppError,
            match="External text-to-video tasks must not have source images",
        ):
            await VideoService()._build_provider_request(session, video)
    await engine.dispose()
```

- [ ] **Step 3: Run tests to prove the current global no-image guard blocks text mode**

```powershell
& "C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe" -m pytest tests/test_external_video_generation.py::test_external_video_start_passes_storyboard_text_without_backend_augmentation tests/test_external_video_generation.py::test_video_service_rejects_zero_images_without_external_text_double_marker tests/test_external_video_generation.py::test_video_service_rejects_text_mode_record_with_source_images -v
```

Expected: text-mode passthrough fails with `Video task has no source images.` and the corrupt-record invariant is not yet enforced.

- [ ] **Step 4: Add the double-marker helper**

Change the import in `video_service.py`:

```python
from backend.app.services.external_sources import (
    EXTERNAL_VIDEO_GENERATION_MODE_TEXT_TO_VIDEO,
    EXTERNAL_VIDEO_GENERATION_SOURCE,
)
```

Add immediately after `_is_external_video_generation`:

```python
def _is_external_text_to_video_generation(video: VideoAsset) -> bool:
    metadata = video.metadata_json or {}
    return (
        metadata.get("source") == EXTERNAL_VIDEO_GENERATION_SOURCE
        and metadata.get("generation_mode") == EXTERNAL_VIDEO_GENERATION_MODE_TEXT_TO_VIDEO
    )
```

- [ ] **Step 5: Relax source loading only for the double marker**

Replace the beginning of `_build_provider_request` through source loading:

```python
is_external_text_to_video = _is_external_text_to_video_generation(video)
if not video.source_asset_ids and not is_external_text_to_video:
    raise AppError("Video task has no source images.")
if video.source_asset_ids and is_external_text_to_video:
    raise AppError("External text-to-video tasks must not have source images.")

assets = []
if video.source_asset_ids:
    assets = await self._load_source_assets(
        session=session,
        campaign_id=video.campaign_id,
        asset_ids=video.source_asset_ids,
    )
```

Leave URL resolution, provider image limits, duration validation, and this prompt branch unchanged:

```python
is_external_video = _is_external_video_generation(video)
creative_strategy = None if is_external_video else _strategy_from_metadata(video.metadata_json)
if is_external_video:
    prompt = (video.prompt or "").strip()
```

- [ ] **Step 6: Verify both modes and commit Task 2**

```powershell
& "C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe" -m pytest tests/test_external_video_generation.py::test_external_video_start_passes_storyboard_text_without_backend_augmentation tests/test_external_video_generation.py::test_video_service_rejects_zero_images_without_external_text_double_marker tests/test_external_video_generation.py::test_video_service_rejects_text_mode_record_with_source_images tests/test_external_video_generation.py::test_external_video_generation_start_task_then_polling_returns_url -v
& "C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe" -m ruff check backend/app/services/video_service.py tests/test_external_video_generation.py
git add -- backend/app/services/video_service.py tests/test_external_video_generation.py
git diff --cached --check
git diff --cached --name-only
git commit -m "feat: allow trusted external text video jobs"
```

Expected: text mode reaches the fake provider with `source_images=[]`; existing two-image flow still starts and polls; only two files are staged.

---

### Task 3: Characterize the provider text-only payload

**Files:**
- Test: `tests/test_video_provider.py:14-56`
- Inspect only: `backend/app/integrations/video/volcengine_provider.py:_build_payload`

**Interfaces:**
- Consumes `VideoGenerationRequest(source_images=[])` from Task 2.
- Proves exactly one text item is sent and current first/last-frame roles remain intact.

- [ ] **Step 1: Add the text-only payload test**

```python
def test_volcengine_video_payload_supports_text_only_content() -> None:
    provider = VolcengineVideoProvider(
        api_key="test-key",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        model="ep-20260611001554-nwgqk",
        resolution="720p",
        image_mode="first_last_frame",
        min_duration_seconds=4,
        max_duration_seconds=12,
        max_reference_images=2,
        generate_audio=False,
        watermark=False,
        return_last_frame=False,
        execution_expires_after=172800,
        priority=0,
        safety_identifier="test-user",
    )
    payload = provider._build_payload(
        VideoGenerationRequest(
            prompt="Create a direct-response ad video from text only.",
            source_images=[],
            duration_seconds=12,
            aspect_ratio="9:16",
        )
    )
    assert payload["content"] == [
        {"type": "text", "text": "Create a direct-response ad video from text only."}
    ]
    assert payload["model"] == "ep-20260611001554-nwgqk"
    assert payload["ratio"] == "9:16"
    assert payload["duration"] == 12
    assert payload["safety_identifier"] == "test-user"
```

- [ ] **Step 2: Run provider characterization and retain production provider code unchanged**

```powershell
& "C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe" -m pytest tests/test_video_provider.py::test_volcengine_video_payload_supports_text_only_content tests/test_video_provider.py::test_volcengine_video_payload_uses_seedance_task_schema -v
git diff -- backend/app/integrations/video/volcengine_provider.py
& "C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe" -m ruff check tests/test_video_provider.py
```

Expected: both tests pass immediately and provider diff is empty. If not, stop for design review rather than silently broadening provider behavior.

- [ ] **Step 3: Commit only the test**

```powershell
git add -- tests/test_video_provider.py
git diff --cached --check
git diff --cached --name-only
git commit -m "test: cover text only video provider payload"
```

Expected staged file: exactly `tests/test_video_provider.py`.

---
### Task 4: Lock idempotency, no-fallback behavior, and full regression

**Files:**
- Test: `tests/test_external_video_generation.py:977-1139` and validation section
- Verify: all files changed in Tasks 1-3

**Interfaces:**
- Proves same-ID cross-mode calls return the original job/mode and create no second task.
- Proves malformed two-image input creates no text-mode fallback record.
- Produces final local verification only; no push, merge, server mutation, or paid generation.

- [ ] **Step 1: Add bidirectional cross-mode idempotency coverage**

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_images", "second_images", "expected_mode", "expected_asset_count"),
    [
        ([ONE_PIXEL_PNG_BASE64] * 2, [], EXTERNAL_VIDEO_GENERATION_MODE_FIRST_LAST_FRAME, 2),
        ([], [ONE_PIXEL_PNG_BASE64] * 2, EXTERNAL_VIDEO_GENERATION_MODE_TEXT_TO_VIDEO, 0),
    ],
)
async def test_external_video_generation_same_id_keeps_original_mode(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    first_images: list[str],
    second_images: list[str],
    expected_mode: str,
    expected_asset_count: int,
) -> None:
    monkeypatch.setenv("GENERATION_TASK_EXECUTION_BACKEND", "celery")
    get_settings.cache_clear()
    enqueued: list[tuple[str, str, int]] = []

    def capture_enqueue(task_id: str, queue_name: str, priority: int, countdown_seconds: int = 0) -> None:
        assert countdown_seconds == 0
        enqueued.append((task_id, queue_name, priority))

    monkeypatch.setattr(
        "backend.app.services.generation_task_dispatcher._enqueue_celery_generation_task",
        capture_enqueue,
    )
    client, engine, app, _ = await _client_with_db(tmp_path, monkeypatch, token="video-token")
    request_id = f"same-id-{expected_mode}"
    try:
        first = client.post(
            "/api/v1/integrations/video-generation/videos",
            headers=_authorized_headers(),
            json=_video_payload(external_request_id=request_id, images=first_images),
        )
        second = client.post(
            "/api/v1/integrations/video-generation/videos",
            headers=_authorized_headers(),
            json=_video_payload(external_request_id=request_id, images=second_images),
        )
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["data"]["job_id"] == second.json()["data"]["job_id"]
    assert "generation_mode" not in first.json()["data"]
    assert "generation_mode" not in second.json()["data"]
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        assets = (await session.execute(select(CreativeAsset))).scalars().all()
        videos = (await session.execute(select(VideoAsset))).scalars().all()
        tasks = (await session.execute(select(GenerationTask))).scalars().all()
    assert len(assets) == expected_asset_count
    assert len(videos) == 1
    assert videos[0].metadata_json["generation_mode"] == expected_mode
    assert len(tasks) == 1
    assert tasks[0].metadata_json["generation_mode"] == expected_mode
    assert len(enqueued) == 1
    await engine.dispose()
```

- [ ] **Step 2: Add malformed frame no-fallback coverage**

```python
@pytest.mark.asyncio
async def test_external_video_generation_invalid_frame_does_not_fallback_to_text(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine, app, _ = await _client_with_db(tmp_path, monkeypatch, token="video-token")
    try:
        response = client.post(
            "/api/v1/integrations/video-generation/videos",
            headers=_authorized_headers(),
            json=_video_payload(
                external_request_id="invalid-frame-no-fallback",
                images=[ONE_PIXEL_PNG_BASE64, "not-valid-base64"],
            ),
        )
    finally:
        app.dependency_overrides.clear()
        client.close()
    assert response.status_code == 400
    assert response.json()["code"] == 4001
    assert "valid base64" in response.json()["message"]
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        assets = (await session.execute(select(CreativeAsset))).scalars().all()
        videos = (await session.execute(select(VideoAsset))).scalars().all()
        tasks = (await session.execute(select(GenerationTask))).scalars().all()
    assert assets == []
    assert videos == []
    assert tasks == []
    await engine.dispose()
```

In `test_external_video_generation_requeues_retryable_failed_duplicate_request`, prove both records retain frame mode:

```python
assert video.metadata_json["generation_mode"] == EXTERNAL_VIDEO_GENERATION_MODE_FIRST_LAST_FRAME
assert tasks[-1].metadata_json["generation_mode"] == EXTERNAL_VIDEO_GENERATION_MODE_FIRST_LAST_FRAME
```

- [ ] **Step 3: Run focused idempotency, retry, and no-fallback tests**

```powershell
& "C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe" -m pytest tests/test_external_video_generation.py::test_external_video_generation_reuses_duplicate_external_request_id tests/test_external_video_generation.py::test_external_video_generation_same_id_keeps_original_mode tests/test_external_video_generation.py::test_external_video_generation_invalid_frame_does_not_fallback_to_text tests/test_external_video_generation.py::test_external_video_generation_requeues_retryable_failed_duplicate_request tests/test_external_video_generation.py::test_external_video_generation_does_not_requeue_provider_backed_failure -v
```

Expected: each cross-mode pair produces one original video/task, malformed frames produce no records, and retries retain the original mode.

- [ ] **Step 4: Run complete focused files, Ruff, and full backend tests**

```powershell
& "C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe" -m pytest tests/test_external_video_generation.py tests/test_video_provider.py -v
& "C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe" -m ruff check backend tests
& "C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe" -m pytest
```

Expected: all commands exit 0. Tests use fakes/local payload building and do not make a paid provider request. No frontend build is required because there is no frontend or public contract change.

- [ ] **Step 5: Verify literal scope and secret boundaries**

```powershell
git status --short
git diff --check
git diff --name-only edf562eab28a0c0a74374eb0f9fa48166468a2cd..HEAD
git status --short | Select-String -Pattern 'AGENTS\.md|\.env|migration|alembic|frontend|nginx|pixel_project'
```

Expected implementation paths are limited to:

```text
backend/app/services/external_sources.py
backend/app/services/external_video_generation_service.py
backend/app/services/video_service.py
tests/test_external_video_generation.py
tests/test_video_provider.py
```

The final secret/scope filter produces no output.

- [ ] **Step 6: Commit final regression changes and record local evidence**

```powershell
git add -- tests/test_external_video_generation.py
git diff --cached --check
git diff --cached --name-only
git commit -m "test: lock external text video mode boundaries"
git status -sb
git log --oneline --decorate -6
git diff --stat edf562eab28a0c0a74374eb0f9fa48166468a2cd..HEAD
```

Expected: clean worktree with the design commit plus four scoped implementation/test commits. Stop without pushing, merging, deploying, or calling a real video provider unless separately authorized.

## Post-Implementation Deployment Boundary

A later approved deployment must rebuild both processes that execute the changed logic:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build backend worker_video
```

Deployment verification must separately prove Git revision, container recreation, backend health, worker readiness, authenticated API behavior, and mock/stub text-only payload behavior. Real provider generation costs quota and remains excluded until separately authorized.