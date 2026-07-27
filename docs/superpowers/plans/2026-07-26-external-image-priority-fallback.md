# External Image Priority Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route eligible external one-image text-to-image jobs through JBB and DM Fox in equal priority, then CPA Gemini and Volcengine only after technical failures.

**Architecture:** Add `priority_fallback` as a route mode separate from ordinary round-robin. A dedicated Redis counter alternates JBB and DM Fox for new eligible jobs; the existing Celery retry lifecycle changes only the persisted route metadata after an eligible technical error. The same `GenerationTask` and public `job_id` survive all retries, with JSON metadata retaining the non-sensitive provider transition history.

**Tech Stack:** FastAPI, Pydantic Settings, SQLAlchemy async tasks, Celery, Redis, pytest, pytest-asyncio.

## Global Constraints

- Normal traffic must use only `jbb_gpt_image` and `dm_fox_gpt_image` in strict 1:1 order.
- `cpa_gemini` is first fallback and `volcengine` is final fallback; neither is selected because of queue depth.
- Automatic cross-provider fallback is only for `provider_timeout`, `provider_429`, and `unknown_provider_error`.
- The user accepts duplicate-billing risk after timeout; record provider transitions but never API keys, prompts, response bodies, or signed URLs.
- Route every external text-to-image count through the JBB/DM primary pair, but limit cross-provider fallback to `count=1`; multi-image, edit, reference-image, revision, and internal image tasks do not advance providers.
- Keep `round_robin` and `fixed` mode behavior unchanged.
- No database migration or data backfill: route state remains in `GenerationTask.metadata_json`.

---

## File Structure

- Modify: `backend/app/core/config.py`
  - Declare and validate the `priority_fallback` route mode and its exact primary/fallback provider lists.
- Modify: `backend/app/services/external_image_route_service.py`
  - Select initial priority routes, resolve route-specific models, and build immutable route-transition metadata.
- Modify: `backend/app/services/external_image_generation_service.py`
  - Assign the priority strategy to new external text-to-image tasks and ensure three attempts are available.
- Modify: `backend/app/services/generation_task_service.py`
  - Advance eligible task metadata before the existing delayed retry is scheduled.
- Modify: `.env.example`
  - Document the non-secret production configuration for priority fallback.
- Modify: `.env.production.example`
  - Mirror the non-secret production configuration for priority fallback.
- Modify: `tests/test_external_image_route_service.py`
  - Cover configuration validation, initial JBB/DM alternation, and pure metadata route transitions.
- Modify: `tests/test_external_image_generation.py`
  - Cover eligibility and attempt-count behavior at external task creation.
- Modify: `tests/test_generation_tasks.py`
  - Cover integration with generic automatic retry scheduling and non-eligible task protection.

## Task 1: Add Priority-Fallback Settings and Initial Route Selection

**Files:**
- Modify: `backend/app/core/config.py:45-76, 230-260`
- Modify: `backend/app/services/external_image_route_service.py:1-145`
- Modify: `tests/test_external_image_route_service.py:1-190`

**Interfaces:**
- Consumes: `Settings.redis_url`, provider-specific model settings, and `effective_image_model()`.
- Produces: `select_external_image_route(settings)` returning an `ExternalImageRoute` whose `strategy` is either `round_robin` or `priority_fallback`.
- Produces: `ExternalImageRoute.as_metadata()` with `strategy`, `sequence`, `provider`, and `model`.

- [ ] **Step 1: Write failing route-selection and validation tests**

Add a `priority_fallback_settings` fixture that sets the following environment values and injects `FakeRedis` through `set_redis_client_factory_for_tests`:

```python
monkeypatch.setenv("EXTERNAL_IMAGE_ROUTE_MODE", "priority_fallback")
monkeypatch.setenv(
    "EXTERNAL_IMAGE_PRIORITY_PRIMARY_PROVIDERS",
    "jbb_gpt_image,dm_fox_gpt_image",
)
monkeypatch.setenv(
    "EXTERNAL_IMAGE_PRIORITY_FALLBACK_PROVIDERS",
    "cpa_gemini,volcengine",
)
monkeypatch.setenv("JBB_GPT_IMAGE_MODEL", "jbb-gpt-image-model")
monkeypatch.setenv("DM_FOX_GPT_IMAGE_MODEL", "dm-fox-gpt-image-model")
monkeypatch.setenv("MODEL_GATEWAY_GEMINI_IMAGE_MODEL", "cpa-gemini-image-model")
monkeypatch.setenv("VOLCENGINE_IMAGE_MODEL", "volcengine-image-model")
monkeypatch.setenv("EXTERNAL_IMAGE_GENERATION_MAX_ATTEMPTS", "3")
```

