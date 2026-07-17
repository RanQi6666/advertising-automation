# External Storyboard V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an isolated asynchronous V2 external storyboard endpoint that analyzes caller-supplied first and last frames with two multimodal model calls and returns a readable, frame-anchored storyboard.

**Architecture:** The new endpoint creates an `external_video_storyboard_v2` task in the existing `text_queue`. The worker first calls the LLM to create internal `frame_analysis`, stores that analysis in task metadata, then calls the LLM again with the same two image inputs plus the analysis to create a structured storyboard. Only the formatted storyboard text is placed in task results and returned by the existing public polling route.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy async, Celery text queue, OpenAI-compatible Chat Completions, Responses API gateway, pytest, httpx.

## Global Constraints

- Preserve `/api/v1/integrations/ai/storyboard` exactly as it is.
- Do not modify `/api/v1/integrations/video-generation/videos`.
- Do not modify the external Pixel project in this implementation.
- V2 accepts only `external_request_id`, `first_frame_image_url`, `last_frame_image_url`, `duration_seconds`, and `aspect_ratio`; all other top-level request fields are forbidden.
- V2 must not construct Campaign, CopyDraft, CreativeAsset, ContentTopic, WorkOrder, or VideoAsset rows.
- V2 must not include Meta/Facebook compliance, creative safety, keyword substitutions, creative_strategy, legacy copy, product name, or fixed 3A templates in either new LLM prompt.
- The first model call reports visual facts without sanitizing or rejecting image content; the second always attempts a bridge between valid supplied frames.
- The first scene must use `first_frame`, every middle scene `transition`, and the final scene `last_frame` as `frame_anchor`.
- Existing public polling returns only job/status fields, request ID, readable `storyboard_text`, duration, and aspect ratio.
- Keep edits ASCII-only.

---

### Task 1: Define the V2 Data and Provider Contracts

**Files:**
- Modify: `backend/app/schemas/external_ai_generation.py`
- Modify: `backend/app/schemas/ai.py`
- Modify: `backend/app/integrations/llm/base.py`
- Modify: `backend/app/integrations/llm/mock_provider.py`
- Test: `tests/test_external_ai_generation.py`

**Interfaces:**
- Produces `ExternalAIFrameAnchoredStoryboardCreate` with `external_request_id`, `first_frame_image_url`, `last_frame_image_url`, `duration_seconds`, and `aspect_ratio`.
- Produces `FrameAnalysis`, `FrameLanguageAnalysis`, `FrameTransitionBrief`, `FrameAnchoredStoryboardScene`, `FrameAnchoredStoryboard`, and `StoryboardSoundDesign` Pydantic schemas.
- Extends `LLMProvider` with `analyze_video_frame_pair(...) -> FrameAnalysis` and `generate_frame_anchored_video_storyboard(...) -> FrameAnchoredStoryboard`.
- Produces deterministic implementations of both methods on `MockLLMProvider` for test and local mock mode.

- [ ] **Step 1: Add failing schema and mock-contract tests**

Add a V2 request helper and tests in `tests/test_external_ai_generation.py`:

