# Material Generation Integration API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build external API endpoints for copy, image, and async video material generation without exposing internal campaign/topic/draft IDs to the external publishing system.

**Architecture:** Add a thin integration layer under `/api/v1/integrations/material-generation/*`. The new service converts external business parameters into minimal internal `Campaign`, `ContentTopic`, `CopyDraft`, `CreativeAsset`, and `VideoAsset` records, delegates generation to existing services, and returns only `code`, `message`, and `data` payloads. Video creation remains async: create/start the provider job first, then refresh/query status by `job_id`.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy async ORM, existing mock LLM provider, existing placeholder image/video providers, pytest, SQLite test databases, local `/storage` static files.

---

## File Structure

- Create `backend/app/schemas/material_generation.py`
  - Request models for shared material input, image options, video options.
  - Response envelope models and constants for `code` values.
  - A small `MaterialGenerationAPIError` exception carrying HTTP status, code, message, and data.

- Create `backend/app/services/material_generation_service.py`
  - Builds minimal internal records from external payloads.
  - Calls `CopywritingService`, `CreativeService`, and `VideoService`.
  - Normalizes generated image/video URLs.
  - Runs brand-safety scans before returning external results.
  - Implements lightweight idempotency by `metadata_json.external_request_id`.

- Create `backend/app/api/v1/endpoints/material_generation.py`
  - Defines the four external endpoints.
  - Converts service exceptions into the unified external response body.
  - Uses a material-specific token dependency so auth failures can return `code = 4003`.

- Modify `backend/app/api/deps.py`
  - Add `require_material_generation_access_token` using the same token sources as `require_ai_ads_access_token`, but with unified response detail.

- Modify `backend/app/api/v1/router.py`
  - Include `material_generation.router` without the existing global `protected_dependencies`; the router endpoints apply their own dependency.

- Modify `backend/app/integrations/image/placeholder_provider.py`
  - Store generated placeholder images as local SVG files under `LOCAL_STORAGE_ROOT`.
  - Return `local://...` storage keys so test/local image calls can return a real `/storage/...` URL.

- Modify `backend/app/services/creative_service.py`
  - When an image provider returns a local storage key without a provider URL, set the public URL immediately.

- Modify `backend/app/integrations/video/placeholder_provider.py`
  - Store a small placeholder video artifact under `LOCAL_STORAGE_ROOT`.
  - Return a local `/storage/...` URL from status polling.

- Modify `backend/app/services/video_storage_service.py`
  - Add `storage_key_for_public_url`, mirroring `ImageStorageService`.

- Modify `backend/app/services/video_service.py`
  - If provider status returns an already-local `/storage/...` video URL, set `url` and `storage_key` directly instead of downloading it again.

- Create `tests/test_material_generation_integration.py`
  - Endpoint-level tests with isolated SQLite databases.
  - Tests cover auth, validation, copy, image URL return, video async creation/query, brand-safety blocking, and `customEventType` recognition for registration events.

---

### Task 1: API Contract Foundation

**Files:**
- Create: `tests/test_material_generation_integration.py`
- Create: `backend/app/schemas/material_generation.py`
- Create: `backend/app/api/v1/endpoints/material_generation.py`
- Modify: `backend/app/api/deps.py`
- Modify: `backend/app/api/v1/router.py`

- [ ] **Step 1: Write failing endpoint contract tests**

Create `tests/test_material_generation_integration.py` with the shared fixtures and the first auth/validation tests:

```python
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import get_settings
from backend.app.db.base import Base
from backend.app.db.session import get_session
from backend.app.main import create_app


@pytest.fixture(autouse=True)
def mock_material_generation_providers(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("IMAGE_PROVIDER", "placeholder")
    monkeypatch.setenv("VIDEO_PROVIDER", "placeholder")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://ai.example.test")
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path / "storage"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _session_factory(tmp_path, filename: str = "material-generation.db"):
    database_url = f"sqlite+aiosqlite:///{(tmp_path / filename).as_posix()}"
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _authorized_headers(token: str = "material-token") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _client_with_db(tmp_path, monkeypatch: pytest.MonkeyPatch, token: str | None = None):
    if token:
        monkeypatch.setenv("AI_ADS_ACCESS_TOKEN", token)
    else:
        monkeypatch.delenv("AI_ADS_ACCESS_TOKEN", raising=False)
    get_settings.cache_clear()

    engine, session_factory = await _session_factory(tmp_path)

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)
    return client, engine, app


@pytest.mark.asyncio
async def test_material_generation_auth_failure_uses_external_error_body(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    try:
        response = client.post(
            "/api/v1/integrations/material-generation/copy",
            json={"product_name": "Demo App", "brief": "Create a clear daily-use ad."},
        )
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()

    assert response.status_code == 401
    assert response.json() == {
        "code": 4003,
        "message": "invalid or missing access token",
        "data": {},
    }


@pytest.mark.asyncio
async def test_material_generation_validation_error_uses_external_error_body(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    try:
        response = client.post(
            "/api/v1/integrations/material-generation/copy",
            headers=_authorized_headers(),
            json={"brief": "Create a clear daily-use ad."},
        )
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()

    assert response.status_code == 400
    assert response.json()["code"] == 4001
    assert response.json()["message"] == "product_name is required"
    assert response.json()["data"] == {}
```

- [ ] **Step 2: Run the failing tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_material_generation_integration.py -q
```

Expected: FAIL because `/api/v1/integrations/material-generation/copy` is not registered yet.

- [ ] **Step 3: Add schemas and token dependency**

Create `backend/app/schemas/material_generation.py`:

```python
from typing import Any, Literal

from fastapi import status
from pydantic import BaseModel, Field, HttpUrl