Add this test and assert the primary pair repeats without selecting CPA or Volcengine:

```python
@pytest.mark.asyncio
async def test_priority_fallback_initial_routes_strictly_alternate_primary_pair(
    priority_fallback_settings: FakeRedis,
) -> None:
    routes = [
        await external_image_route_service.select_external_image_route()
        for _ in range(4)
    ]

    assert [(route.strategy, route.sequence, route.provider, route.model) for route in routes] == [
        ("priority_fallback", 1, "jbb_gpt_image", "jbb-gpt-image-model"),
        ("priority_fallback", 2, "dm_fox_gpt_image", "dm-fox-gpt-image-model"),
        ("priority_fallback", 3, "jbb_gpt_image", "jbb-gpt-image-model"),
        ("priority_fallback", 4, "dm_fox_gpt_image", "dm-fox-gpt-image-model"),
    ]
    assert priority_fallback_settings.closed is True
```

Add parameterized invalid-settings cases for a reversed fallback list, a primary list containing Volcengine, and fewer than three `EXTERNAL_IMAGE_GENERATION_MAX_ATTEMPTS`. Each case must assert that `get_settings()` raises `ValidationError`.

- [ ] **Step 2: Run the new tests and verify they fail for missing mode/configuration**

Run:

```powershell
cd "C:\Users\panda\AppData\Local\Temp\广告自动化-新增新中转图片节点-20260725"
.venv\Scripts\python.exe -m pytest tests/test_external_image_route_service.py -q
```

Expected: FAIL because `priority_fallback` is not an allowed `external_image_route_mode` and the priority settings do not exist.

- [ ] **Step 3: Implement settings validation and priority selection**

In `backend/app/core/config.py`, declare a module-level alias before `class Settings` so the configuration module does not import the service-layer `ImageRouteProvider` type:

```python
ExternalImageRouteProvider = Literal[
    "gateway",
    "volcengine",
    "cpa_gemini",
    "jbb_grok",
    "jbb_gpt_image",
    "dm_fox_gpt_image",
    "newcli_gemini",
]
```

In `Settings`, use that alias for the existing route list and add these fields:

```python
external_image_route_mode: Literal["fixed", "round_robin", "priority_fallback"] = "fixed"
external_image_priority_primary_providers: Annotated[list[ExternalImageRouteProvider], NoDecode] = Field(
    default_factory=lambda: ["jbb_gpt_image", "dm_fox_gpt_image"]
)
external_image_priority_fallback_providers: Annotated[list[ExternalImageRouteProvider], NoDecode] = Field(
    default_factory=lambda: ["cpa_gemini", "volcengine"]
)
```

Use the same comma-separated parser as `external_image_route_providers` for both new list settings. Extend the existing `model_validator(mode="after")` with these exact checks when the mode is `priority_fallback`:

```python
if self.external_image_priority_primary_providers != [
    "jbb_gpt_image",
    "dm_fox_gpt_image",
]:
    raise ValueError(
        "EXTERNAL_IMAGE_PRIORITY_PRIMARY_PROVIDERS must be jbb_gpt_image,dm_fox_gpt_image."
    )
if self.external_image_priority_fallback_providers != [
    "cpa_gemini",
    "volcengine",
]:
    raise ValueError(
        "EXTERNAL_IMAGE_PRIORITY_FALLBACK_PROVIDERS must be cpa_gemini,volcengine."
    )
if self.external_image_generation_max_attempts < 3:
    raise ValueError(
        "EXTERNAL_IMAGE_GENERATION_MAX_ATTEMPTS must be at least 3 for priority_fallback."
    )
```

In `external_image_route_service.py`, add:

```python
EXTERNAL_IMAGE_PRIORITY_REDIS_KEY = "external_image_generation:priority_primary"
ImageRouteStrategy = Literal["round_robin", "priority_fallback"]
```

Extend `ExternalImageRoute` with a defaulted `strategy` field and serialize it:

```python
strategy: ImageRouteStrategy = "round_robin"

def as_metadata(self) -> dict[str, str | int]:
    return {
        "strategy": self.strategy,
        "sequence": self.sequence,
        "provider": self.provider,
        "model": self.model,
    }
```

Update `select_external_image_route()` to branch by mode. For `priority_fallback`, increment `EXTERNAL_IMAGE_PRIORITY_REDIS_KEY`, select from `settings.external_image_priority_primary_providers`, resolve the provider model using `settings_for_external_image_route()`, and return `ExternalImageRoute(..., strategy="priority_fallback")`. Preserve the current counter and selection implementation unchanged for `round_robin`.

Update `route_from_metadata()` to read `strategy`, default a missing strategy to `round_robin` for historical tasks, and reject an unknown strategy.

- [ ] **Step 4: Run focused tests and formatter/linter**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_external_image_route_service.py -q
.venv\Scripts\python.exe -m ruff check backend/app/core/config.py backend/app/services/external_image_route_service.py tests/test_external_image_route_service.py
```

Expected: all route-service tests pass and Ruff reports no violations.

- [ ] **Step 5: Commit the isolated route-selection change**

```powershell
git add backend/app/core/config.py backend/app/services/external_image_route_service.py tests/test_external_image_route_service.py
git commit -m "功能：增加生图优先路由选择"
```

## Task 2: Create a Pure Priority-Fallback Metadata Transition Helper

**Files:**
- Modify: `backend/app/services/external_image_route_service.py`
- Modify: `tests/test_external_image_route_service.py`

**Interfaces:**
- Consumes: task metadata, task payload, task type, current attempt number, technical error code, and `Settings`.
- Produces: `advance_priority_fallback_route_metadata(...) -> dict | None`; returns replacement metadata only when a route may safely advance.

- [ ] **Step 1: Write failing metadata-transition tests**

Add tests using a `priority_fallback` route metadata object and a single-image external generation payload:

```python
metadata = {
    "image_route": {
        "strategy": "priority_fallback",
        "sequence": 1,
        "provider": "jbb_gpt_image",
        "model": "jbb-gpt-image-model",
    }
}
payload = {"prompt": "test", "count": 1, "size": "9:16"}
```

Assert this technical transition:

```python
updated = external_image_route_service.advance_priority_fallback_route_metadata(
    metadata,
    payload=payload,
    task_type="external_image_generate",
    attempt_count=1,
    error_code="provider_timeout",
)

assert updated["image_route"]["provider"] == "cpa_gemini"
assert updated["image_route"]["model"] == "cpa-gemini-image-model"
assert updated["image_route_history"] == [{
    "attempt": 1,
    "provider": "jbb_gpt_image",
    "model": "jbb-gpt-image-model",
    "error_code": "provider_timeout",
    "next_provider": "cpa_gemini",
    "next_model": "cpa-gemini-image-model",
}]
```

Add corresponding tests for `dm_fox_gpt_image -> cpa_gemini`, `cpa_gemini -> volcengine`, and `volcengine -> None`. Parameterize `provider_timeout`, `provider_429`, and `unknown_provider_error`. Add a no-transition test for `provider_400`, payload `count=2`, `task_type="image_generate"`, and metadata with `strategy="round_robin"`.

- [ ] **Step 2: Run tests and verify they fail because the helper is missing**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_external_image_route_service.py -q
```

Expected: FAIL with `AttributeError` for `advance_priority_fallback_route_metadata`.

- [ ] **Step 3: Implement the side-effect-free transition helper**

Add these constants and helper functions to `external_image_route_service.py`:

```python
PRIORITY_FALLBACK_ERROR_CODES = {
    "provider_timeout",
    "provider_429",
    "unknown_provider_error",
}
PRIORITY_FALLBACK_NEXT_PROVIDER = {
    "jbb_gpt_image": "cpa_gemini",
    "dm_fox_gpt_image": "cpa_gemini",
    "cpa_gemini": "volcengine",
}

def advance_priority_fallback_route_metadata(
    metadata: dict | None,
    *,
    payload: dict | None,
    task_type: str,
    attempt_count: int,
    error_code: str,
    settings: Settings | None = None,
) -> dict | None:
    ...
```

The implementation must return `None` unless all conditions hold:

```python
task_type == "external_image_generate"
int((payload or {}).get("count") or 1) == 1
error_code in PRIORITY_FALLBACK_ERROR_CODES
route_from_metadata(metadata) is not None
route.strategy == "priority_fallback"
route.provider in PRIORITY_FALLBACK_NEXT_PROVIDER
```

For an eligible transition, derive the next model from the current settings using `settings_for_external_image_route()` and `effective_image_model()`. Return a copied metadata dictionary; do not mutate the input. Preserve existing metadata fields, replace `image_route`, and append one history item containing only `attempt`, `provider`, `model`, `error_code`, `next_provider`, and `next_model`.

- [ ] **Step 4: Run focused tests and lint**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_external_image_route_service.py -q
.venv\Scripts\python.exe -m ruff check backend/app/services/external_image_route_service.py tests/test_external_image_route_service.py
```

Expected: all focused tests pass and the helper has no lint violations.

- [ ] **Step 5: Commit the transition helper**

```powershell
git add backend/app/services/external_image_route_service.py tests/test_external_image_route_service.py
git commit -m "功能：记录生图优先级降级链路"
```

## Task 3: Apply Priority Routing Only to Eligible External Generation Jobs

**Files:**
- Modify: `backend/app/services/external_image_generation_service.py:36-82, 490-512`
- Modify: `tests/test_external_image_generation.py`

**Interfaces:**
- Consumes: `ExternalImageGenerationCreate`, configured route mode, and `select_external_image_route()`.
- Produces: eligible task metadata with `image_route.strategy="priority_fallback"` and `max_attempts=3` or greater.

- [ ] **Step 1: Write failing external-job eligibility tests**

Add one test that creates a text-only payload with `count=1` under `priority_fallback` settings and asserts:

```python
assert task.max_attempts == 3
assert task.metadata_json["image_route"]["strategy"] == "priority_fallback"
assert task.metadata_json["image_route"]["provider"] == "jbb_gpt_image"
```

Add one test that creates a payload with `count=2` under the same settings and asserts it receives the next primary route without consuming a fallback provider:

```python
assert task.metadata_json["image_route"]["strategy"] == "priority_fallback"
assert task.metadata_json["image_route"]["provider"] == "dm_fox_gpt_image"
```

- [ ] **Step 2: Run tests and verify the multi-image guard fails**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_external_image_generation.py -q
```

Expected: FAIL because current settings reject `priority_fallback` and route metadata does not yet expose the strategy.

- [ ] **Step 3: Gate priority metadata at task creation**

Replace the unconditional route-metadata call with an operation-aware helper:

```python
async def _initial_route_metadata(
    settings: Settings,
    *,
    allow_priority_fallback: bool,
) -> dict[str, dict[str, str | int]]:
    if settings.external_image_route_mode == "fixed":
        return {}
    if settings.external_image_route_mode == "priority_fallback" and not allow_priority_fallback:
        return {}
    route = await select_external_image_route(settings)
    return {"image_route": route.as_metadata()}
```

Replace every `_round_robin_route_metadata()` call. In `create_job()`, call `_initial_route_metadata(settings, allow_priority_fallback=True)`. In `create_edit_job()` and `create_revision_job()`, call `_initial_route_metadata(settings, allow_priority_fallback=False)`. This preserves existing fixed/round-robin behavior for those operations while preventing `priority_fallback` selection for them.

Do not change how edit or revision requests execute after selection; only replace their metadata helper call so they explicitly remain outside the priority strategy.