```python
def _storyboard_v2_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "external_request_id": "external-ai-storyboard-v2-1",
        "first_frame_image_url": "https://cdn.example.test/first.png",
        "last_frame_image_url": "https://cdn.example.test/last.png",
        "duration_seconds": 12,
        "aspect_ratio": "9:16",
    }
    payload.update(overrides)
    return payload


def test_external_storyboard_v2_rejects_legacy_and_missing_frame_fields() -> None:
    assert ExternalAIFrameAnchoredStoryboardCreate.model_validate(
        _storyboard_v2_payload()
    ).first_frame_image_url.endswith("first.png")

    with pytest.raises(ValidationError):
        ExternalAIFrameAnchoredStoryboardCreate.model_validate(
            _storyboard_v2_payload(first_frame_image_url="")
        )

    with pytest.raises(ValidationError):
        ExternalAIFrameAnchoredStoryboardCreate.model_validate(
            _storyboard_v2_payload(brief="legacy input")
        )


@pytest.mark.asyncio
async def test_mock_provider_returns_frame_anchored_storyboard() -> None:
    provider = MockLLMProvider()
    analysis = await provider.analyze_video_frame_pair(
        first_frame_image_url="https://cdn.example.test/first.png",
        last_frame_image_url="https://cdn.example.test/last.png",
        duration_seconds=12,
        aspect_ratio="9:16",
    )
    storyboard = await provider.generate_frame_anchored_video_storyboard(
        first_frame_image_url="https://cdn.example.test/first.png",
        last_frame_image_url="https://cdn.example.test/last.png",
        frame_analysis=analysis,
        duration_seconds=12,
        aspect_ratio="9:16",
    )

    assert storyboard.scenes[0].frame_anchor == "first_frame"
    assert storyboard.scenes[-1].frame_anchor == "last_frame"
    assert storyboard.sound_design.music
```

- [ ] **Step 2: Run the new tests to verify failure**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_external_ai_generation.py -k "storyboard_v2 or frame_anchored" -v
```

Expected: collection or import failure because the V2 schema, schemas, and mock provider methods do not exist.

- [ ] **Step 3: Add the V2 Pydantic request and internal result schemas**

In `backend/app/schemas/external_ai_generation.py`, add an independent V2 request model rather than inheriting `ExternalAIRequestBase`, because that base exposes the forbidden `language` field:

```python
class ExternalAIFrameAnchoredStoryboardCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_request_id: str | None = Field(default=None, max_length=128)
    first_frame_image_url: str = Field(min_length=1, max_length=2048)
    last_frame_image_url: str = Field(min_length=1, max_length=2048)
    duration_seconds: int = Field(default=12, ge=1, le=300)
    aspect_ratio: str = Field(default="9:16", max_length=32)
```

In `backend/app/schemas/ai.py`, add focused schemas with the following exact fields:

```python
class FrameAnalysis(BaseModel):
    first_frame: FrameVisualFacts
    last_frame: FrameVisualFacts
    transition_brief: FrameTransitionBrief
    language_analysis: FrameLanguageAnalysis


class FrameAnchoredStoryboardScene(BaseModel):
    scene_index: int
    start_second: int | None = None
    end_second: int | None = None
    frame_anchor: Literal["first_frame", "transition", "last_frame"]
    visual: str
    motion: str | None = None
    transition_goal: str | None = None
    subtitle: str | None = None
    voiceover: str | None = None
    sound_effects: list[str] = Field(default_factory=list)
    notes: str | None = None


class FrameAnchoredStoryboard(BaseModel):
    duration_seconds: int
    aspect_ratio: str
    scenes: list[FrameAnchoredStoryboardScene] = Field(default_factory=list)
    sound_design: StoryboardSoundDesign = Field(default_factory=StoryboardSoundDesign)
    rationale: str | None = None
```

Define `FrameVisualFacts`, `FrameTransitionBrief`, `FrameLanguageAnalysis`, and `StoryboardSoundDesign` with the keys described in the approved design document. All textual fields are ordinary strings; do not import or call safety sanitizers in these schemas.

- [ ] **Step 4: Extend the provider protocol and mock provider**

In `backend/app/integrations/llm/base.py`, add:

```python
async def analyze_video_frame_pair(
    self,
    first_frame_image_url: str,
    last_frame_image_url: str,
    duration_seconds: int,
    aspect_ratio: str,
) -> FrameAnalysis:
    """Analyze exact first and last frame images for visual continuity."""

async def generate_frame_anchored_video_storyboard(
    self,
    first_frame_image_url: str,
    last_frame_image_url: str,
    frame_analysis: FrameAnalysis,
    duration_seconds: int,
    aspect_ratio: str,
) -> FrameAnchoredStoryboard:
    """Create an anchored storyboard using the same first and last frames."""