MATERIAL_CODE_SUCCESS = 0
MATERIAL_CODE_PROCESSING = 1001
MATERIAL_CODE_VALIDATION_ERROR = 4001
MATERIAL_CODE_AUTH_ERROR = 4003
MATERIAL_CODE_BRAND_SAFETY_ERROR = 4091
MATERIAL_CODE_PROVIDER_ERROR = 5001

MaterialJobStatus = Literal["processing", "succeeded", "failed"]


class MaterialGenerationBaseRequest(BaseModel):
    external_request_id: str | None = Field(default=None, max_length=128)
    product_name: str | None = Field(default=None, max_length=255)
    landing_url: HttpUrl | None = None
    audience: str | None = None
    country: str | None = None
    event_name: str | None = None
    customEventType: str | None = None
    language: str | None = None
    brief: str | None = None
    selling_points: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)


class MaterialCopyGenerateRequest(MaterialGenerationBaseRequest):
    pass


class MaterialImageGenerateRequest(MaterialGenerationBaseRequest):
    count: int = Field(default=1, ge=1, le=5)
    size: str = "1:1"


class MaterialVideoGenerateRequest(MaterialGenerationBaseRequest):
    image_urls: list[HttpUrl] = Field(default_factory=list)
    duration_seconds: int = Field(default=6, ge=1, le=300)
    aspect_ratio: str = "9:16"
    prompt: str | None = None


class MaterialGenerationEnvelope(BaseModel):
    code: int
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class MaterialGenerationAPIError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: int = MATERIAL_CODE_VALIDATION_ERROR,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.message = message
        self.code = code
        self.status_code = status_code
        self.data = data or {}
        super().__init__(message)
```

Modify `backend/app/api/deps.py` by adding this dependency under `require_ai_ads_access_token`:

```python
def require_material_generation_access_token(
    authorization: Annotated[str | None, Header()] = None,
    access_token: Annotated[str | None, Query()] = None,
    ai_access_token: Annotated[str | None, Query()] = None,
) -> None:
    expected_token = get_settings().ai_ads_access_token
    if not expected_token:
        return

    provided_token = _bearer_token(authorization) or access_token or ai_access_token
    if provided_token and secrets.compare_digest(provided_token, expected_token):
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "code": 4003,
            "message": "invalid or missing access token",
            "data": {},
        },
        headers={"WWW-Authenticate": "Bearer"},
    )
```

- [ ] **Step 4: Register a minimal endpoint skeleton**

Create `backend/app/api/v1/endpoints/material_generation.py`:

```python
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from backend.app.api.deps import DbSession, require_material_generation_access_token
from backend.app.schemas.material_generation import (
    MATERIAL_CODE_VALIDATION_ERROR,
    MaterialCopyGenerateRequest,
    MaterialGenerationAPIError,
    MaterialGenerationEnvelope,
)

router = APIRouter(
    prefix="/integrations/material-generation",
    dependencies=[Depends(require_material_generation_access_token)],
)


@router.post("/copy", response_model=MaterialGenerationEnvelope)
async def generate_copy(payload: MaterialCopyGenerateRequest, session: DbSession):
    try:
        _validate_base_payload(payload)
        return MaterialGenerationEnvelope(code=0, message="success", data={})
    except MaterialGenerationAPIError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "data": exc.data},
        )


def _validate_base_payload(payload: MaterialCopyGenerateRequest) -> None:
    if not (payload.product_name or "").strip():
        raise MaterialGenerationAPIError(
            "product_name is required",
            code=MATERIAL_CODE_VALIDATION_ERROR,
        )
    has_brief = bool((payload.brief or "").strip())
    has_selling_points = any(point.strip() for point in payload.selling_points)
    if not has_brief and not has_selling_points:
        raise MaterialGenerationAPIError(
            "brief or selling_points is required",
            code=MATERIAL_CODE_VALIDATION_ERROR,
        )
```

Modify `backend/app/api/v1/router.py`:

```python
from backend.app.api.v1.endpoints import (
    ad_generation,
    ad_performance,
    campaigns,
    copywriting,
    creatives,
    health,
    landing_pages,
    legal,
    material_generation,
    reviews,
    topics,
    videos,
    work_orders,
)
```

Add this include near the other integration routers:

```python
api_router.include_router(
    material_generation.router,
    tags=["material-generation"],
)
```

- [ ] **Step 5: Run tests and commit**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_material_generation_integration.py -q
```

Expected: PASS for the two new tests.

Commit only the files from this task:

```powershell
git add backend/app/api/deps.py backend/app/api/v1/router.py backend/app/api/v1/endpoints/material_generation.py backend/app/schemas/material_generation.py tests/test_material_generation_integration.py
git commit -m "feat: add material generation api contract"
```

---

### Task 2: Copy Generation Service

**Files:**
- Modify: `tests/test_material_generation_integration.py`
- Create: `backend/app/services/material_generation_service.py`
- Modify: `backend/app/api/v1/endpoints/material_generation.py`

- [ ] **Step 1: Add failing copy generation tests**

Append these tests to `tests/test_material_generation_integration.py`:

```python
from sqlalchemy import select

from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.topic import ContentTopic


@pytest.mark.asyncio
async def test_material_copy_generation_returns_copy_and_stores_external_context(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    try:
        response = client.post(
            "/api/v1/integrations/material-generation/copy",
            headers=_authorized_headers(),
            json={
                "external_request_id": "copy-ext-1",
                "product_name": "Demo App",
                "landing_url": "https://example.com/demo",
                "audience": "New users who need a simple setup",
                "country": "US",
                "event_name": "quick registration",
                "brief": "Create a clear ad for daily use.",
                "selling_points": ["Simple setup", "Clear content"],
            },
        )
        body = response.json()
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert response.status_code == 200
    assert body["code"] == 0
    assert body["message"] == "success"
    assert body["data"]["request_id"]
    assert body["data"]["primary_text"]
    assert body["data"]["headline"]
    assert body["data"]["cta"]
    assert body["data"]["customEventType"] == "COMPLETE_REGISTRATION"

    async with engine.begin() as conn:
        campaigns = (await conn.execute(select(Campaign))).scalars().all()
        topics = (await conn.execute(select(ContentTopic))).scalars().all()
        drafts = (await conn.execute(select(CopyDraft))).scalars().all()

    assert len(campaigns) == 1
    assert len(topics) == 1
    assert len(drafts) == 1
    assert campaigns[0].metadata_json["source"] == "external_material_generation"
    assert campaigns[0].metadata_json["external_request_id"] == "copy-ext-1"

    await engine.dispose()


@pytest.mark.asyncio
async def test_material_copy_generation_reuses_successful_external_request_id(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    payload = {
        "external_request_id": "copy-ext-idempotent",
        "product_name": "Demo App",
        "brief": "Create a clear ad for daily use.",
    }
    try:
        first = client.post(
            "/api/v1/integrations/material-generation/copy",
            headers=_authorized_headers(),
            json=payload,
        )
        second = client.post(
            "/api/v1/integrations/material-generation/copy",
            headers=_authorized_headers(),
            json=payload,
        )
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["data"]["request_id"] == second.json()["data"]["request_id"]

    async with engine.begin() as conn:
        drafts = (await conn.execute(select(CopyDraft))).scalars().all()

    assert len(drafts) == 1
    await engine.dispose()
```

- [ ] **Step 2: Run the failing copy tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_material_generation_integration.py::test_material_copy_generation_returns_copy_and_stores_external_context tests/test_material_generation_integration.py::test_material_copy_generation_reuses_successful_external_request_id -q
```

Expected: FAIL because the endpoint returns empty data and the service file does not exist.

- [ ] **Step 3: Implement `MaterialGenerationService.generate_copy`**

Create `backend/app/services/material_generation_service.py` with these building blocks:

```python
from typing import Any

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.topic import ContentTopic
from backend.app.schemas.copywriting import CopyGenerateRequest
from backend.app.schemas.material_generation import (
    MATERIAL_CODE_BRAND_SAFETY_ERROR,
    MATERIAL_CODE_SUCCESS,
    MaterialCopyGenerateRequest,
    MaterialGenerationAPIError,
    MaterialGenerationEnvelope,
)
from backend.app.services.brand_safety_policy import scan_brand_safety
from backend.app.services.copywriting_service import CopywritingService
from backend.app.services.custom_event_types import custom_event_type

SOURCE = "external_material_generation"


class MaterialGenerationService:
    def __init__(self) -> None:
        self.copywriting = CopywritingService()

    async def generate_copy(
        self,
        session: AsyncSession,
        payload: MaterialCopyGenerateRequest,
    ) -> MaterialGenerationEnvelope:
        _validate_base_payload(payload)
        existing = await self._find_existing_copy(session, payload.external_request_id)
        if existing:
            return _copy_response(existing, payload.event_name, payload.customEventType)

        campaign, topic = await self._create_context(session, payload, material_type="copy")
        draft = await self.copywriting.generate_copy(
            session,
            CopyGenerateRequest(
                topic_id=topic.id,
                constraints={
                    **payload.constraints,
                    "brief": payload.brief,
                    "selling_points": payload.selling_points,
                    "language": payload.language,
                    "source": SOURCE,
                },
            ),
        )
        draft.metadata_json = {
            **(draft.metadata_json or {}),
            "source": SOURCE,
            "external_request_id": payload.external_request_id,
            "material_type": "copy",
        }
        await session.commit()
        await session.refresh(draft)
        _raise_if_brand_safety_blocked(
            {
                "primary_text": draft.primary_text,
                "headline": draft.headline,
                "description": draft.description,
                "cta": draft.cta,
            }
        )
        return _copy_response(draft, payload.event_name, payload.customEventType)

    async def _create_context(
        self,
        session: AsyncSession,
        payload: MaterialCopyGenerateRequest,
        material_type: str,
    ) -> tuple[Campaign, ContentTopic]:
        parsed_fields = {
            "landing_url": str(payload.landing_url) if payload.landing_url else None,
            "country": payload.country,
            "event_name": payload.event_name or payload.customEventType,
            "audience_description_raw": payload.audience,
        }
        campaign = Campaign(
            name=payload.product_name.strip(),
            product_name=payload.product_name.strip(),
            objective=payload.event_name or payload.customEventType,
            audience_description=payload.audience,
            metadata_json={
                "source": SOURCE,
                "external_request_id": payload.external_request_id,
                "material_type": material_type,
                "work_order": {
                    "raw_content": payload.brief or "",
                    "parsed_fields": parsed_fields,
                    "country": payload.country,
                    "landing_url": str(payload.landing_url) if payload.landing_url else None,
                },
                "landing_page": (
                    {"url": str(payload.landing_url), "status": "provided"}
                    if payload.landing_url
                    else {}
                ),
            },
        )
        session.add(campaign)
        await session.flush()

        topic = ContentTopic(
            campaign_id=campaign.id,
            title=payload.product_name.strip(),
            angle=(payload.brief or "Create a clear product message.").strip(),
            audience=payload.audience,
            selling_points=[point.strip() for point in payload.selling_points if point.strip()],
            source_data={
                "source": SOURCE,
                "external_request_id": payload.external_request_id,
                "material_type": material_type,
            },
        )
        session.add(topic)
        await session.commit()
        await session.refresh(campaign)
        await session.refresh(topic)
        return campaign, topic

    async def _find_existing_copy(
        self,
        session: AsyncSession,
        external_request_id: str | None,
    ) -> CopyDraft | None:
        if not external_request_id:
            return None
        result = await session.execute(select(CopyDraft).order_by(CopyDraft.created_at.desc()))
        for draft in result.scalars().all():
            metadata = draft.metadata_json if isinstance(draft.metadata_json, dict) else {}
            if (
                metadata.get("source") == SOURCE
                and metadata.get("material_type") == "copy"
                and metadata.get("external_request_id") == external_request_id
            ):
                return draft
        return None