- [ ] **Step 4: Run focused tests and lint**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_external_image_generation.py tests/test_external_image_route_service.py -q
.venv\Scripts\python.exe -m ruff check backend/app/services/external_image_generation_service.py tests/test_external_image_generation.py
```

Expected: all focused tests pass, proving all external text generation starts on JBB or DM Fox while edits and revisions remain outside the priority strategy.

- [ ] **Step 5: Commit the eligibility guard**

```powershell
git add backend/app/services/external_image_generation_service.py tests/test_external_image_generation.py
git commit -m "功能：限制优先生图路由适用范围"
```

## Task 4: Advance the Route Before Existing Automatic Retry Scheduling

**Files:**
- Modify: `backend/app/services/generation_task_service.py:1210-1255`
- Modify: `tests/test_generation_tasks.py:1500-1640`

**Interfaces:**
- Consumes: `GenerationTask`, classified task error code, and `advance_priority_fallback_route_metadata()`.
- Produces: unchanged Celery scheduling behavior with replacement `metadata_json.image_route` for eligible technical failures.

- [ ] **Step 1: Write failing automatic-retry integration tests**

Use the existing SQLite auto-retry fixture style and monkeypatch `_schedule_auto_retry_task`. Create an `external_image_generate` task with `count=1`, `max_attempts=3`, and JBB priority metadata. Invoke `_mark_failed()` with:

```python
TimeoutError("JBB image request timed out")
```

Assert the task remains queued, schedules exactly once, and changes only the stored route and history:

```python
assert stored.status == "queued"
assert stored.attempt_count == 1
assert stored.metadata_json["image_route"]["provider"] == "cpa_gemini"
assert stored.metadata_json["image_route_history"][0]["provider"] == "jbb_gpt_image"
assert stored.metadata_json["auto_retry"]["next_attempt"] == 2
assert scheduled[0][0] == stored.id
```

Add a second technical failure from CPA metadata and assert the next route is `volcengine`. Add a third technical failure from Volcengine metadata and assert terminal `failed` status with no scheduled retry. Add a `ProviderError` containing HTTP 400 and assert the JBB route is unchanged and no cross-provider history exists.

- [ ] **Step 2: Run tests and verify route metadata remains unchanged before implementation**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_generation_tasks.py -q
```

Expected: FAIL at the assertions expecting `cpa_gemini` or `volcengine`, because current automatic retry preserves the original route.

- [ ] **Step 3: Call the transition helper in `_mark_failed()`**

Import `advance_priority_fallback_route_metadata` at the top of `generation_task_service.py`. Immediately before `_scheduled_auto_retry_metadata()` is called, compute and persist the optional transition:

```python
next_metadata = advance_priority_fallback_route_metadata(
    task.metadata_json,
    payload=task.payload_json,
    task_type=task.task_type,
    attempt_count=task.attempt_count,
    error_code=error_code,
)
if next_metadata is not None:
    task.metadata_json = next_metadata
```

Keep the existing retryability classifier, delay selection, scheduler, attempt count, and terminal-failure path unchanged. `_scheduled_auto_retry_metadata()` must then receive the potentially replaced metadata and append its existing auto-retry diagnostics.