```

Implement both methods on `MockLLMProvider`. The mock analysis must preserve the supplied URLs only as descriptive facts and return `recommended_output_language="en"`. The mock storyboard must return three scenes: first-frame anchor, transition, last-frame anchor; subtitles and voiceover may be `None`; include nonempty `sound_design.music` and `sound_design.ambience`.

- [ ] **Step 5: Run the focused tests to verify pass**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_external_ai_generation.py -k "storyboard_v2 or frame_anchored" -v
```

Expected: PASS.

- [ ] **Step 6: Commit the contract layer**

```powershell
git add backend/app/schemas/external_ai_generation.py backend/app/schemas/ai.py backend/app/integrations/llm/base.py backend/app/integrations/llm/mock_provider.py tests/test_external_ai_generation.py
git commit -m "feat: define frame-anchored storyboard contracts"
```

### Task 2: Implement Multimodal V2 Methods in the LLM Providers

**Files:**
- Modify: `backend/app/integrations/llm/openai_provider.py`
- Modify: `backend/app/integrations/llm/responses_provider.py`
- Create: `tests/test_frame_anchored_storyboard_provider.py`

**Interfaces:**
- Consumes `FrameAnalysis` and `FrameAnchoredStoryboard` from Task 1.
- Produces real OpenAI-compatible and Responses-gateway implementations of `analyze_video_frame_pair` and `generate_frame_anchored_video_storyboard`.
- Uses the exact list content format accepted by `_responses_user_content`: `{"type": "text", "text": ...}` and `{"type": "image_url", "image_url": {"url": ...}}`.

- [ ] **Step 1: Write provider request-capture tests**

Create `tests/test_frame_anchored_storyboard_provider.py` with an `httpx.MockTransport` handler that records the `/responses` JSON body and returns queued JSON text responses. Test both methods through `GatewayResponsesLLMProvider`:

```python
async def test_gateway_frame_analysis_sends_first_then_last_image() -> None:
    provider, captured = _gateway_provider_with_responses(
        '{"first_frame": {...}, "last_frame": {...}, "transition_brief": {...}, "language_analysis": {...}}'
    )
    await provider.analyze_video_frame_pair(
        "https://cdn.example.test/first.png",
        "https://cdn.example.test/last.png",
        12,
        "9:16",
    )

    content = captured["input"][1]["content"]
    assert content[1] == {"type": "input_image", "image_url": "https://cdn.example.test/first.png"}
    assert content[3] == {"type": "input_image", "image_url": "https://cdn.example.test/last.png"}
    assert "Meta/Facebook" not in captured["input"][0]["content"]
    assert "creative_strategy" not in captured["input"][0]["content"]
```

Add a second test that calls `generate_frame_anchored_video_storyboard`, asserts the same image order, asserts the user text includes serialized frame analysis, and asserts the second system prompt contains frame-anchor rules but no legacy policy/template text.

- [ ] **Step 2: Run provider tests to verify failure**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_frame_anchored_storyboard_provider.py -v
```

Expected: FAIL because V2 provider methods do not exist.

- [ ] **Step 3: Add shared multimodal prompt and parsing helpers in `openai_provider.py`**

Implement both V2 methods on `OpenAILLMProvider` so `GatewayResponsesLLMProvider` inherits them and dispatches through its existing `_json_completion` override.

Use a helper with this exact content shape:

```python
def _frame_pair_user_content(
    first_frame_image_url: str,
    last_frame_image_url: str,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {"type": "text", "text": json.dumps(payload, ensure_ascii=False)},
        {"type": "image_url", "image_url": {"url": first_frame_image_url}},
        {"type": "text", "text": "LAST FRAME: analyze this exact ending frame."},
        {"type": "image_url", "image_url": {"url": last_frame_image_url}},
    ]