```

Add these helper functions in the same file:

```python
def _validate_base_payload(payload: MaterialCopyGenerateRequest) -> None:
    if not (payload.product_name or "").strip():
        raise MaterialGenerationAPIError("product_name is required")
    has_brief = bool((payload.brief or "").strip())
    has_selling_points = any(point.strip() for point in payload.selling_points)
    if not has_brief and not has_selling_points:
        raise MaterialGenerationAPIError("brief or selling_points is required")


def _copy_response(
    draft: CopyDraft,
    event_name: str | None,
    provided_custom_event_type: str | None,
) -> MaterialGenerationEnvelope:
    resolved_event_type = provided_custom_event_type or custom_event_type(event_name)
    return MaterialGenerationEnvelope(
        code=MATERIAL_CODE_SUCCESS,
        message="success",
        data={
            "request_id": draft.id,
            "primary_text": draft.primary_text or draft.body,
            "headline": draft.headline,
            "description": draft.description,
            "cta": draft.cta,
            "customEventType": resolved_event_type,
        },
    )


def _raise_if_brand_safety_blocked(payload: Any) -> None:
    report = scan_brand_safety(payload)
    if report["status"] == "blocked":
        raise MaterialGenerationAPIError(
            "brand safety check failed",
            code=MATERIAL_CODE_BRAND_SAFETY_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            data={"brand_safety": report},
        )
```

- [ ] **Step 4: Wire the copy endpoint to the service**

Modify `backend/app/api/v1/endpoints/material_generation.py`:

```python
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from backend.app.api.deps import DbSession, require_material_generation_access_token
from backend.app.schemas.material_generation import (
    MaterialCopyGenerateRequest,
    MaterialGenerationAPIError,
    MaterialGenerationEnvelope,
)
from backend.app.services.material_generation_service import MaterialGenerationService

router = APIRouter(
    prefix="/integrations/material-generation",
    dependencies=[Depends(require_material_generation_access_token)],
)
service = MaterialGenerationService()


@router.post("/copy", response_model=MaterialGenerationEnvelope)
async def generate_copy(payload: MaterialCopyGenerateRequest, session: DbSession):
    try:
        return await service.generate_copy(session, payload)
    except MaterialGenerationAPIError as exc:
        return _error_response(exc)


def _error_response(exc: MaterialGenerationAPIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "data": exc.data},
    )
```

- [ ] **Step 5: Run tests and commit**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_material_generation_integration.py -q
```

Expected: PASS for auth, validation, and copy tests.

Run:

```powershell
.venv\Scripts\python.exe -m ruff check backend/app/api/deps.py backend/app/api/v1/router.py backend/app/api/v1/endpoints/material_generation.py backend/app/schemas/material_generation.py backend/app/services/material_generation_service.py tests/test_material_generation_integration.py
```

Expected: PASS.

Commit:

```powershell
git add backend/app/api/v1/endpoints/material_generation.py backend/app/services/material_generation_service.py tests/test_material_generation_integration.py
git commit -m "feat: generate external copy materials"
```

---

### Task 3: Image Generation and Local Placeholder URLs

**Files:**
- Modify: `tests/test_material_generation_integration.py`
- Modify: `backend/app/schemas/material_generation.py`
- Modify: `backend/app/api/v1/endpoints/material_generation.py`
- Modify: `backend/app/services/material_generation_service.py`
- Modify: `backend/app/integrations/image/placeholder_provider.py`
- Modify: `backend/app/services/creative_service.py`

- [ ] **Step 1: Add failing image endpoint test**

Append:

```python
@pytest.mark.asyncio
async def test_material_image_generation_returns_public_urls(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    try:
        response = client.post(
            "/api/v1/integrations/material-generation/images",
            headers=_authorized_headers(),
            json={
                "external_request_id": "image-ext-1",
                "product_name": "Demo App",
                "brief": "Create a clean product visual for daily use.",
                "count": 2,
                "size": "1:1",
            },
        )
        body = response.json()
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert response.status_code == 200
    assert body["code"] == 0
    assert body["message"] == "success"
    assert body["data"]["request_id"]
    assert len(body["data"]["urls"]) == 2
    assert all(url.startswith("https://ai.example.test/storage/") for url in body["data"]["urls"])

    await engine.dispose()
```

- [ ] **Step 2: Run the failing image test**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_material_generation_integration.py::test_material_image_generation_returns_public_urls -q
```

Expected: FAIL because `/images` is not implemented.

- [ ] **Step 3: Make placeholder image provider create local storage files**

Modify `backend/app/integrations/image/placeholder_provider.py`:

```python
from pathlib import Path
from uuid import uuid4

from backend.app.core.config import get_settings
from backend.app.schemas.ai import GeneratedImage, ImageBrief