- [ ] **Step 4: Run focused regression tests and lint**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_generation_tasks.py tests/test_external_image_route_service.py -q
.venv\Scripts\python.exe -m ruff check backend/app/services/generation_task_service.py tests/test_generation_tasks.py
```

Expected: all focused tests pass; existing non-image automatic retry tests continue to assert unchanged scheduling.

- [ ] **Step 5: Commit retry integration**

```powershell
git add backend/app/services/generation_task_service.py tests/test_generation_tasks.py
git commit -m "功能：技术失败时降级生图节点"
```

## Task 5: Document Configuration and Run Full Verification

**Files:**
- Modify: `.env.example`
- Modify: `.env.production.example`
- Modify: `docs/superpowers/specs/2026-07-26-external-image-priority-fallback-design.md`
- Modify: `docs/superpowers/plans/2026-07-26-external-image-priority-fallback.md`

**Interfaces:**
- Consumes: the completed `priority_fallback` configuration contract.
- Produces: copyable non-secret configuration examples and verified deployment instructions.

- [ ] **Step 1: Update non-secret environment examples**

Replace the route-mode comments with the exact priority configuration:

```env
# External one-image text generation: JBB and DM Fox alternate; CPA then Volcengine are technical-failure fallbacks.
EXTERNAL_IMAGE_ROUTE_MODE=priority_fallback
EXTERNAL_IMAGE_PRIORITY_PRIMARY_PROVIDERS=jbb_gpt_image,dm_fox_gpt_image
EXTERNAL_IMAGE_PRIORITY_FALLBACK_PROVIDERS=cpa_gemini,volcengine
EXTERNAL_IMAGE_GENERATION_MAX_ATTEMPTS=3
```

Retain `EXTERNAL_IMAGE_ROUTE_PROVIDERS` with a comment that it applies only when `EXTERNAL_IMAGE_ROUTE_MODE=round_robin`.

- [ ] **Step 2: Run the complete backend verification suite**

Run:

```powershell
.venv\Scripts\python.exe -m ruff check backend tests
.venv\Scripts\python.exe -m pytest
```

Expected: Ruff exits 0 and pytest reports all tests passing.

- [ ] **Step 3: Review the implementation against the approved design**

Verify each item before commit:

```text
[ ] New one-image tasks start only on JBB or DM Fox.
[ ] JBB and DM Fox alternate 1:1.
[ ] Technical failures advance JBB/DM -> CPA -> Volcengine.
[ ] Timeouts advance providers as explicitly approved.
[ ] 400, moderation, capability, and storage failures do not advance providers.
[ ] Multi-image tasks start on JBB or DM Fox but never advance to CPA Gemini or Volcengine after failure.
[ ] Edit, reference-image, revision, internal, fixed, and round-robin behavior remain unchanged.
[ ] Route history has no prompt, key, response body, or signed URL.
[ ] No migration or data backfill is introduced.
```

- [ ] **Step 4: Commit documentation and final implementation verification**

```powershell
git add .env.example .env.production.example docs/superpowers/specs/2026-07-26-external-image-priority-fallback-design.md docs/superpowers/plans/2026-07-26-external-image-priority-fallback.md
git commit -m "文档：补充生图优先级部署配置"
git status --short
```

Expected: no uncommitted files attributable to this feature.

## Deployment Plan

After the implementation branch is reviewed and pushed to `team/main`, update the test server as follows. Do not execute the live provider test unless separately authorized.

```bash
cd /www/wwwroot/advertising-automation

cp .env.production ".env.production.bak.$(date +%Y%m%d%H%M%S).before-priority-fallback"

# Set only the following non-secret route settings.
# Existing JBB, DM Fox, CPA, and Volcengine credentials remain unchanged.
set_env() {
  key="$1"
  value="$2"
  if grep -qE "^${key}=" .env.production; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env.production
  else
    printf '\n%s=%s\n' "$key" "$value" >> .env.production
  fi
}

set_env EXTERNAL_IMAGE_ROUTE_MODE priority_fallback
set_env EXTERNAL_IMAGE_PRIORITY_PRIMARY_PROVIDERS jbb_gpt_image,dm_fox_gpt_image
set_env EXTERNAL_IMAGE_PRIORITY_FALLBACK_PROVIDERS cpa_gemini,volcengine

grep -qxF 'EXTERNAL_IMAGE_ROUTE_MODE=priority_fallback' .env.production
grep -qxF 'EXTERNAL_IMAGE_PRIORITY_PRIMARY_PROVIDERS=jbb_gpt_image,dm_fox_gpt_image' .env.production
grep -qxF 'EXTERNAL_IMAGE_PRIORITY_FALLBACK_PROVIDERS=cpa_gemini,volcengine' .env.production
grep -qxF 'EXTERNAL_IMAGE_GENERATION_MAX_ATTEMPTS=3' .env.production

docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build backend worker_image
docker compose -f docker-compose.prod.yml --env-file .env.production ps backend worker_image
```

Verify runtime configuration without exposing secrets:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production exec -T backend python - <<'PY'
from backend.app.core.config import get_settings

settings = get_settings()
print(settings.external_image_route_mode)
print(','.join(settings.external_image_priority_primary_providers))
print(','.join(settings.external_image_priority_fallback_providers))
PY

curl -fsS -H "Authorization: Bearer ${AI_ADS_ACCESS_TOKEN}" https://ai.ggcss.xyz/api/v1/health/live
```