```

The first text must identify the first image as `FIRST FRAME`. Do not call `_creative_payload_json`, `with_meta_ad_compliance`, `creative_safety_prompt_block`, `_creative_strategy_system_instruction`, or language prompt helpers.

Add `_frame_analysis_from_data(data: dict[str, Any]) -> FrameAnalysis` and `_frame_anchored_storyboard_from_data(data: dict[str, Any], duration_seconds: int, aspect_ratio: str) -> FrameAnchoredStoryboard`. The storyboard parser must validate the anchor sequence exactly:

```python
if not scenes or scenes[0].frame_anchor != "first_frame":
    raise ProviderError("Frame-anchored storyboard must start from first_frame.")
if scenes[-1].frame_anchor != "last_frame":
    raise ProviderError("Frame-anchored storyboard must end at last_frame.")
if any(scene.frame_anchor != "transition" for scene in scenes[1:-1]):
    raise ProviderError("Middle frame-anchored storyboard scenes must use transition.")
```

The analysis system prompt must be factual and contain only the approved visual-analysis responsibilities. The storyboard prompt must contain the approved frame-anchor, symmetric-frame, timing, aspect-ratio, optional-subtitle/voiceover, sound-design, and image-text handling rules.

- [ ] **Step 4: Ensure Responses conversion preserves image order**

Keep `_responses_user_content` as the conversion point. Extend its tests if necessary, but do not change its behavior for existing callers. The V2 provider sends `text` and `image_url` items; the gateway conversion must emit `input_text` and `input_image` in the original list order.

- [ ] **Step 5: Run provider tests to verify pass**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_frame_anchored_storyboard_provider.py -v
```

Expected: PASS, with recorded Responses payloads containing two distinct `input_image` entries in first/last order.

- [ ] **Step 6: Commit provider support**

```powershell
git add backend/app/integrations/llm/openai_provider.py backend/app/integrations/llm/responses_provider.py tests/test_frame_anchored_storyboard_provider.py
git commit -m "feat: add multimodal frame storyboard provider flow"
```

### Task 3: Add the V2 Endpoint, Queue Dispatch, and Internal Metadata Persistence

**Files:**
- Modify: `backend/app/api/v1/endpoints/external_ai_generation.py`
- Modify: `backend/app/services/external_ai_generation_service.py`
- Modify: `backend/app/services/generation_task_service.py`
- Modify: `tests/test_external_ai_generation.py`

**Interfaces:**
- Consumes `ExternalAIFrameAnchoredStoryboardCreate`, `FrameAnalysis`, and `FrameAnchoredStoryboard` from Task 1.
- Consumes V2 provider methods from Task 2.
- Produces `POST /api/v1/integrations/ai/storyboard-v2` and task type `external_video_storyboard_v2`.
- Produces internal task metadata keys `frame_analysis` and `frame_anchored_storyboard`.

- [ ] **Step 1: Write endpoint and worker failing tests**

Extend `FakeExternalAILLM` in `tests/test_external_ai_generation.py` with recording V2 methods:

```python
class FakeExternalAILLM:
    def __init__(self) -> None:
        self.frame_analysis_calls: list[dict[str, object]] = []
        self.frame_storyboard_calls: list[dict[str, object]] = []

    async def analyze_video_frame_pair(self, first_frame_image_url, last_frame_image_url, duration_seconds, aspect_ratio):
        self.frame_analysis_calls.append(locals().copy())
        return _frame_analysis_fixture()

    async def generate_frame_anchored_video_storyboard(self, first_frame_image_url, last_frame_image_url, frame_analysis, duration_seconds, aspect_ratio):
        self.frame_storyboard_calls.append(locals().copy())
        return _frame_anchored_storyboard_fixture(duration_seconds, aspect_ratio)
```

Add tests that:

```python
def test_external_storyboard_v2_async_create_poll_and_private_metadata(...):
    create = client.post(
        "/api/v1/integrations/ai/storyboard-v2",
        headers=_authorized_headers(),
        json=_storyboard_v2_payload(),
    )
    assert create.status_code == 202
    job_id = create.json()["data"]["job_id"]

    polled = client.get(f"/api/v1/integrations/ai/jobs/{job_id}", headers=_authorized_headers())
    public = polled.json()["data"]
    assert public["status"] == "succeeded"
    assert "storyboard_text" in public
    assert "frame_analysis" not in public
    assert "frame_anchored_storyboard" not in public
```