class PlaceholderImageProvider:
    async def generate_images(self, briefs: list[ImageBrief]) -> list[GeneratedImage]:
        settings = get_settings()
        storage_root = Path(settings.local_storage_root)
        images: list[GeneratedImage] = []
        for brief in briefs:
            image_id = str(uuid4())
            relative_path = Path("images") / "placeholder" / f"{image_id}.svg"
            target_path = storage_root / relative_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(_placeholder_svg(brief), encoding="utf-8")
            storage_key = f"local://{relative_path.as_posix()}"
            images.append(
                GeneratedImage(
                    prompt=f"{brief.title}: {brief.visual_direction}. Text: {brief.short_text}",
                    storage_key=storage_key,
                    alt_text=brief.short_text,
                    size=brief.size,
                    metadata={"image_index": brief.image_index, "provider": "placeholder"},
                )
            )
        return images


def _placeholder_svg(brief: ImageBrief) -> str:
    title = _escape_svg_text(brief.title)[:80]
    text = _escape_svg_text(brief.short_text)[:120]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024">'
        '<rect width="1024" height="1024" fill="#f3f4f6"/>'
        '<rect x="96" y="96" width="832" height="832" rx="32" fill="#ffffff" stroke="#d1d5db"/>'
        f'<text x="128" y="220" font-size="48" font-family="Arial" fill="#111827">{title}</text>'
        f'<text x="128" y="300" font-size="30" font-family="Arial" fill="#374151">{text}</text>'
        '<text x="128" y="900" font-size="24" font-family="Arial" fill="#6b7280">Placeholder image</text>'
        "</svg>"
    )


def _escape_svg_text(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
```

Modify `backend/app/services/creative_service.py` in `_asset_from_generated_image` after the provider URL transfer block:

```python
        if not image_url:
            image_url = self.image_storage.public_url_for_storage_key(storage_key)
```

- [ ] **Step 4: Implement image service and endpoint**

Add to `MaterialGenerationService.__init__`:

```python
        self.creatives = CreativeService()
```

Add imports:

```python
from backend.app.schemas.creative import CreativeGenerateRequest
from backend.app.schemas.material_generation import MaterialImageGenerateRequest
from backend.app.services.creative_service import CreativeService
```

Add method:

```python
    async def generate_images(
        self,
        session: AsyncSession,
        payload: MaterialImageGenerateRequest,
    ) -> MaterialGenerationEnvelope:
        _validate_base_payload(payload)
        existing = await self._find_existing_images(session, payload.external_request_id)
        if existing:
            return _image_response(existing)

        copy_result = await self.generate_copy(session, payload)
        draft_id = str(copy_result.data["request_id"])
        assets = await self.creatives.generate_creatives(
            session,
            CreativeGenerateRequest(
                draft_id=draft_id,
                count=payload.count,
                size=payload.size,
            ),
        )
        for asset in assets:
            asset.metadata_json = {
                **(asset.metadata_json or {}),
                "source": SOURCE,
                "external_request_id": payload.external_request_id,
                "material_type": "image",
            }
        await session.commit()
        for asset in assets:
            await session.refresh(asset)
        _raise_if_brand_safety_blocked(
            [{"prompt": asset.prompt, "alt_text": asset.alt_text} for asset in assets]
        )
        return _image_response(assets)
```

Add helper:

```python
def _image_response(assets: list[CreativeAsset]) -> MaterialGenerationEnvelope:
    urls = [asset.url for asset in assets if asset.url]
    if not urls:
        raise MaterialGenerationAPIError(
            "image generation returned no usable urls",
            code=MATERIAL_CODE_PROVIDER_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return MaterialGenerationEnvelope(
        code=MATERIAL_CODE_SUCCESS,
        message="success",
        data={"request_id": assets[0].id, "urls": urls},
    )
```

Add `_find_existing_images`:

```python
    async def _find_existing_images(
        self,
        session: AsyncSession,
        external_request_id: str | None,
    ) -> list[CreativeAsset]:
        if not external_request_id:
            return []
        result = await session.execute(select(CreativeAsset).order_by(CreativeAsset.created_at))
        assets: list[CreativeAsset] = []
        for asset in result.scalars().all():
            metadata = asset.metadata_json if isinstance(asset.metadata_json, dict) else {}
            if (
                metadata.get("source") == SOURCE
                and metadata.get("material_type") == "image"
                and metadata.get("external_request_id") == external_request_id
                and asset.url
            ):
                assets.append(asset)
        return assets
```

Add endpoint in `backend/app/api/v1/endpoints/material_generation.py`:

```python
@router.post("/images", response_model=MaterialGenerationEnvelope)
async def generate_images(payload: MaterialImageGenerateRequest, session: DbSession):
    try:
        return await service.generate_images(session, payload)
    except MaterialGenerationAPIError as exc:
        return _error_response(exc)
```

- [ ] **Step 5: Run tests and commit**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_material_generation_integration.py -q
.venv\Scripts\python.exe -m ruff check backend/app/integrations/image/placeholder_provider.py backend/app/services/creative_service.py backend/app/services/material_generation_service.py backend/app/api/v1/endpoints/material_generation.py tests/test_material_generation_integration.py
```

Expected: PASS.

Commit:

```powershell
git add backend/app/integrations/image/placeholder_provider.py backend/app/services/creative_service.py backend/app/services/material_generation_service.py backend/app/api/v1/endpoints/material_generation.py tests/test_material_generation_integration.py
git commit -m "feat: generate external image materials"
```

---

### Task 4: Async Video Generation and Status Query

**Files:**
- Modify: `tests/test_material_generation_integration.py`
- Modify: `backend/app/schemas/material_generation.py`
- Modify: `backend/app/services/material_generation_service.py`
- Modify: `backend/app/api/v1/endpoints/material_generation.py`
- Modify: `backend/app/integrations/video/placeholder_provider.py`
- Modify: `backend/app/services/video_storage_service.py`
- Modify: `backend/app/services/video_service.py`

- [ ] **Step 1: Add failing video tests**

Append:

```python
@pytest.mark.asyncio
async def test_material_video_generation_creates_async_job_and_query_returns_url(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    try:
        image_response = client.post(
            "/api/v1/integrations/material-generation/images",
            headers=_authorized_headers(),
            json={
                "external_request_id": "video-image-ext-1",
                "product_name": "Demo App",
                "brief": "Create a clean product visual for daily use.",
                "count": 1,
            },
        )
        image_url = image_response.json()["data"]["urls"][0]

        create_response = client.post(
            "/api/v1/integrations/material-generation/videos",
            headers=_authorized_headers(),
            json={
                "external_request_id": "video-ext-1",
                "product_name": "Demo App",
                "brief": "Create a short video for daily use.",
                "image_urls": [image_url],
                "duration_seconds": 6,
                "aspect_ratio": "9:16",
                "prompt": "Use a simple motion sequence.",
            },
        )
        create_body = create_response.json()
        status_response = client.get(
            f"/api/v1/integrations/material-generation/jobs/{create_body['data']['job_id']}",
            headers=_authorized_headers(),
        )
        status_body = status_response.json()
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert create_response.status_code == 202
    assert create_body["code"] == 1001
    assert create_body["message"] == "processing"
    assert create_body["data"]["job_id"]
    assert create_body["data"]["status"] == "processing"

    assert status_response.status_code == 200
    assert status_body["code"] == 0
    assert status_body["message"] == "success"
    assert status_body["data"]["status"] == "succeeded"
    assert status_body["data"]["url"].startswith("https://ai.example.test/storage/")

    await engine.dispose()


@pytest.mark.asyncio
async def test_material_video_generation_requires_reference_image(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    try:
        response = client.post(
            "/api/v1/integrations/material-generation/videos",
            headers=_authorized_headers(),
            json={
                "product_name": "Demo App",
                "brief": "Create a short video for daily use.",
                "image_urls": [],
            },
        )
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()

    assert response.status_code == 400
    assert response.json()["code"] == 4001
    assert response.json()["message"] == "image_urls is required"
```

- [ ] **Step 2: Run the failing video tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_material_generation_integration.py::test_material_video_generation_creates_async_job_and_query_returns_url tests/test_material_generation_integration.py::test_material_video_generation_requires_reference_image -q
```

Expected: FAIL because `/videos` and `/jobs/{job_id}` are not implemented.

- [ ] **Step 3: Make placeholder video status return local storage URL**

Modify `backend/app/integrations/video/placeholder_provider.py`:

```python
from pathlib import Path
from uuid import uuid4

from backend.app.core.config import get_settings
from backend.app.integrations.video.base import (
    VideoGenerationRequest,
    VideoGenerationStart,
    VideoGenerationStatus,
)


class PlaceholderVideoProvider:
    async def start_generation(self, request: VideoGenerationRequest) -> VideoGenerationStart:
        provider_job_id = f"placeholder-video-{uuid4()}"
        payload = {
            "prompt": request.prompt,
            "duration_seconds": request.duration_seconds,
            "aspect_ratio": request.aspect_ratio,
            "source_images": [
                {"id": image.id, "url": image.url, "role": image.role}
                for image in request.source_images
            ],
        }
        return VideoGenerationStart(
            provider_job_id=provider_job_id,
            provider_status="queued",
            raw_response={"id": provider_job_id, "status": "queued", "provider": "placeholder"},
            request_payload=payload,
        )

    async def get_generation_status(self, provider_job_id: str) -> VideoGenerationStatus:
        settings = get_settings()
        relative_path = Path("videos") / "placeholder" / f"{provider_job_id}.mp4"
        target_path = Path(settings.local_storage_root) / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if not target_path.exists():
            target_path.write_bytes(b"placeholder video")
        video_url = f"{settings.public_base_url.rstrip('/')}/storage/{relative_path.as_posix()}"
        return VideoGenerationStatus(
            provider_job_id=provider_job_id,
            provider_status="succeeded",
            video_url=video_url,
            raw_response={
                "id": provider_job_id,
                "status": "succeeded",
                "provider": "placeholder",
                "content": {"video_url": video_url},
            },
        )
```

- [ ] **Step 4: Let video storage recognize local public URLs**

Add to `backend/app/services/video_storage_service.py`:

```python
    def storage_key_for_public_url(self, url: str | None) -> str | None:
        if not url:
            return None

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return None
        if not parsed.path.startswith("/storage/"):
            return None

        storage_path = parsed.path.removeprefix("/storage/").lstrip("/")
        if not storage_path:
            return None

        try:
            relative_path = _relative_path_from_storage_key(f"local://{storage_path}")
        except ProviderError:
            return None
        return f"local://{relative_path}"
```

Modify `backend/app/services/video_service.py` inside `refresh_video_generation` before `transfer_provider_video`:

```python
        if provider_status.video_url and provider_status.provider_status == "succeeded":
            local_storage_key = self.video_storage.storage_key_for_public_url(
                provider_status.video_url
            )
            if local_storage_key:
                stored_storage_key = local_storage_key
                stored_video_url = self.video_storage.public_url_for_storage_key(
                    local_storage_key
                )
            else:
                stored_video_url, stored_storage_key = await self.video_storage.transfer_provider_video(
                    source_url=provider_status.video_url,
                    video_id=video.id,
                    provider_job_id=provider_status.provider_job_id,
                )
            video.url = stored_video_url
            video.storage_key = stored_storage_key
```

- [ ] **Step 5: Implement video service methods and endpoints**

Add imports to `backend/app/services/material_generation_service.py`:

```python
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.enums import VideoStatus
from backend.app.db.models.video_asset import VideoAsset
from backend.app.schemas.material_generation import MaterialVideoGenerateRequest
from backend.app.schemas.video import VideoGenerateRequest
from backend.app.services.image_storage_service import ImageStorageService
from backend.app.services.video_service import VideoService
```

Add to `MaterialGenerationService.__init__`:

```python
        self.videos = VideoService()
        self.image_storage = ImageStorageService()
```

Add methods:

```python
    async def create_video(
        self,
        session: AsyncSession,
        payload: MaterialVideoGenerateRequest,
    ) -> MaterialGenerationEnvelope:
        _validate_base_payload(payload)
        if not payload.image_urls:
            raise MaterialGenerationAPIError("image_urls is required")
        existing = await self._find_existing_video(session, payload.external_request_id)
        if existing and existing.status in {VideoStatus.GENERATING.value, VideoStatus.GENERATED.value}:
            return _video_processing_response(existing)

        campaign, topic = await self._create_context(session, payload, material_type="video")
        draft = CopyDraft(
            campaign_id=campaign.id,
            topic_id=topic.id,
            body=(payload.brief or payload.prompt or "External video generation request").strip(),
            primary_text=payload.brief,
            headline=payload.product_name,
            metadata_json={
                "source": SOURCE,
                "external_request_id": payload.external_request_id,
                "material_type": "video",
            },
        )
        session.add(draft)
        await session.flush()
        source_assets = await self._source_assets_from_urls(session, campaign.id, draft.id, payload)
        await session.commit()

        prompt = _video_prompt(payload)
        video = await self.videos.create_video_job(
            session,
            VideoGenerateRequest(
                campaign_id=campaign.id,
                draft_id=draft.id,
                creative_asset_ids=[asset.id for asset in source_assets],
                prompt=prompt,
                duration_seconds=payload.duration_seconds,
                aspect_ratio=payload.aspect_ratio,
                metadata_json={
                    "source": SOURCE,
                    "external_request_id": payload.external_request_id,
                    "material_type": "video",
                },
            ),
        )
        started = await self.videos.start_video_generation(session, video.id)
        return _video_processing_response(started)

    async def get_video_job(
        self,
        session: AsyncSession,
        job_id: str,
    ) -> MaterialGenerationEnvelope:
        video = await session.get(VideoAsset, job_id)
        if video is None:
            raise MaterialGenerationAPIError(
                "video job not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        if video.status == VideoStatus.GENERATING.value and video.provider_job_id:
            video = await self.videos.refresh_video_generation(session, video.id)
        if video.status == VideoStatus.GENERATED.value and video.url:
            return MaterialGenerationEnvelope(
                code=MATERIAL_CODE_SUCCESS,
                message="success",
                data={"job_id": video.id, "status": "succeeded", "url": video.url},
            )
        if video.status == VideoStatus.FAILED.value:
            return MaterialGenerationEnvelope(
                code=MATERIAL_CODE_PROVIDER_ERROR,
                message="video generation failed",
                data={
                    "job_id": video.id,
                    "status": "failed",
                    "error": video.error_message,
                },
            )
        return _video_processing_response(video)
```

Add helpers:

```python
    async def _source_assets_from_urls(
        self,
        session: AsyncSession,
        campaign_id: str,
        draft_id: str,
        payload: MaterialVideoGenerateRequest,
    ) -> list[CreativeAsset]:
        assets: list[CreativeAsset] = []
        for index, image_url in enumerate(payload.image_urls, start=1):
            url = str(image_url)
            storage_key = self.image_storage.storage_key_for_public_url(url)
            asset = CreativeAsset(
                campaign_id=campaign_id,
                draft_id=draft_id,
                url=url,
                storage_key=storage_key,
                prompt=payload.prompt or payload.brief or "External reference image",
                alt_text=f"External reference image {index}",
                size=payload.aspect_ratio,
                metadata_json={
                    "source": SOURCE,
                    "external_request_id": payload.external_request_id,
                    "material_type": "video_source_image",
                    "provider_image_url": url,
                },
            )
            session.add(asset)
            assets.append(asset)
        await session.flush()
        return assets
```

Add endpoint imports and routes:

```python
from fastapi import status
from backend.app.schemas.material_generation import (
    MaterialImageGenerateRequest,
    MaterialVideoGenerateRequest,
)


@router.post(
    "/videos",
    response_model=MaterialGenerationEnvelope,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_video(payload: MaterialVideoGenerateRequest, session: DbSession):
    try:
        result = await service.create_video(session, payload)
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=result.model_dump())
    except MaterialGenerationAPIError as exc:
        return _error_response(exc)


@router.get("/jobs/{job_id}", response_model=MaterialGenerationEnvelope)
async def get_job(job_id: str, session: DbSession):
    try:
        return await service.get_video_job(session, job_id)
    except MaterialGenerationAPIError as exc:
        return _error_response(exc)
```

- [ ] **Step 6: Run tests and commit**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_material_generation_integration.py -q
.venv\Scripts\python.exe -m ruff check backend tests
```

Expected: PASS.

Commit:

```powershell
git add backend/app/integrations/video/placeholder_provider.py backend/app/services/video_storage_service.py backend/app/services/video_service.py backend/app/services/material_generation_service.py backend/app/api/v1/endpoints/material_generation.py tests/test_material_generation_integration.py
git commit -m "feat: generate external video materials asynchronously"
```

---

### Task 5: Brand Safety, Error Mapping, and Verification

**Files:**
- Modify: `tests/test_material_generation_integration.py`
- Modify: `backend/app/services/material_generation_service.py`
- Modify: `backend/app/api/v1/endpoints/material_generation.py`
- Modify: `docs/外部投放系统对接说明.md`

- [ ] **Step 1: Add failing brand-safety and provider-error tests**

Append:

```python
@pytest.mark.asyncio
async def test_material_generation_blocks_brand_safety_risky_input(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    try:
        response = client.post(
            "/api/v1/integrations/material-generation/copy",
            headers=_authorized_headers(),
            json={
                "product_name": "Demo App",
                "brief": "Create a free discount ad with low price language.",
            },
        )
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()

    assert response.status_code == 409
    assert response.json()["code"] == 4091
    assert response.json()["message"] == "brand safety check failed"
    assert response.json()["data"]["brand_safety"]["status"] == "blocked"


@pytest.mark.asyncio
async def test_material_generation_accepts_ai_access_token_query(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    try:
        response = client.post(
            "/api/v1/integrations/material-generation/copy?ai_access_token=material-token",
            json={
                "product_name": "Demo App",
                "brief": "Create a clear daily-use ad.",
            },
        )
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()

    assert response.status_code == 200
    assert response.json()["code"] == 0
```

- [ ] **Step 2: Run failing tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_material_generation_integration.py::test_material_generation_blocks_brand_safety_risky_input tests/test_material_generation_integration.py::test_material_generation_accepts_ai_access_token_query -q
```

Expected: first test FAIL until request scanning is added, second should PASS if Task 1 token dependency is correct.

- [ ] **Step 3: Scan request payloads before generation**

Add to `backend/app/services/material_generation_service.py`:

```python
def _request_brand_safety_payload(payload: MaterialGenerationBaseRequest) -> dict[str, Any]:
    return {
        "product_name": payload.product_name,
        "brief": payload.brief,
        "selling_points": payload.selling_points,
        "constraints": payload.constraints,
    }
```

Call this at the start of `generate_copy`, `generate_images`, and `create_video` after `_validate_base_payload(payload)`:

```python
        _raise_if_brand_safety_blocked(_request_brand_safety_payload(payload))
```

Ensure `MaterialGenerationBaseRequest` is imported from `backend.app.schemas.material_generation`.

- [ ] **Step 4: Convert provider and app errors at endpoint boundary**

Modify `_error_response` handling in `backend/app/api/v1/endpoints/material_generation.py` by adding imports:

```python
from backend.app.core.errors import AppError, ProviderError
from backend.app.schemas.material_generation import MATERIAL_CODE_PROVIDER_ERROR
```

Wrap each endpoint with these except clauses:

```python
    except MaterialGenerationAPIError as exc:
        return _error_response(exc)
    except ProviderError as exc:
        return JSONResponse(
            status_code=500,
            content={"code": MATERIAL_CODE_PROVIDER_ERROR, "message": str(exc), "data": {}},
        )
    except AppError as exc:
        return JSONResponse(
            status_code=400,
            content={"code": 4001, "message": str(exc), "data": {}},
        )
```

- [ ] **Step 5: Add external integration documentation**

Append a concise section to `docs/外部投放系统对接说明.md`:

```markdown
## 外部素材生成接口

鉴权方式继续使用 `AI_ADS_ACCESS_TOKEN`：

```http
Authorization: Bearer 8100503f7747bdedfc8269458aa0202c3788f9575260b7fc90fbe8f4e7295495
```

文案生成：

```text
POST https://ai.ggcss.xyz/api/v1/integrations/material-generation/copy
```

图片生成：

```text
POST https://ai.ggcss.xyz/api/v1/integrations/material-generation/images
```

视频生成：

```text
POST https://ai.ggcss.xyz/api/v1/integrations/material-generation/videos
GET  https://ai.ggcss.xyz/api/v1/integrations/material-generation/jobs/{job_id}
```

文案和图片接口同步返回 `code = 0`。视频创建接口先返回 `code = 1001` 和 `job_id`，外部系统轮询查询接口，成功后返回 `code = 0` 和视频链接。
```

- [ ] **Step 6: Run full verification**

Run backend checks:

```powershell
.venv\Scripts\python.exe -m ruff check backend tests
.venv\Scripts\python.exe -m pytest
```

Expected: ruff PASS and pytest PASS.

Run frontend build only if route exports or generated docs touch frontend code. This plan does not touch frontend code, so frontend build is optional. If in doubt, run:

```powershell
cd frontend\web-admin
npm run build
```

Run local production-like Docker rebuild because this changes backend API behavior:

```powershell
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
docker compose -f docker-compose.prod.yml --env-file .env.production ps
curl.exe -i http://127.0.0.1/api/v1/health/live
```

Expected: compose services running and health endpoint returns `{"status":"ok"}`.

- [ ] **Step 7: Final commit**

Commit the remaining changes:

```powershell
git add backend/app/services/material_generation_service.py backend/app/api/v1/endpoints/material_generation.py tests/test_material_generation_integration.py docs/外部投放系统对接说明.md
git commit -m "feat: enforce material generation safety and docs"
```

---

## Self-Review

Spec coverage:

- External endpoints: covered by Tasks 1 through 4.
- Copy sync response: covered by Task 2.
- Image sync URL response: covered by Task 3.
- Video async create and query: covered by Task 4.
- Shared auth and `code/message/data`: covered by Tasks 1 and 5.
- Brand safety: covered by Task 5.
- No internal IDs required from external system: covered by service context creation in Tasks 2 through 4.
- Existing work-order, final review, callback, and ad-performance flows remain unchanged: plan adds new modules and one router include, with no changes to existing payload contracts.

Placeholder scan:

- The plan avoids unfinished placeholder markers and unspecified test commands.
- Every task lists exact files, exact tests, expected outcomes, and commit commands.

Type consistency:

- Request classes are named `MaterialCopyGenerateRequest`, `MaterialImageGenerateRequest`, and `MaterialVideoGenerateRequest`.
- The response class is consistently `MaterialGenerationEnvelope`.
- The service class is consistently `MaterialGenerationService`.
- The external error class is consistently `MaterialGenerationAPIError`.