After polling, load the task from SQLite and assert:

```python
assert task.task_type == "external_video_storyboard_v2"
assert task.metadata_json["frame_analysis"]["first_frame"]["opening_state"]
assert task.metadata_json["frame_anchored_storyboard"]["scenes"][0]["frame_anchor"] == "first_frame"
assert await _count_rows(engine, Campaign) == 0
assert await _count_rows(engine, CopyDraft) == 0
assert await _count_rows(engine, CreativeAsset) == 0
assert await _count_rows(engine, VideoAsset) == 0
```

Also test same `external_request_id` returns the same V2 job and wrong/missing V2 fields return existing `4001` validation envelopes.

- [ ] **Step 2: Run V2 external tests to verify failure**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_external_ai_generation.py -k "storyboard_v2" -v
```

Expected: FAIL with a 404 or unsupported task type because route, dispatch, and service implementation are absent.

- [ ] **Step 3: Add the route and service task creation**

In `external_ai_generation.py`, add the V2 route immediately after the legacy storyboard route:

```python
@router.post(
    "/storyboard-v2",
    response_model=ExternalAIGenerationEnvelope,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_frame_anchored_video_storyboard_job(
    payload: ExternalAIFrameAnchoredStoryboardCreate,
    session: DbSession,
    background_tasks: BackgroundTasks,
):
    task = await _service().create_frame_anchored_video_storyboard_job(session, payload)
    schedule_generation_task(task, background_tasks)
    return _accepted_response(task)
```

Use the same `ProviderError` and `AppError` envelope handling as the legacy route.

In `external_ai_generation_service.py`, add:

```python
EXTERNAL_VIDEO_STORYBOARD_V2_TASK_TYPE = "external_video_storyboard_v2"
```

Include it in `EXTERNAL_AI_TEXT_TASK_TYPES`, add a `create_frame_anchored_video_storyboard_job` method that delegates to `_create_job`, and branch it in `execute_task`.

In `generation_task_service.py`, add the type to `TEXT_TASK_TYPES` and to the external-AI task set in `_run_text_task`.

- [ ] **Step 4: Implement V2 worker execution and metadata persistence**

Implement `execute_frame_anchored_video_storyboard(self, session, task)`. Unlike legacy execution, retain `session` to persist internal metadata. The body must be structurally equivalent to:

```python
payload = ExternalAIFrameAnchoredStoryboardCreate.model_validate(task.payload_json or {})
llm = get_llm_provider()
async with llm_text_rate_limiter():
    frame_analysis = await llm.analyze_video_frame_pair(
        first_frame_image_url=payload.first_frame_image_url,
        last_frame_image_url=payload.last_frame_image_url,
        duration_seconds=payload.duration_seconds,
        aspect_ratio=payload.aspect_ratio,
    )
    task.metadata_json = {
        **dict(task.metadata_json or {}),
        "frame_analysis": frame_analysis.model_dump(mode="json"),
    }
    await session.commit()
    await session.refresh(task)

    storyboard = await llm.generate_frame_anchored_video_storyboard(
        first_frame_image_url=payload.first_frame_image_url,
        last_frame_image_url=payload.last_frame_image_url,
        frame_analysis=frame_analysis,
        duration_seconds=payload.duration_seconds,
        aspect_ratio=payload.aspect_ratio,
    )

task.metadata_json = {
    **dict(task.metadata_json or {}),
    "frame_anchored_storyboard": storyboard.model_dump(mode="json"),
}
await session.commit()
```

Return only:

```python
{
    "request_id": _request_id(payload.external_request_id, task.id),
    "storyboard_text": _format_frame_anchored_storyboard_text(storyboard),
    "duration_seconds": storyboard.duration_seconds,
    "aspect_ratio": storyboard.aspect_ratio,
}
```

Do not call `_campaign_from_external_context`, `_draft_from_storyboard_payload`, `_assets_from_storyboard_payload`, `_storyboard_context`, `build_creative_strategy`, or any creative safety helper.

Implement `_format_frame_anchored_storyboard_text` with the approved labels: video duration, aspect ratio, scene/time/anchor, visual, camera, subtitle, voiceover, sound effects, transition goal, overall music, and ambience. Render missing optional values as `None`.

- [ ] **Step 5: Run V2 external tests to verify pass**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_external_ai_generation.py -k "storyboard_v2" -v
```

Expected: PASS. Confirm the recording fake observes analysis before storyboard generation and that the second method receives the exact analysis object returned by the first.

- [ ] **Step 6: Commit V2 service and routing**

```powershell
git add backend/app/api/v1/endpoints/external_ai_generation.py backend/app/services/external_ai_generation_service.py backend/app/services/generation_task_service.py tests/test_external_ai_generation.py
git commit -m "feat: add frame-anchored storyboard v2 endpoint"
```

### Task 4: Run Regression, Lint, and Production-Like Verification

**Files:**
- Modify: none unless a verification failure identifies a narrowly scoped defect.
- Test: `tests/test_external_ai_generation.py`
- Test: `tests/test_frame_anchored_storyboard_provider.py`

**Interfaces:**
- Verifies all Task 1-3 interfaces and legacy external AI behavior together.

- [ ] **Step 1: Run focused unit and integration tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_external_ai_generation.py tests/test_frame_anchored_storyboard_provider.py -v
```

Expected: PASS, including legacy `/storyboard` coverage.

- [ ] **Step 2: Run lint for touched backend and test modules**

Run:

```powershell
.venv\Scripts\python.exe -m ruff check backend/app/schemas/ai.py backend/app/schemas/external_ai_generation.py backend/app/integrations/llm/base.py backend/app/integrations/llm/openai_provider.py backend/app/integrations/llm/responses_provider.py backend/app/integrations/llm/mock_provider.py backend/app/api/v1/endpoints/external_ai_generation.py backend/app/services/external_ai_generation_service.py backend/app/services/generation_task_service.py tests/test_external_ai_generation.py tests/test_frame_anchored_storyboard_provider.py
```

Expected: PASS with no findings.

- [ ] **Step 3: Build and start the local production-like stack**

Run:

```powershell
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
docker compose -f docker-compose.prod.yml --env-file .env.production ps
curl.exe -sS http://127.0.0.1/api/v1/health/live
```

Expected: backend and required `worker_text` services are running; health response is `{"status":"ok"}`.

- [ ] **Step 4: Run a controlled V2 request and poll it**

Use two public image URLs and the configured access token without writing any credentials to source files:

```powershell
$body = @{
  external_request_id = "local-storyboard-v2-$([guid]::NewGuid())"
  first_frame_image_url = "https://.../first.png"
  last_frame_image_url = "https://.../last.png"
  duration_seconds = 12
  aspect_ratio = "9:16"
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1/api/v1/integrations/ai/storyboard-v2" -Headers @{ Authorization = "Bearer $env:AI_ADS_ACCESS_TOKEN" } -ContentType "application/json" -Body $body
```

Poll the returned job ID through `/api/v1/integrations/ai/jobs/{job_id}` until terminal. Verify the result has readable text and no internal analysis fields. Inspect the task only through local database tooling to confirm metadata contains both internal keys.

- [ ] **Step 5: Review final diff and commit only task-owned files**

Run:

```powershell
git diff --check
git status --short
git diff --stat HEAD~3..HEAD
```

Stage only files listed in Tasks 1-3 plus the new provider test. Do not stage `AGENTS.md`, `.env`, `.env.production`, unrelated documentation, `.cursor/`, or runtime artifacts.

If verification fixes were necessary, commit them with:

```powershell
git add <only-fixed-task-files>
git commit -m "fix: verify frame-anchored storyboard v2"
```
