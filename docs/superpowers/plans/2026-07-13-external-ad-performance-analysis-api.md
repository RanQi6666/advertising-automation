# External Facebook Ad Performance Analysis API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a backward-compatible asynchronous Facebook/Meta ad-performance analysis API that accepts one external JSON payload, creates an idempotent job, processes media and public-market research in a dedicated Celery queue, and persists a strictly validated `facebook_ad_analysis_v1` result for polling by `analysis_id`.

**Architecture:** Extend the existing `ad_performance_analyses` record as the PostgreSQL source of truth and create its `GenerationTask` in the same transaction, publishing to `ad_analysis_queue` only after commit. A dedicated worker orchestrates deterministic Meta metrics, private media processing, safe public research, and schema-constrained LLM inference; every optional subsystem may degrade without losing a meaningful rules-based result. The existing synchronous `POST /api/v1/integrations/ad-performance/analyses` route remains unchanged.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2 async, PostgreSQL 16, Alembic, Redis 7, Celery 5, HTTPX, OpenAI/Gateway LLM providers, FFmpeg/FFprobe, Pytest, Ruff, Docker Compose.

## Global Constraints

- Preserve `POST /api/v1/integrations/ad-performance/analyses` exactly as a synchronous HTTP 201 endpoint returning `AdPerformanceAnalysisRead`; do not wrap it in `{code,message,data}`.
- Add only `POST /api/v1/integrations/ad-performance/analysis-jobs` and `GET /api/v1/integrations/ad-performance/analysis-jobs/{analysis_id}`. Do not add callbacks, cancellation, GET-by-`external_request_id`, or `external_account_id`.
- Treat caller-created `external_request_id` as the global POST idempotency key and system-created `analysis_id` as the only polling key. The shared Bearer token authenticates requests but is not part of idempotency.
- New job: HTTP 202 with `code=1001`; same normalized payload replay: HTTP 200 with `code=0`; same ID with a different payload: HTTP 409 with `code=4001`; unknown polling key: HTTP 404 with `code=4001`.
- A known GET job always returns HTTP 200, including `queued`, `processing`, `succeeded`, and `failed` lifecycle states.
- Extend `ad_performance_analyses`; do not introduce a second competing job table. PostgreSQL, not Redis or the Celery result backend, is authoritative for state and final results.
- Create `AdPerformanceAnalysis` and `GenerationTask` in one database transaction, commit once, and publish to Celery only after the commit succeeds.
- Use queue `ad_analysis_queue`, worker `worker_ad_analysis`, task type `ad_performance_analysis`, result schema `facebook_ad_analysis_v1`, and task-level `max_attempts=2`.
- Recovery publication for ad-analysis jobs must use a database lock or atomic claim. The existing generic queued-task scan must not independently publish the same ad-analysis row.
- The caller supplies `creative.image_url` or `creative.video_url`; ignore caller `thumbnail_url` and `video_keyframes` and generate private internal artifacts.
- Media URLs must be complete HTTPS URLs. Initial media allowlisting must include `newpixel.messrocts.com`; video maximums are 20 seconds and 100 MB; accepted containers are MP4, MOV, and WebM, with MP4/H.264 preferred.
- Store media only below `/data/ad-analysis-media/{analysis_id}/`, never under `/storage`, never mount it into nginx, delete it after final-result persistence, and clean orphans older than two hours.
- Safe outbound HTTP must use `httpx.AsyncClient(trust_env=False)`, validate DNS and every redirect, block non-public addresses, stream with hard byte limits, validate MIME and magic bytes, and never forward the API Bearer token or browser cookies.
- Public research defaults to disabled/degraded mode when no provider is configured. Public references normally use `type=public_proxy_signals` and `verified=false`; never invent public-ad CTR, CPC, CPA, purchases, revenue, or ROAS.
- Deterministic rules own Meta facts, formulas, currency, missing-vs-zero semantics, and source labels. Strict Pydantic validation and deterministic merging must prevent LLM output from overwriting those fields.
- The approved sample must deterministically return `verdict=optimize`, `primary_bottleneck=landing_page`, `confidence=medium`, `scale_eligibility=not_ready`, `pause_recommended=false`, and `landing_page_view_rate` approximately `26.74%`.
- Missing purchase data remains `null`/`unavailable`, not zero. Missing request currency uses `account_currency` when supplied and is otherwise `null`; never default to USD.
- Keep existing unrelated workspace changes untouched. Never stage `AGENTS.md`, `.env`, `.env.production`, credentials, `.cursor/`, deleted/renamed spec files, or pre-existing untracked documents and plans.
- `.env.example` and `.env.production.example` already contain unrelated edits. Inspect their diffs and use interactive hunk staging so only this feature's environment-variable additions enter the later configuration commit.

---

## File and Responsibility Map

| Area | File | Responsibility |
| --- | --- | --- |
| External request contract | `backend/app/schemas/external_ad_performance_analysis.py` | Request validation, response envelopes, lifecycle read models, canonical normalization, SHA-256 payload hashing |
| Result contract | `backend/app/schemas/facebook_ad_analysis.py` | `facebook_ad_analysis_v1` models, rule facts, LLM contribution schema, evidence constraints |
| Persistence | `backend/app/db/models/ad_performance_analysis.py` | Existing analysis record extended with asynchronous job state |
| Reference persistence | `backend/app/db/models/ad_analysis_reference_ad.py` | Selected public-reference ads and evidence snapshots |
| Migration | `backend/alembic/versions/20260713_0012_add_async_ad_analysis_jobs.py` | Schema extension from migration head `20260702_0011` |
| Job lifecycle | `backend/app/services/external_ad_performance_analysis_service.py` | Transactional create/get, idempotency conflict handling, state transitions, task execution entry point |
| Dispatch/recovery | `backend/app/services/ad_analysis_dispatch_service.py` | Post-commit publish accounting and atomic stale-dispatch claims |
| Metric rules | `backend/app/services/facebook_ad_metrics.py` | Meta action parsing, objective alignment, funnel metrics, missing-vs-zero semantics, deterministic verdict |
| Result assembly | `backend/app/services/facebook_ad_analysis_assembler.py` | Merge rule-owned facts with validated inferred sections |
| Safe HTTP | `backend/app/services/safe_public_http.py` | SSRF-safe redirects, DNS checks, bounded downloads, MIME/magic checks |
| Media | `backend/app/services/ad_analysis_media_service.py` | Download, FFprobe, FFmpeg thumbnails/keyframes, private cleanup |
| Research provider | `backend/app/integrations/public_research/` | Replaceable public search/fetch/normalization boundary and disabled/JSON providers |
| Research logic | `backend/app/services/ad_analysis_research_service.py` | Profile inference, query generation, candidate normalization, deduplication, scoring, selection |
| LLM prompt | `backend/app/services/facebook_ad_analysis_prompt.py` | Facebook-specific system prompt and compact evidence context |
| Orchestration | `backend/app/services/ad_analysis_orchestrator.py` | Stage progression, degradation, retries, persistence, cleanup, structured logging |
| API | `backend/app/api/v1/endpoints/external_ad_performance_analysis.py` | POST/GET envelopes and external authentication behavior |
| Worker/runtime | `backend/app/services/generation_task_service.py`, `backend/app/worker/tasks.py`, `backend/app/worker/celery_app.py`, `docker-compose.prod.yml` | Dedicated queue execution, recovery, Beat cleanup, worker process |
| External docs | `docs/EXTERNAL_FACEBOOK_AD_ANALYSIS_API.md`, `docs/examples/external-facebook-ad-analysis-request.json` | Caller-facing contract, polling guidance, examples |
| Acceptance | `scripts/verify_external_ad_performance_analysis.ps1` | Local production-like Docker, route, idempotency, polling, FFmpeg, regression checks, plus the mandatory post-deployment test-server health gate |

## Acceptance Traceability

| Acceptance criterion | Primary tasks |
| --- | --- |
| 1–4: create, idempotency, conflict, polling key | Tasks 1, 3, 4, 15, 16 |
| 5–7: server-side media artifacts and degradation | Tasks 8, 9, 13, 14, 16 |
| 8–12: automatic public research and evidence boundaries | Tasks 8, 10, 11, 12, 13, 15 |
| 13–15: strict result schema, verdict, Facebook funnel and sources | Tasks 6, 7, 12, 13, 15 |
| 16: synchronous route and other queues unchanged | Tasks 3, 4, 5, 14, 15, 16 |
| 17: local production-like Docker and post-deployment test-server health | Tasks 14 and 16 |

### Task 1: Async Request Schemas and Canonical Payload Hashing

**Files:**
- Create: `backend/app/schemas/external_ad_performance_analysis.py`
- Create: `tests/test_external_ad_performance_contract.py`

**Interfaces:**
- Consumes: Existing payload keys `campaign`, `adset`, `creative`, `insight`, `siblings`, and `metadata_json` from `AdPerformanceAnalysisCreate`.
- Produces: `ExternalAdPerformanceAnalysisCreate`; `AdAnalysisEnvelope`; `AdAnalysisCreateData`; `AdAnalysisJobData`; `canonicalize_ad_analysis_payload(payload: ExternalAdPerformanceAnalysisCreate) -> dict[str, Any]`; `ad_analysis_payload_hash(payload: ExternalAdPerformanceAnalysisCreate) -> str`.

- [ ] **Step 1: Write failing request-validation and fixed-hash-vector tests**

```python
from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.app.schemas.external_ad_performance_analysis import (
    ExternalAdPerformanceAnalysisCreate,
    ad_analysis_payload_hash,
    canonicalize_ad_analysis_payload,
)


def _payload() -> dict:
    return {
        "external_request_id": " ad-analysis-20260713-000001 ",
        "source_type": "external",
        "campaign": {},
        "adset": {},
        "creative": {
            "creative_type": "image",
            "image_url": "https://newpixel.messrocts.com/uploads/a.jpg",
            "thumbnail_url": "https://caller.example/thumb.jpg",
            "video_keyframes": [{"second": 0, "image_url": "https://caller.example/0.jpg"}],
            "future_meta_field": "preserved",
        },
        "insight": {
            "spend": "0.2400",
            "actions": [
                {"action_type": "link_click", "value": "86.0"},
                {"action_type": "landing_page_view", "value": "23.00"},
            ],
        },
        "siblings": [],
        "unknown_top_level": {"keep": True},
    }


def test_canonical_payload_ignores_server_generated_media_fields_and_stabilizes_order():
    first = ExternalAdPerformanceAnalysisCreate.model_validate(_payload())
    reordered = deepcopy(_payload())
    reordered["insight"]["actions"].reverse()
    reordered["insight"]["spend"] = "0.24"
    second = ExternalAdPerformanceAnalysisCreate.model_validate(reordered)

    normalized = canonicalize_ad_analysis_payload(first)

    assert first.external_request_id == "ad-analysis-20260713-000001"
    assert normalized["creative"]["future_meta_field"] == "preserved"
    assert "thumbnail_url" not in normalized["creative"]
    assert "video_keyframes" not in normalized["creative"]
    assert "external_request_id" not in normalized
    assert ad_analysis_payload_hash(first) == ad_analysis_payload_hash(second)


def test_canonical_payload_fixed_vector_does_not_drift():
    payload = ExternalAdPerformanceAnalysisCreate.model_validate(
        {
            "external_request_id": "vector-1",
            "source_type": "external",
            "campaign": {},
            "adset": {},
            "creative": {
                "creative_type": "image",
                "image_url": "https://newpixel.messrocts.com/uploads/a.jpg",
            },
            "insight": {
                "actions": [
                    {"action_type": "landing_page_view", "value": "23"},
                    {"action_type": "link_click", "value": "86"},
                ],
                "spend": "0.24",
            },
            "siblings": [],
        }
    )
    assert ad_analysis_payload_hash(payload) == (
        "0691b999c66db8f94340f95657aa10e97cee95758e6a150500e4b2672dabda29"
    )


@pytest.mark.parametrize(
    ("creative", "message"),
    [
        ({"creative_type": "image"}, "creative.image_url is required"),
        ({"creative_type": "video"}, "creative.video_url is required"),
        (
            {"creative_type": "image", "image_url": "http://example.test/a.jpg"},
            "must be a complete HTTPS URL",
        ),
    ],
)
def test_media_contract_rejects_missing_or_non_https_source(creative, message):
    payload = _payload()
    payload["creative"] = creative
    with pytest.raises(ValidationError, match=message):
        ExternalAdPerformanceAnalysisCreate.model_validate(payload)
```

- [ ] **Step 2: Run the focused tests and confirm the new module is missing**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_external_ad_performance_contract.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'backend.app.schemas.external_ad_performance_analysis'`.

- [ ] **Step 3: Add the exact request, envelope, normalization, and hashing contracts**

Implement the following public surface in `backend/app/schemas/external_ad_performance_analysis.py`:

```python
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AD_ANALYSIS_CODE_SUCCESS = 0
AD_ANALYSIS_CODE_ACCEPTED = 1001
AD_ANALYSIS_CODE_VALIDATION_ERROR = 4001
AD_ANALYSIS_CODE_AUTH_ERROR = 4003

_NUMERIC_KEYS = {
    "spend", "impressions", "reach", "frequency", "clicks", "inline_link_clicks",
    "ctr", "inline_link_click_ctr", "cpc", "cpm", "value", "purchase_value",
    "website_purchase_roas", "daily_budget", "lifetime_budget",
}
_IGNORED_CREATIVE_KEYS = {"thumbnail_url", "video_keyframes"}
_REJECTED_TOP_LEVEL_KEYS = {
    "callback_url", "external_account_id", "research_context", "search_keywords",
    "competitor_names",
}


class ExternalAdPerformanceAnalysisCreate(BaseModel):
    model_config = ConfigDict(extra="allow")

    external_request_id: str = Field(min_length=1, max_length=128)
    source_type: str = "external"
    external_user_id: str | None = None
    date_preset: str | None = None
    date_start: str | None = None
    date_stop: str | None = None
    account_currency: str | None = None
    campaign: dict[str, Any]
    adset: dict[str, Any]
    creative: dict[str, Any]
    insight: dict[str, Any]
    siblings: list[dict[str, Any]] = Field(default_factory=list)
    metadata_json: dict[str, Any] = Field(default_factory=dict)

    @field_validator("external_request_id")
    @classmethod
    def strip_external_request_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("external_request_id must not be blank")
        return stripped

    @model_validator(mode="after")
    def validate_external_contract(self) -> "ExternalAdPerformanceAnalysisCreate":
        extras = set(self.model_extra or {})
        rejected = sorted(extras & _REJECTED_TOP_LEVEL_KEYS)
        if rejected:
            raise ValueError(f"unsupported fields: {', '.join(rejected)}")
        creative_type = str(self.creative.get("creative_type") or "").strip().lower()
        if creative_type not in {"image", "video"}:
            raise ValueError("creative.creative_type must be image or video")
        source_field = "image_url" if creative_type == "image" else "video_url"
        source_url = self.creative.get(source_field)
        if not isinstance(source_url, str) or not source_url.strip():
            raise ValueError(f"creative.{source_field} is required")
        parsed = urlparse(source_url.strip())
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError(f"creative.{source_field} must be a complete HTTPS URL")
        return self


class AdAnalysisCreateData(BaseModel):
    analysis_id: str
    external_request_id: str
    status: Literal["queued", "processing", "succeeded", "failed"]
    stage: str
    created_at: str
    poll_url: str
    idempotent_replay: bool = False


class AdAnalysisJobError(BaseModel):
    error_code: str
    message: str
    retryable: bool


class AdAnalysisJobData(BaseModel):
    analysis_id: str
    external_request_id: str
    status: Literal["queued", "processing", "succeeded", "failed"]
    stage: str
    progress: int = Field(ge=0, le=100)
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    result: dict[str, Any] | None = None
    error: AdAnalysisJobError | None = None


class AdAnalysisEnvelope(BaseModel):
    code: int
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


def canonicalize_ad_analysis_payload(
    payload: ExternalAdPerformanceAnalysisCreate,
) -> dict[str, Any]:
    raw = payload.model_dump(mode="python", exclude_none=True)
    raw.pop("external_request_id", None)
    creative = raw.get("creative")
    if isinstance(creative, dict):
        raw["creative"] = {
            key: value for key, value in creative.items() if key not in _IGNORED_CREATIVE_KEYS
        }
    normalized = _normalize_value(raw)
    if not isinstance(normalized, dict):
        raise TypeError("normalized ad-analysis payload must be an object")
    return normalized


def ad_analysis_payload_hash(payload: ExternalAdPerformanceAnalysisCreate) -> str:
    canonical_json = json.dumps(
        canonicalize_ad_analysis_payload(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _normalize_value(value: Any, key: str | None = None) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        result = {
            str(child_key): _normalize_value(child_value, str(child_key))
            for child_key, child_value in value.items()
            if child_value is not None
        }
        for array_key in ("actions", "cost_per_action_type"):
            items = result.get(array_key)
            if isinstance(items, list):
                result[array_key] = sorted(
                    items,
                    key=lambda item: (
                        str(item.get("action_type") or "") if isinstance(item, dict) else "",
                        str(item.get("value") or "") if isinstance(item, dict) else "",
                    ),
                )
        siblings = result.get("siblings")
        if isinstance(siblings, list):
            result["siblings"] = sorted(siblings, key=_sibling_sort_key)
        return result
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if key in _NUMERIC_KEYS and isinstance(value, (str, int, float, Decimal)):
        try:
            decimal_value = Decimal(str(value))
        except InvalidOperation:
            return value
        return format(decimal_value.normalize(), "f")
    return value


def _sibling_sort_key(value: Any) -> tuple[str, str]:
    if not isinstance(value, dict):
        return ("", json.dumps(value, ensure_ascii=False, sort_keys=True))
    creative = value.get("creative") if isinstance(value.get("creative"), dict) else {}
    identity = value.get("facebook_ad_id") or creative.get("fb_id") or creative.get("facebook_ad_id")
    fallback = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (str(identity or ""), fallback)
```

The implementation must omit `None` recursively rather than only at the root, sort only the approved order-insensitive arrays, preserve all other array order, and include model extras in both the saved raw payload and canonical payload unless explicitly rejected or ignored above.

- [ ] **Step 4: Run contract tests and lint the new schema**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_external_ad_performance_contract.py -q
.venv\Scripts\python.exe -m ruff check backend/app/schemas/external_ad_performance_analysis.py tests/test_external_ad_performance_contract.py
```

Expected: all contract tests pass and Ruff prints `All checks passed!`.

- [ ] **Step 5: Commit only Task 1 files**

```powershell
git add backend/app/schemas/external_ad_performance_analysis.py tests/test_external_ad_performance_contract.py
git diff --cached --check
git commit -m "feat: define async ad analysis contract"
```

### Task 2: Migration, Analysis Model Extension, and Reference-Ad Model

**Files:**
- Create: `backend/alembic/versions/20260713_0012_add_async_ad_analysis_jobs.py`
- Create: `backend/app/db/models/ad_analysis_reference_ad.py`
- Modify: `backend/app/db/models/ad_performance_analysis.py`
- Modify: `backend/app/db/models/__init__.py`
- Create: `tests/test_ad_analysis_persistence.py`

**Interfaces:**
- Consumes: Existing `AdPerformanceAnalysis.id`, `request_payload`, `metrics`, `analysis_result`, `status`, and timestamp mixins.
- Produces: Nullable async columns on `AdPerformanceAnalysis`; `AdAnalysisReferenceAd`; unique database constraints for `analysis_id`, `external_request_id`, and `(analysis_record_id, reference_id)`; foreign-key link to `generation_tasks.id`.

- [ ] **Step 1: Write failing model metadata and persistence tests**

```python
import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.db.base import Base
from backend.app.db.models.ad_analysis_reference_ad import AdAnalysisReferenceAd
from backend.app.db.models.ad_performance_analysis import AdPerformanceAnalysis


@pytest.mark.asyncio
async def test_async_analysis_columns_and_reference_ads_persist(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'models.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        columns = await connection.run_sync(
            lambda sync_connection: {
                column["name"]
                for column in inspect(sync_connection).get_columns("ad_performance_analyses")
            }
        )
    assert {
        "analysis_id", "external_request_id", "payload_hash", "normalized_payload",
        "stage", "progress", "analysis_scope", "result_schema_version",
        "generation_task_id", "attempt_count", "max_attempts", "started_at",
        "completed_at", "error_code", "error_retryable", "media_summary",
        "research_summary", "dispatch_claimed_at", "last_dispatched_at", "dispatch_error",
    } <= columns

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        analysis = AdPerformanceAnalysis(
            analysis_id="ana_model_1",
            external_request_id="external-model-1",
            payload_hash="a" * 64,
            request_payload={"creative": {"creative_type": "image"}},
            normalized_payload={"creative": {"creative_type": "image"}},
            status="queued",
            stage="queued",
            progress=0,
            analysis_result={},
        )
        session.add(analysis)
        await session.flush()
        reference = AdAnalysisReferenceAd(
            analysis_record_id=analysis.id,
            reference_id="ref_001",
            source_type="meta_ad_library",
            source_url="https://www.facebook.com/ads/library/?id=1",
            source_domain="facebook.com",
            content_hash="b" * 64,
            similarity_score=0.87,
            performance_evidence_json={"type": "public_proxy_signals", "verified": False},
            creative_analysis_json={},
            raw_excerpt_json={},
        )
        session.add(reference)
        await session.commit()

    assert reference.analysis_record_id == analysis.id
    await engine.dispose()
```

- [ ] **Step 2: Run the model test and confirm the reference model is missing**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ad_analysis_persistence.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'backend.app.db.models.ad_analysis_reference_ad'`.

- [ ] **Step 3: Extend the ORM models without changing existing synchronous field meanings**

Add these typed columns to `AdPerformanceAnalysis` in `backend/app/db/models/ad_performance_analysis.py`:

```python
from sqlalchemy import Boolean, ForeignKey, Integer

analysis_id: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True, index=True)
external_request_id: Mapped[str | None] = mapped_column(
    String(128), nullable=True, unique=True, index=True
)
payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
normalized_payload: Mapped[dict] = mapped_column(JSON, default=json_default)
stage: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
analysis_scope: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
result_schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
attempt_count: Mapped[int] = mapped_column(Integer, default=0)
max_attempts: Mapped[int] = mapped_column(Integer, default=2)
generation_task_id: Mapped[str | None] = mapped_column(
    String(36), ForeignKey("generation_tasks.id", ondelete="SET NULL"), nullable=True, unique=True
)
started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
error_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
error_retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
media_summary: Mapped[dict] = mapped_column(JSON, default=json_default)
research_summary: Mapped[dict] = mapped_column(JSON, default=json_default)
dispatch_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
last_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
dispatch_error: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Create `backend/app/db/models/ad_analysis_reference_ad.py` with a child record keyed to the existing internal UUID:

```python
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, json_default


class AdAnalysisReferenceAd(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ad_analysis_reference_ads"
    __table_args__ = (
        UniqueConstraint(
            "analysis_record_id",
            "reference_id",
            name="uq_ad_analysis_reference_ads_analysis_reference",
        ),
    )

    analysis_record_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("ad_performance_analyses.id", ondelete="CASCADE"),
        index=True,
    )
    reference_id: Mapped[str] = mapped_column(String(64), index=True)
    source_type: Mapped[str] = mapped_column(String(64), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    source_domain: Mapped[str] = mapped_column(String(255), index=True)
    advertiser_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ad_library_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    similarity_score: Mapped[float] = mapped_column(Float, default=0.0)
    performance_evidence_json: Mapped[dict] = mapped_column(JSON, default=json_default)
    creative_analysis_json: Mapped[dict] = mapped_column(JSON, default=json_default)
    raw_excerpt_json: Mapped[dict] = mapped_column(JSON, default=json_default)
```

Export `AdAnalysisReferenceAd` from `backend/app/db/models/__init__.py` and include it in `__all__`.

- [ ] **Step 4: Add the Alembic revision from the verified current head**

Create `backend/alembic/versions/20260713_0012_add_async_ad_analysis_jobs.py` with:

```python
"""add asynchronous ad analysis jobs

Revision ID: 20260713_0012
Revises: 20260702_0011
Create Date: 2026-07-13 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_0012"
down_revision: str | None = "20260702_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ad_performance_analyses", sa.Column("analysis_id", sa.String(36)))
    op.add_column("ad_performance_analyses", sa.Column("external_request_id", sa.String(128)))
    op.add_column("ad_performance_analyses", sa.Column("payload_hash", sa.String(64)))
    op.add_column(
        "ad_performance_analyses",
        sa.Column("normalized_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column("ad_performance_analyses", sa.Column("stage", sa.String(64)))
    op.add_column("ad_performance_analyses", sa.Column("progress", sa.Integer()))
    op.add_column("ad_performance_analyses", sa.Column("analysis_scope", sa.String(64)))
    op.add_column("ad_performance_analyses", sa.Column("result_schema_version", sa.String(64)))
    op.add_column(
        "ad_performance_analyses",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ad_performance_analyses",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="2"),
    )
    op.add_column("ad_performance_analyses", sa.Column("generation_task_id", sa.String(36)))
    op.add_column("ad_performance_analyses", sa.Column("started_at", sa.DateTime(timezone=True)))
    op.add_column("ad_performance_analyses", sa.Column("completed_at", sa.DateTime(timezone=True)))
    op.add_column("ad_performance_analyses", sa.Column("error_code", sa.String(64)))
    op.add_column("ad_performance_analyses", sa.Column("error_retryable", sa.Boolean()))
    op.add_column(
        "ad_performance_analyses",
        sa.Column("media_summary", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "ad_performance_analyses",
        sa.Column("research_summary", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column("ad_performance_analyses", sa.Column("dispatch_claimed_at", sa.DateTime(timezone=True)))
    op.add_column("ad_performance_analyses", sa.Column("last_dispatched_at", sa.DateTime(timezone=True)))
    op.add_column("ad_performance_analyses", sa.Column("dispatch_error", sa.Text()))
    op.create_unique_constraint(
        "uq_ad_performance_analyses_analysis_id", "ad_performance_analyses", ["analysis_id"]
    )
    op.create_unique_constraint(
        "uq_ad_performance_analyses_external_request_id",
        "ad_performance_analyses",
        ["external_request_id"],
    )
    op.create_unique_constraint(
        "uq_ad_performance_analyses_generation_task_id",
        "ad_performance_analyses",
        ["generation_task_id"],
    )
    op.create_foreign_key(
        "fk_ad_performance_analyses_generation_task_id_generation_tasks",
        "ad_performance_analyses",
        "generation_tasks",
        ["generation_task_id"],
        ["id"],
        ondelete="SET NULL",
    )
    for column in (
        "analysis_id", "external_request_id", "payload_hash", "stage", "analysis_scope", "error_code"
    ):
        op.create_index(
            op.f(f"ix_ad_performance_analyses_{column}"),
            "ad_performance_analyses",
            [column],
        )

    op.create_table(
        "ad_analysis_reference_ads",
        sa.Column("analysis_record_id", sa.String(36), nullable=False),
        sa.Column("reference_id", sa.String(64), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_domain", sa.String(255), nullable=False),
        sa.Column("advertiser_name", sa.String(255)),
        sa.Column("ad_library_id", sa.String(128)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("collected_at", sa.DateTime(timezone=True)),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("similarity_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("performance_evidence_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("creative_analysis_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("raw_excerpt_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["analysis_record_id"], ["ad_performance_analyses.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "analysis_record_id",
            "reference_id",
            name="uq_ad_analysis_reference_ads_analysis_reference",
        ),
    )
    for column in (
        "analysis_record_id", "reference_id", "source_type", "source_domain", "ad_library_id", "content_hash"
    ):
        op.create_index(
            op.f(f"ix_ad_analysis_reference_ads_{column}"),
            "ad_analysis_reference_ads",
            [column],
        )


def downgrade() -> None:
    op.drop_table("ad_analysis_reference_ads")
    op.drop_constraint(
        "fk_ad_performance_analyses_generation_task_id_generation_tasks",
        "ad_performance_analyses",
        type_="foreignkey",
    )
    for constraint in (
        "uq_ad_performance_analyses_generation_task_id",
        "uq_ad_performance_analyses_external_request_id",
        "uq_ad_performance_analyses_analysis_id",
    ):
        op.drop_constraint(constraint, "ad_performance_analyses", type_="unique")
    for column in (
        "dispatch_error", "last_dispatched_at", "dispatch_claimed_at", "research_summary",
        "media_summary", "error_retryable", "error_code", "completed_at", "started_at",
        "generation_task_id", "max_attempts", "attempt_count", "result_schema_version",
        "analysis_scope", "progress", "stage", "normalized_payload", "payload_hash",
        "external_request_id", "analysis_id",
    ):
        op.drop_column("ad_performance_analyses", column)
```

Before accepting this revision, run `alembic heads` and confirm the only head is `20260713_0012`; if index-name autogeneration differs, use the explicit names created in `upgrade()` in `downgrade()`.

- [ ] **Step 5: Run persistence, migration-head, and lint checks**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ad_analysis_persistence.py -q
.venv\Scripts\python.exe -m alembic heads
.venv\Scripts\python.exe -m ruff check backend/app/db/models/ad_performance_analysis.py backend/app/db/models/ad_analysis_reference_ad.py backend/app/db/models/__init__.py backend/alembic/versions/20260713_0012_add_async_ad_analysis_jobs.py tests/test_ad_analysis_persistence.py
```

Expected: tests pass, Alembic prints `20260713_0012 (head)`, and Ruff passes.

- [ ] **Step 6: Commit only Task 2 files**

```powershell
git add backend/alembic/versions/20260713_0012_add_async_ad_analysis_jobs.py backend/app/db/models/ad_performance_analysis.py backend/app/db/models/ad_analysis_reference_ad.py backend/app/db/models/__init__.py tests/test_ad_analysis_persistence.py
git diff --cached --check
git commit -m "feat: persist async ad analysis jobs"
```

### Task 3: Transactional Job Creation and Database Idempotency

**Files:**
- Create: `backend/app/services/external_ad_performance_analysis_service.py`
- Modify: `backend/app/services/generation_task_service.py:133-273`
- Modify: `tests/test_generation_tasks.py`
- Create: `tests/test_external_ad_performance_jobs.py`

**Interfaces:**
- Consumes: Task 1 `ExternalAdPerformanceAnalysisCreate`, `canonicalize_ad_analysis_payload`, and `ad_analysis_payload_hash`; Task 2 async columns; existing `GenerationTaskService.create_task()`.
- Produces: Backward-compatible `GenerationTaskService.create_task(..., commit: bool = True) -> GenerationTask`; `AdAnalysisJobCreation`; `AdAnalysisIdempotencyConflict`; `ExternalAdPerformanceAnalysisService.create_job(session, payload) -> AdAnalysisJobCreation`; `ExternalAdPerformanceAnalysisService.get_job(session, analysis_id) -> AdPerformanceAnalysis`.

- [ ] **Step 1: Add failing tests for transaction ownership, replay, conflict, and rollback**

Append this transaction-ownership test to `tests/test_generation_tasks.py`:

```python
@pytest.mark.asyncio
async def test_generation_task_create_can_flush_without_committing(tmp_path):
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'no-commit.db').as_posix()}"
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        task = await GenerationTaskService().create_task(
            session,
            queue_name="ad_analysis_queue",
            task_type="ad_performance_analysis",
            business_type="ad_performance_analysis",
            business_id="analysis-record-1",
            payload={"analysis_record_id": "analysis-record-1"},
            commit=False,
        )
        assert task.id
        async with session_factory() as other_session:
            assert await other_session.get(GenerationTask, task.id) is None
        await session.commit()

    async with session_factory() as session:
        assert await session.get(GenerationTask, task.id) is not None
    await engine.dispose()
```

Create `tests/test_external_ad_performance_jobs.py` with these core cases:

```python
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.db.base import Base
from backend.app.db.models.ad_performance_analysis import AdPerformanceAnalysis
from backend.app.db.models.generation_task import GenerationTask
from backend.app.schemas.external_ad_performance_analysis import ExternalAdPerformanceAnalysisCreate
from backend.app.services.external_ad_performance_analysis_service import (
    AdAnalysisIdempotencyConflict,
    ExternalAdPerformanceAnalysisService,
)


def _request(external_request_id: str = "request-1", spend: str = "0.24"):
    return ExternalAdPerformanceAnalysisCreate.model_validate(
        {
            "external_request_id": external_request_id,
            "campaign": {"objective": "OUTCOME_TRAFFIC"},
            "adset": {"optimization_goal": "LINK_CLICKS"},
            "creative": {
                "creative_type": "image",
                "image_url": "https://newpixel.messrocts.com/uploads/a.jpg",
            },
            "insight": {"spend": spend},
        }
    )


@pytest.mark.asyncio
async def test_create_job_commits_analysis_and_generation_task_together(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'jobs.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        created = await ExternalAdPerformanceAnalysisService().create_job(session, _request())

    async with session_factory() as session:
        analysis = await session.scalar(
            select(AdPerformanceAnalysis).where(
                AdPerformanceAnalysis.analysis_id == created.analysis.analysis_id
            )
        )
        task = await session.get(GenerationTask, created.task.id)

    assert analysis is not None and task is not None
    assert analysis.generation_task_id == task.id
    assert task.business_id == analysis.id
    assert task.queue_name == "ad_analysis_queue"
    assert task.task_type == "ad_performance_analysis"
    assert created.idempotent_replay is False
    await engine.dispose()


@pytest.mark.asyncio
async def test_same_external_request_and_payload_returns_original_job(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'replay.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    service = ExternalAdPerformanceAnalysisService()
    async with session_factory() as session:
        first = await service.create_job(session, _request())
    async with session_factory() as session:
        replay = await service.create_job(session, _request())

    assert replay.analysis.analysis_id == first.analysis.analysis_id
    assert replay.task.id == first.task.id
    assert replay.idempotent_replay is True
    await engine.dispose()


@pytest.mark.asyncio
async def test_same_external_request_with_different_payload_raises_conflict(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'conflict.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    service = ExternalAdPerformanceAnalysisService()
    async with session_factory() as session:
        first = await service.create_job(session, _request())
    async with session_factory() as session:
        with pytest.raises(AdAnalysisIdempotencyConflict) as exc_info:
            await service.create_job(session, _request(spend="9.99"))

    assert exc_info.value.analysis_id == first.analysis.analysis_id
    await engine.dispose()
```

Add a fourth test with a fake `GenerationTaskService.create_task()` that raises after `session.flush()`, explicitly call `await session.rollback()`, and assert both `select(AdPerformanceAnalysis)` and `select(GenerationTask)` return empty lists. This verifies there is no analysis-only commit.

- [ ] **Step 2: Run focused tests and observe the missing service/signature failures**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_generation_tasks.py::test_generation_task_create_can_flush_without_committing tests/test_external_ad_performance_jobs.py -q
```

Expected: the new service import fails and `GenerationTaskService.create_task()` rejects `commit=False`.

- [ ] **Step 3: Make `GenerationTaskService.create_task()` transaction-owner aware**

Change the signature exactly to:

```python
async def create_task(
    self,
    session: AsyncSession,
    *,
    queue_name: str,
    task_type: str,
    business_type: str,
    business_id: str,
    payload: dict[str, Any],
    campaign_id: str | None = None,
    owner_user_id: str | None = None,
    priority: int = 0,
    max_attempts: int = 2,
    metadata: dict[str, Any] | None = None,
    commit: bool = True,
) -> GenerationTask:
```

Replace the unconditional commit/refresh at the creation point with:

```python
session.add(task)
if commit:
    await session.commit()
    await session.refresh(task)
else:
    await session.flush()
_set_generation_task_reused(task, False)
return task
```

Add `commit: bool` to `_mark_idempotency_reuse(...)` and use the same ownership rule:

```python
if commit:
    await session.commit()
    await session.refresh(task)
else:
    await session.flush()
_set_generation_task_reused(task, True)
```

Pass the caller's `commit` value from `create_task()` into `_mark_idempotency_reuse()`. Existing call sites omit the argument and retain `commit=True` behavior.

- [ ] **Step 4: Implement database-backed analysis idempotency and one-transaction creation**

Create `backend/app/services/external_ad_performance_analysis_service.py` with this public structure:

```python
from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.errors import AppError, NotFoundError
from backend.app.db.models.ad_performance_analysis import AdPerformanceAnalysis
from backend.app.db.models.generation_task import GenerationTask
from backend.app.schemas.external_ad_performance_analysis import (
    ExternalAdPerformanceAnalysisCreate,
    ad_analysis_payload_hash,
    canonicalize_ad_analysis_payload,
)
from backend.app.services.generation_task_service import GenerationTaskService

AD_ANALYSIS_QUEUE_NAME = "ad_analysis_queue"
AD_ANALYSIS_TASK_TYPE = "ad_performance_analysis"


@dataclass(frozen=True)
class AdAnalysisJobCreation:
    analysis: AdPerformanceAnalysis
    task: GenerationTask
    idempotent_replay: bool


class AdAnalysisIdempotencyConflict(AppError):
    def __init__(self, external_request_id: str, analysis_id: str) -> None:
        self.external_request_id = external_request_id
        self.analysis_id = analysis_id
        super().__init__("external_request_id already exists with a different payload")


class ExternalAdPerformanceAnalysisService:
    def __init__(self, task_service: GenerationTaskService | None = None) -> None:
        self.task_service = task_service or GenerationTaskService()

    async def create_job(
        self,
        session: AsyncSession,
        payload: ExternalAdPerformanceAnalysisCreate,
    ) -> AdAnalysisJobCreation:
        payload_hash = ad_analysis_payload_hash(payload)
        existing = await self._by_external_request_id(session, payload.external_request_id)
        if existing is not None:
            return await self._existing_job(session, existing, payload_hash)

        analysis = AdPerformanceAnalysis(
            analysis_id=f"ana_{uuid4().hex}",
            external_request_id=payload.external_request_id,
            payload_hash=payload_hash,
            request_payload=payload.model_dump(mode="json", exclude_none=False),
            normalized_payload=canonicalize_ad_analysis_payload(payload),
            source_type=payload.source_type,
            external_user_id=payload.external_user_id,
            status="queued",
            stage="queued",
            progress=0,
            max_attempts=2,
            metrics={},
            analysis_result={},
            media_summary={},
            research_summary={},
        )
        session.add(analysis)
        try:
            await session.flush()
            task = await self.task_service.create_task(
                session,
                queue_name=AD_ANALYSIS_QUEUE_NAME,
                task_type=AD_ANALYSIS_TASK_TYPE,
                business_type="ad_performance_analysis",
                business_id=analysis.id,
                payload={
                    "analysis_record_id": analysis.id,
                    "analysis_id": analysis.analysis_id,
                    "external_request_id": analysis.external_request_id,
                },
                max_attempts=analysis.max_attempts,
                metadata={"external_request_id": payload.external_request_id},
                commit=False,
            )
            analysis.generation_task_id = task.id
            await session.commit()
            await session.refresh(analysis)
            await session.refresh(task)
            return AdAnalysisJobCreation(analysis=analysis, task=task, idempotent_replay=False)
        except IntegrityError:
            await session.rollback()
            existing = await self._by_external_request_id(session, payload.external_request_id)
            if existing is None:
                raise
            return await self._existing_job(session, existing, payload_hash)
        except Exception:
            await session.rollback()
            raise

    async def get_job(self, session: AsyncSession, analysis_id: str) -> AdPerformanceAnalysis:
        analysis = await session.scalar(
            select(AdPerformanceAnalysis).where(AdPerformanceAnalysis.analysis_id == analysis_id)
        )
        if analysis is None:
            raise NotFoundError("analysis job not found")
        return analysis

    async def _by_external_request_id(
        self, session: AsyncSession, external_request_id: str
    ) -> AdPerformanceAnalysis | None:
        return await session.scalar(
            select(AdPerformanceAnalysis).where(
                AdPerformanceAnalysis.external_request_id == external_request_id
            )
        )

    async def _existing_job(
        self,
        session: AsyncSession,
        analysis: AdPerformanceAnalysis,
        payload_hash: str,
    ) -> AdAnalysisJobCreation:
        if analysis.payload_hash != payload_hash:
            raise AdAnalysisIdempotencyConflict(
                str(analysis.external_request_id), str(analysis.analysis_id)
            )
        if not analysis.generation_task_id:
            raise AppError("analysis job is missing its generation task")
        task = await session.get(GenerationTask, analysis.generation_task_id)
        if task is None:
            raise AppError("analysis generation task not found")
        return AdAnalysisJobCreation(analysis=analysis, task=task, idempotent_replay=True)
```

The transaction test must also pin the exact Task 13 identifier pair stored in the
generation-task payload:

```python
assert task.payload_json == {
    "analysis_record_id": analysis.id,
    "analysis_id": analysis.analysis_id,
    "external_request_id": analysis.external_request_id,
}
```

Populate the existing campaign/adset/creative/date summary columns from the validated payload using small private helpers copied from the current synchronous service's alias rules; do not call `create_analysis()` because it performs synchronous analysis and commits independently.

- [ ] **Step 5: Run focused and existing generation-task tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_external_ad_performance_jobs.py tests/test_generation_tasks.py -q
.venv\Scripts\python.exe -m ruff check backend/app/services/external_ad_performance_analysis_service.py backend/app/services/generation_task_service.py tests/test_external_ad_performance_jobs.py tests/test_generation_tasks.py
```

Expected: new transaction/idempotency tests pass, all existing generation-task tests remain green, and Ruff passes.

- [ ] **Step 6: Commit only Task 3 files**

```powershell
git add backend/app/services/external_ad_performance_analysis_service.py backend/app/services/generation_task_service.py tests/test_generation_tasks.py tests/test_external_ad_performance_jobs.py
git diff --cached --check
git commit -m "feat: create idempotent ad analysis jobs"
```

### Task 4: POST/GET Envelopes and Authentication Contracts

**Files:**
- Create: `backend/app/api/v1/endpoints/external_ad_performance_analysis.py`
- Modify: `backend/app/api/deps.py`
- Modify: `backend/app/api/v1/router.py`
- Create: `tests/test_external_ad_performance_api.py`

**Interfaces:**
- Consumes: Task 1 envelopes; Task 3 `create_job()` and `get_job()`; existing `schedule_generation_task(task, background_tasks)`.
- Produces: Authenticated `POST /integrations/ad-performance/analysis-jobs`; authenticated `GET /integrations/ad-performance/analysis-jobs/{analysis_id}`; structured validation/auth/conflict/not-found envelopes.

- [ ] **Step 1: Write failing API contract and synchronous-regression tests**

```python
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import get_settings
from backend.app.db.base import Base
from backend.app.db.session import get_session
from backend.app.main import create_app


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_ADS_ACCESS_TOKEN", "analysis-token")
    monkeypatch.setenv("GENERATION_TASK_EXECUTION_BACKEND", "celery")
    get_settings.cache_clear()
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'api.db').as_posix()}")

    async def prepare():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    import asyncio
    asyncio.run(prepare())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        yield client
    asyncio.run(engine.dispose())
    get_settings.cache_clear()


def _payload(spend="0.24"):
    return {
        "external_request_id": "api-request-1",
        "campaign": {"objective": "OUTCOME_TRAFFIC"},
        "adset": {"optimization_goal": "LINK_CLICKS"},
        "creative": {
            "creative_type": "image",
            "image_url": "https://newpixel.messrocts.com/uploads/a.jpg",
        },
        "insight": {"spend": spend},
    }


def test_create_replay_conflict_and_polling_contract(api_client, monkeypatch):
    enqueued = []
    monkeypatch.setattr(
        "backend.app.api.v1.endpoints.external_ad_performance_analysis.schedule_generation_task",
        lambda task, background_tasks=None: enqueued.append(task.id) or True,
    )
    headers = {"Authorization": "Bearer analysis-token"}

    created = api_client.post(
        "/api/v1/integrations/ad-performance/analysis-jobs",
        json=_payload(),
        headers=headers,
    )
    replay = api_client.post(
        "/api/v1/integrations/ad-performance/analysis-jobs",
        json=_payload(),
        headers=headers,
    )
    conflict = api_client.post(
        "/api/v1/integrations/ad-performance/analysis-jobs",
        json=_payload(spend="9.99"),
        headers=headers,
    )
    analysis_id = created.json()["data"]["analysis_id"]
    polled = api_client.get(
        f"/api/v1/integrations/ad-performance/analysis-jobs/{analysis_id}",
        headers=headers,
    )

    assert created.status_code == 202 and created.json()["code"] == 1001
    assert replay.status_code == 200 and replay.json()["code"] == 0
    assert replay.json()["data"]["analysis_id"] == analysis_id
    assert replay.json()["data"]["idempotent_replay"] is True
    assert conflict.status_code == 409 and conflict.json()["code"] == 4001
    assert conflict.json()["data"]["analysis_id"] == analysis_id
    assert polled.status_code == 200 and polled.json()["data"]["status"] == "queued"
    assert len(enqueued) == 1


def test_validation_auth_and_unknown_job_use_envelopes(api_client):
    no_auth = api_client.post(
        "/api/v1/integrations/ad-performance/analysis-jobs", json=_payload()
    )
    invalid = api_client.post(
        "/api/v1/integrations/ad-performance/analysis-jobs",
        json={**_payload(), "creative": {"creative_type": "image"}},
        headers={"Authorization": "Bearer analysis-token"},
    )
    missing = api_client.get(
        "/api/v1/integrations/ad-performance/analysis-jobs/ana_missing",
        headers={"Authorization": "Bearer analysis-token"},
    )

    assert no_auth.status_code == 401 and no_auth.json()["code"] == 4003
    assert invalid.status_code == 422 and invalid.json()["code"] == 4001
    assert missing.status_code == 404 and missing.json()["code"] == 4001


def test_existing_sync_route_keeps_http_201_and_unwrapped_body(api_client):
    payload = _payload()
    payload.pop("external_request_id")
    response = api_client.post(
        "/api/v1/integrations/ad-performance/analyses",
        json=payload,
        headers={"Authorization": "Bearer analysis-token"},
    )
    assert response.status_code == 201
    assert "analysis_id" in response.json()
    assert "code" not in response.json()
```

- [ ] **Step 2: Run API tests and confirm the new routes return 404**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_external_ad_performance_api.py -q
```

Expected: route assertions fail because `/analysis-jobs` is not registered.

- [ ] **Step 3: Add a reusable structured external-integration auth dependency**

In `backend/app/api/deps.py`, add a dependency that uses the same `AI_ADS_ACCESS_TOKEN` and comparison logic but returns the new envelope through `HTTPException.detail`:

```python
def require_external_integration_access_token(
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
        detail={"code": 4003, "message": "invalid or missing access token", "data": {}},
        headers={"WWW-Authenticate": "Bearer"},
    )
```

Do not alter `require_ai_ads_access_token()` because existing routers depend on its current error body.

- [ ] **Step 4: Implement the new router with local validation and HTTP-exception conversion**

Create `backend/app/api/v1/endpoints/external_ad_performance_analysis.py` with an `APIRoute` subclass that converts `RequestValidationError` to HTTP 422 `{code:4001,...}` and converts structured `HTTPException.detail` directly into an envelope. Implement response helpers with these exact mappings:

```python
def _create_data(analysis, *, replay: bool) -> dict[str, object]:
    return {
        "analysis_id": analysis.analysis_id,
        "external_request_id": analysis.external_request_id,
        "status": analysis.status,
        "stage": analysis.stage,
        "created_at": analysis.created_at.isoformat(),
        "poll_url": (
            "/api/v1/integrations/ad-performance/analysis-jobs/"
            f"{analysis.analysis_id}"
        ),
        "idempotent_replay": replay,
    }


def _job_data(analysis) -> dict[str, object]:
    error = None
    if analysis.error_code:
        error = {
            "error_code": analysis.error_code,
            "message": analysis.error_message or "analysis failed",
            "retryable": bool(analysis.error_retryable),
        }
    return {
        "analysis_id": analysis.analysis_id,
        "external_request_id": analysis.external_request_id,
        "status": analysis.status,
        "stage": analysis.stage,
        "progress": int(analysis.progress or 0),
        "created_at": analysis.created_at.isoformat(),
        "started_at": analysis.started_at.isoformat() if analysis.started_at else None,
        "completed_at": analysis.completed_at.isoformat() if analysis.completed_at else None,
        "result": analysis.analysis_result if analysis.status == "succeeded" else None,
        "error": error,
    }
```

The POST handler must:

1. call `create_job()`;
2. call `schedule_generation_task()` only when `idempotent_replay` is false;
3. return HTTP 202, `code=1001`, message `analysis job accepted` for a new record;
4. return HTTP 200, `code=0`, message `existing analysis job returned` for replay;
5. convert `AdAnalysisIdempotencyConflict` to HTTP 409 with `error_code=IDEMPOTENCY_CONFLICT` and the original `analysis_id`.

The GET handler must return HTTP 200 and `code=0` for every known lifecycle state and HTTP 404/`code=4001` for `NotFoundError`.

Register this router in `backend/app/api/v1/router.py` without adding it to the existing protected router block because the new router owns its structured auth dependency:

```python
from backend.app.api.v1.endpoints import external_ad_performance_analysis

api_router.include_router(
    external_ad_performance_analysis.router,
    tags=["external-ad-performance-analysis"],
)
```

- [ ] **Step 5: Run API, existing analysis, and auth regression tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_external_ad_performance_api.py tests/test_ad_performance_analysis.py -q
.venv\Scripts\python.exe -m ruff check backend/app/api/deps.py backend/app/api/v1/router.py backend/app/api/v1/endpoints/external_ad_performance_analysis.py tests/test_external_ad_performance_api.py
```

Expected: new envelope tests and the existing synchronous suite pass; Ruff passes.

- [ ] **Step 6: Commit only Task 4 files**

```powershell
git add backend/app/api/deps.py backend/app/api/v1/router.py backend/app/api/v1/endpoints/external_ad_performance_analysis.py tests/test_external_ad_performance_api.py
git diff --cached --check
git commit -m "feat: expose async ad analysis jobs"
```

### Task 5: Dedicated Queue Routing, Dispatch Accounting, and Atomic Recovery

**Files:**
- Create: `backend/app/services/ad_analysis_dispatch_service.py`
- Modify: `backend/app/services/generation_task_service.py:47-64,671-769,815-961`
- Modify: `backend/app/api/v1/endpoints/external_ad_performance_analysis.py`
- Modify: `backend/app/worker/tasks.py`
- Create: `tests/test_ad_analysis_dispatch.py`
- Modify: `tests/test_generation_tasks.py`

**Interfaces:**
- Consumes: Task 2 dispatch columns on `AdPerformanceAnalysis`; Task 3 `generation_task_id`; Task 4 POST handler; existing `schedule_generation_task_id()`.
- Produces: `AD_ANALYSIS_QUEUE_NAME = "ad_analysis_queue"`; `AD_ANALYSIS_TASK_TYPE = "ad_performance_analysis"`; `AdAnalysisDispatchClaim`; `AdAnalysisDispatchService.dispatch_committed_job(...) -> bool`; `AdAnalysisDispatchService.recover_stale(...) -> list[str]`; Celery task `ad_analysis.recover_stale`; dedicated `_run_ad_analysis_task(session, task) -> dict[str, Any]` routing hook.

- [ ] **Step 1: Write failing dispatch, exclusion, atomic-claim, and routing tests**

Create `tests/test_ad_analysis_dispatch.py` with a SQLite session fixture that creates `Base.metadata`, plus these cases:

```python
@pytest.mark.asyncio
async def test_publish_failure_is_accounted_without_changing_queued_state(session_factory):
    analysis, task = await _stored_job(session_factory, status="queued")

    def fail_publish(*args, **kwargs):
        raise RuntimeError("redis unavailable")

    service = AdAnalysisDispatchService(scheduler=fail_publish)
    async with session_factory() as session:
        stored = await session.get(AdPerformanceAnalysis, analysis.id)
        generation_task = await session.get(GenerationTask, task.id)
        published = await service.dispatch_committed_job(session, stored, generation_task)

    async with session_factory() as session:
        stored = await session.get(AdPerformanceAnalysis, analysis.id)
        generation_task = await session.get(GenerationTask, task.id)
        assert published is False
        assert stored.status == "queued"
        assert stored.dispatch_error == "redis unavailable"
        assert stored.last_dispatched_at is None
        assert generation_task.status == "queued"


@pytest.mark.asyncio
async def test_two_recovery_instances_publish_one_claim(session_factory):
    analysis, task = await _stored_job(
        session_factory,
        status="queued",
        task_queued_at=utcnow() - timedelta(minutes=20),
    )
    published: list[str] = []

    def record_publish(task_id, **kwargs):
        published.append(task_id)
        return True

    first = AdAnalysisDispatchService(scheduler=record_publish)
    second = AdAnalysisDispatchService(scheduler=record_publish)
    await asyncio.gather(
        first.recover_stale(session_factory, stale_after_seconds=600),
        second.recover_stale(session_factory, stale_after_seconds=600),
    )

    assert published == [task.id]
    async with session_factory() as session:
        stored = await session.get(AdPerformanceAnalysis, analysis.id)
        assert stored.dispatch_claimed_at is None
        assert stored.last_dispatched_at is not None
        assert stored.dispatch_error is None


@pytest.mark.asyncio
async def test_stale_running_task_is_requeued_only_when_attempt_remains(session_factory):
    analysis, task = await _stored_job(
        session_factory,
        status="processing",
        task_status="running",
        task_attempt_count=1,
        task_started_at=utcnow() - timedelta(minutes=20),
    )
    published: list[str] = []
    service = AdAnalysisDispatchService(
        scheduler=lambda task_id, **kwargs: published.append(task_id) or True
    )

    await service.recover_stale(session_factory, stale_after_seconds=600)

    async with session_factory() as session:
        stored_task = await session.get(GenerationTask, task.id)
        stored_analysis = await session.get(AdPerformanceAnalysis, analysis.id)
        assert stored_task.status == "queued"
        assert stored_task.attempt_count == 1
        assert stored_analysis.status == "queued"
        assert stored_analysis.stage == "queued"
    assert published == [task.id]
```

Append to `tests/test_generation_tasks.py`:

```python
@pytest.mark.asyncio
async def test_generic_recovery_excludes_ad_analysis_queue(task_session):
    regular = await _queued_task(task_session, queue_name="text_queue")
    ad_analysis = await _queued_task(task_session, queue_name="ad_analysis_queue")

    result = await GenerationTaskService().recover_interrupted_tasks(task_session)

    assert regular.id in result.rescheduled_task_ids
    assert ad_analysis.id not in result.rescheduled_task_ids


@pytest.mark.asyncio
async def test_ad_analysis_task_uses_dedicated_execution_hook(monkeypatch, task_session):
    task = await _queued_task(
        task_session,
        queue_name="ad_analysis_queue",
        task_type="ad_performance_analysis",
    )
    calls: list[str] = []

    async def fake_run(session, claimed_task):
        calls.append(claimed_task.id)
        return {"analysis_id": "ana_1", "status": "succeeded"}

    monkeypatch.setattr(generation_task_module, "_run_ad_analysis_task", fake_run)
    result = await GenerationTaskService()._run_task(task_session, task)

    assert result["status"] == "succeeded"
    assert calls == [task.id]


@pytest.mark.asyncio
async def test_prune_orphaned_business_tasks_never_deletes_ad_analysis_task(
    task_session,
):
    task = await _queued_task(
        task_session,
        queue_name="ad_analysis_queue",
        task_type="ad_performance_analysis",
        business_type="ad_performance_analysis",
        business_id="ana_keep_me",
    )

    deleted = await GenerationTaskService().prune_orphaned_business_tasks(task_session)

    assert deleted == 0
    assert await task_session.get(GenerationTask, task.id) is not None
```

- [ ] **Step 2: Run the focused tests and confirm the dispatch module and queue exclusions are absent**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ad_analysis_dispatch.py tests/test_generation_tasks.py::test_generic_recovery_excludes_ad_analysis_queue tests/test_generation_tasks.py::test_ad_analysis_task_uses_dedicated_execution_hook -q
```

Expected: collection fails for `backend.app.services.ad_analysis_dispatch_service`; after that import is temporarily skipped, generic recovery wrongly reschedules the ad-analysis task and orphan pruning wrongly deletes it.

- [ ] **Step 3: Exclude the dedicated queue from every generic recovery scan and add its execution hook**

In `generation_task_service.py`, add constants next to the existing queue constants:

```python
AD_ANALYSIS_QUEUE_NAME = "ad_analysis_queue"
AD_ANALYSIS_TASK_TYPE = "ad_performance_analysis"
AD_ANALYSIS_TASK_TYPES = {AD_ANALYSIS_TASK_TYPE}
```

Add this as the first executable branch of
`_generation_task_has_live_business_reference()`:

```python
if (
    task.queue_name == AD_ANALYSIS_QUEUE_NAME
    and task.task_type == AD_ANALYSIS_TASK_TYPE
    and task.business_type == AD_ANALYSIS_TASK_TYPE
):
    return True
```

Both `_queued_tasks()` and `_running_tasks()` must start their conditions with the queue exclusion:

```python
conditions = [
    GenerationTask.status == "queued",
    GenerationTask.queue_name != AD_ANALYSIS_QUEUE_NAME,
]
```

```python
conditions = [
    GenerationTask.status == "running",
    GenerationTask.queue_name != AD_ANALYSIS_QUEUE_NAME,
]
```

Do not change the ordering or recovery behavior of `text_queue`, `image_queue`, `video_queue`, or `callback_queue`. Add the dedicated route before the unsupported-task exception:

```python
if task.queue_name == AD_ANALYSIS_QUEUE_NAME and task.task_type in AD_ANALYSIS_TASK_TYPES:
    return await _run_ad_analysis_task(session, task)
```

Add the lazy hook at module scope so Task 5 does not create an import cycle and Task 13 can supply the orchestrator:

```python
async def _run_ad_analysis_task(
    session: AsyncSession,
    task: GenerationTask,
) -> dict[str, Any]:
    from backend.app.services.ad_analysis_orchestrator import AdAnalysisOrchestrator

    return await AdAnalysisOrchestrator().run(session, task)
```

The dedicated Celery worker supplies process-level queue isolation, so `process_task()` may call `_process_task_body()` directly for this queue; media, FFmpeg, research, and LLM concurrency gates are added inside their owning services in Tasks 9, 11, and 13.

- [ ] **Step 4: Implement dispatch accounting and the atomic stale claim**

Create `backend/app/services/ad_analysis_dispatch_service.py` with this public shape:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.db.base import utcnow
from backend.app.db.models.ad_performance_analysis import AdPerformanceAnalysis
from backend.app.db.models.generation_task import GenerationTask
from backend.app.services.generation_task_dispatcher import schedule_generation_task_id

AD_ANALYSIS_QUEUE_NAME = "ad_analysis_queue"


@dataclass(frozen=True)
class AdAnalysisDispatchClaim:
    analysis_record_id: str
    analysis_id: str
    task_id: str
    queue_name: str
    priority: int
    claimed_at: datetime


class AdAnalysisDispatchService:
    def __init__(self, scheduler: Callable[..., bool] = schedule_generation_task_id) -> None:
        self.scheduler = scheduler

    async def dispatch_committed_job(
        self,
        session: AsyncSession,
        analysis: AdPerformanceAnalysis,
        task: GenerationTask,
        *,
        background_tasks=None,
    ) -> bool:
        try:
            self.scheduler(
                task.id,
                queue_name=task.queue_name,
                background_tasks=background_tasks,
                priority=task.priority,
            )
        except Exception as exc:  # publication failure is recovered from PostgreSQL
            analysis.dispatch_error = str(exc)[:1000]
            analysis.dispatch_claimed_at = None
            await session.commit()
            return False
        analysis.last_dispatched_at = utcnow()
        analysis.dispatch_claimed_at = None
        analysis.dispatch_error = None
        await session.commit()
        return True

    async def recover_stale(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        stale_after_seconds: int,
        limit: int = 100,
    ) -> list[str]:
        now = utcnow()
        stale_before = now - timedelta(seconds=stale_after_seconds)
        async with session_factory() as session:
            claims = await self._claim_recoverable(
                session,
                now=now,
                stale_before=stale_before,
                limit=limit,
            )
            await session.commit()

        published: list[str] = []
        for claim in claims:
            try:
                self.scheduler(
                    claim.task_id,
                    queue_name=claim.queue_name,
                    priority=claim.priority,
                )
            except Exception as exc:
                await self._finish_claim(
                    session_factory,
                    claim,
                    published=False,
                    error=str(exc),
                )
            else:
                published.append(claim.task_id)
                await self._finish_claim(session_factory, claim, published=True, error=None)
        return published
```

Implement `_claim_recoverable()` as a two-step database claim, not an in-memory lock.
Candidate selection must verify the exact linked pair, business identifiers, matching
states, and queued dispatch age:

```python
candidate_rows = (
    await session.execute(
        select(AdPerformanceAnalysis, GenerationTask)
        .join(
            GenerationTask,
            AdPerformanceAnalysis.generation_task_id == GenerationTask.id,
        )
        .where(
            GenerationTask.queue_name == AD_ANALYSIS_QUEUE_NAME,
            GenerationTask.task_type == AD_ANALYSIS_TASK_TYPE,
            GenerationTask.business_type == AD_ANALYSIS_TASK_TYPE,
            GenerationTask.business_id == AdPerformanceAnalysis.id,
            or_(
                and_(
                    AdPerformanceAnalysis.status == "queued",
                    GenerationTask.status == "queued",
                    GenerationTask.queued_at <= stale_before,
                    or_(
                        AdPerformanceAnalysis.last_dispatched_at.is_(None),
                        AdPerformanceAnalysis.last_dispatched_at <= stale_before,
                    ),
                ),
                and_(
                    AdPerformanceAnalysis.status == "processing",
                    GenerationTask.status == "running",
                    GenerationTask.started_at.is_not(None),
                    GenerationTask.started_at <= stale_before,
                ),
            ),
        )
        .order_by(GenerationTask.priority.desc(), GenerationTask.queued_at.asc())
        .limit(limit)
    )
).all()
```

Rows whose `analysis.generation_task_id`, analysis status, task status, queue name,
task type, or business type do not form this exact pair are excluded.

1. Select at most `limit` joined exact-pair candidates.
2. For each candidate execute this claim and keep it only when `rowcount == 1`:

```python
claim_result = await session.execute(
    update(AdPerformanceAnalysis)
    .where(
        AdPerformanceAnalysis.id == analysis_id,
        AdPerformanceAnalysis.generation_task_id == task_id,
        AdPerformanceAnalysis.status.in_(("queued", "processing")),
        or_(
            AdPerformanceAnalysis.dispatch_claimed_at.is_(None),
            AdPerformanceAnalysis.dispatch_claimed_at <= stale_before,
        ),
    )
    .values(dispatch_claimed_at=now)
)
```

For a claimed stale-running task with `attempt_count < max_attempts`, use a
conditional atomic update and require `rowcount == 1`:

```python
task_update = await session.execute(
    update(GenerationTask)
    .where(
        GenerationTask.id == task.id,
        GenerationTask.queue_name == AD_ANALYSIS_QUEUE_NAME,
        GenerationTask.task_type == AD_ANALYSIS_TASK_TYPE,
        GenerationTask.business_type == AD_ANALYSIS_TASK_TYPE,
        GenerationTask.business_id == analysis.id,
        GenerationTask.status == "running",
        GenerationTask.started_at == task.started_at,
        GenerationTask.attempt_count == task.attempt_count,
        GenerationTask.attempt_count < GenerationTask.max_attempts,
    )
    .values(
        status="queued",
        queued_at=now,
        started_at=None,
        finished_at=None,
        duration_ms=None,
        error_code=None,
        error_message=None,
        retryable=False,
    )
)
if task_update.rowcount != 1:
    await session.execute(
        update(AdPerformanceAnalysis)
        .where(
            AdPerformanceAnalysis.id == analysis.id,
            AdPerformanceAnalysis.generation_task_id == task.id,
            AdPerformanceAnalysis.dispatch_claimed_at == now,
        )
        .values(dispatch_claimed_at=None)
    )
    continue
```

Only a winning task update may reset the analysis to `queued/queued/0` and publish.
If the update loses the race, clear only this analysis claim and do not publish. If
`attempt_count >= max_attempts`, conditionally mark the exact task/analysis pair
failed with `error_code="TASK_RETRY_EXHAUSTED"`, `error_retryable=False`, and do
not publish.

`_finish_claim()` must update only the exact pair and claim timestamp:

```python
finish_result = await session.execute(
    update(AdPerformanceAnalysis)
    .where(
        AdPerformanceAnalysis.id == claim.analysis_record_id,
        AdPerformanceAnalysis.analysis_id == claim.analysis_id,
        AdPerformanceAnalysis.generation_task_id == claim.task_id,
        AdPerformanceAnalysis.dispatch_claimed_at == claim.claimed_at,
    )
    .values(**values)
)
if finish_result.rowcount != 1:
    logger.info(
        "ad-analysis dispatch claim no longer owned",
        extra={"analysis_id": claim.analysis_id, "generation_task_id": claim.task_id},
    )
```

On success, `values` clears `dispatch_claimed_at`/`dispatch_error` and sets
`last_dispatched_at`; on failure it clears only the claim and stores the truncated
publication error. These predicates make concurrent Beat instances harmless.

- [ ] **Step 5: Use the accounted dispatcher from POST and expose the dedicated recovery task**

Replace Task 4's direct `schedule_generation_task()` call in the POST handler with:

```python
if not created.idempotent_replay:
    await AdAnalysisDispatchService().dispatch_committed_job(
        session,
        created.analysis,
        created.task,
        background_tasks=background_tasks,
    )
```

The response remains HTTP 202 even when publication fails because PostgreSQL contains a recoverable queued job. In `backend/app/worker/tasks.py`, add:

```python
@celery_app.task(name="ad_analysis.recover_stale", ignore_result=True)
def recover_stale_ad_analysis_tasks() -> None:
    settings = get_settings()
    _run_async(
        AdAnalysisDispatchService().recover_stale(
            AsyncSessionLocal,
            stale_after_seconds=settings.ad_analysis_stale_timeout_seconds,
        )
    )
```

Task 14 registers this task with Celery Beat after the setting exists.

- [ ] **Step 6: Run focused dispatch, API, worker, and generic queue regression checks**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ad_analysis_dispatch.py tests/test_external_ad_performance_api.py tests/test_generation_tasks.py tests/test_generation_task_dispatcher.py -q
.venv\Scripts\python.exe -m ruff check backend/app/services/ad_analysis_dispatch_service.py backend/app/services/generation_task_service.py backend/app/api/v1/endpoints/external_ad_performance_analysis.py backend/app/worker/tasks.py tests/test_ad_analysis_dispatch.py tests/test_generation_tasks.py
```

Expected: one atomic recovery publication, publication errors remain recoverable, all older queues retain their existing behavior, and Ruff passes.

- [ ] **Step 7: Commit only Task 5 files**

```powershell
git add backend/app/services/ad_analysis_dispatch_service.py backend/app/services/generation_task_service.py backend/app/api/v1/endpoints/external_ad_performance_analysis.py backend/app/worker/tasks.py tests/test_ad_analysis_dispatch.py tests/test_generation_tasks.py
git diff --cached --check
git commit -m "feat: isolate ad analysis dispatch and recovery"
```

### Task 6: Deterministic Facebook Metric and Funnel Engine

**Files:**
- Create: `backend/app/services/facebook_ad_metrics.py`
- Create: `tests/test_facebook_ad_metrics.py`

**Interfaces:**
- Consumes: Task 1 normalized payload dictionaries containing `campaign`, `adset`, `creative`, `insight`, `siblings`, and optional `account_currency`.
- Produces: `MetricDatum`; `FacebookRuleAnalysis`; `build_facebook_rule_analysis(payload: dict[str, Any]) -> FacebookRuleAnalysis`; deterministic rule version `facebook_rules_v1`.

- [ ] **Step 1: Write failing tests for the approved sample and Meta data semantics**

Create `tests/test_facebook_ad_metrics.py` with an embedded `_traffic_payload()` containing the approved values and these assertions:

```python
def test_approved_traffic_sample_has_deterministic_landing_page_verdict():
    result = build_facebook_rule_analysis(_traffic_payload())

    assert result.executive_summary == {
        "verdict": "optimize",
        "priority": "high",
        "primary_bottleneck": "landing_page",
        "confidence": "medium",
        "scale_eligibility": "not_ready",
        "pause_recommended": False,
        "key_findings": result.executive_summary["key_findings"],
    }
    metric = result.performance_funnel["landing_page"]["metrics"][
        "landing_page_view_rate"
    ]
    assert metric["value"] == pytest.approx(26.744186, rel=1e-6)
    assert metric["source"] == "calculated"
    assert metric["formula"] == "landing_page_views / inline_link_clicks * 100"
    assert metric["assessment"] == "critical"
    assert result.objective_alignment["meta_configuration"] == "aligned"
    assert result.objective_alignment["business_goal"] == "unknown"
    assert result.benchmark_comparison["submitted_siblings"]["status"] == (
        "insufficient_data"
    )


def test_missing_purchase_is_unavailable_but_explicit_zero_is_zero():
    missing = build_facebook_rule_analysis(_traffic_payload())
    zero_payload = _traffic_payload()
    zero_payload["insight"]["actions"].append(
        {"action_type": "purchase", "value": "0"}
    )
    explicit_zero = build_facebook_rule_analysis(zero_payload)

    assert missing.performance_funnel["conversion"]["metrics"]["purchase"] == {
        "value": None,
        "unit": "count",
        "source": "missing",
        "assessment": "unavailable",
        "reason": "purchase was not supplied",
    }
    assert explicit_zero.performance_funnel["conversion"]["metrics"]["purchase"] == {
        "value": 0.0,
        "unit": "count",
        "source": "meta_actions",
        "assessment": "zero",
    }


def test_zero_or_missing_denominator_returns_null_with_reason():
    payload = _traffic_payload()
    payload["insight"]["inline_link_clicks"] = "0"
    payload["insight"]["actions"] = [
        {"action_type": "landing_page_view", "value": "23"}
    ]
    result = build_facebook_rule_analysis(payload)
    metric = result.performance_funnel["landing_page"]["metrics"][
        "landing_page_view_rate"
    ]
    assert metric["value"] is None
    assert metric["assessment"] == "unavailable"
    assert metric["reason"] == "inline_link_clicks is zero or missing"


def test_currency_never_defaults_to_usd():
    no_currency = build_facebook_rule_analysis(_traffic_payload())
    with_currency_payload = _traffic_payload()
    with_currency_payload["account_currency"] = "EUR"
    with_currency = build_facebook_rule_analysis(with_currency_payload)

    assert no_currency.metrics["spend"]["currency"] is None
    assert with_currency.metrics["spend"]["currency"] == "EUR"
```

Add these concrete cases below the preceding tests:

```python
def _set_action(payload: dict, action_type: str, value: str) -> None:
    payload["insight"].setdefault("actions", []).append(
        {"action_type": action_type, "value": value}
    )


@pytest.mark.parametrize(
    ("objective", "optimization_goal", "action_type", "expected_metric"),
    [
        ("OUTCOME_SALES", "OFFSITE_CONVERSIONS", "purchase", "purchase"),
        ("OUTCOME_LEADS", "LEAD_GENERATION", "lead", "lead"),
    ],
)
def test_sales_and_lead_objectives_use_their_meta_action(
    objective, optimization_goal, action_type, expected_metric
):
    payload = _traffic_payload()
    payload["campaign"]["objective"] = objective
    payload["adset"]["optimization_goal"] = optimization_goal
    _set_action(payload, action_type, "4")

    result = build_facebook_rule_analysis(payload)

    assert result.objective_alignment["meta_configuration"] == "aligned"
    assert result.performance_funnel["conversion"]["metrics"][expected_metric]["value"] == 4.0


def test_video_retention_uses_video_plays_as_the_retention_denominator():
    payload = _traffic_payload()
    payload["creative"]["creative_type"] = "video"
    _set_action(payload, "video_view", "200")
    _set_action(payload, "video_p50_watched_actions", "50")

    result = build_facebook_rule_analysis(payload)

    p50 = result.performance_funnel["video"]["metrics"]["video_p50_rate"]
    assert p50["value"] == pytest.approx(25.0)
    assert p50["formula"] == "video_p50_views / video_plays * 100"


def test_clicks_and_inline_link_clicks_remain_distinct_meta_facts():
    payload = _traffic_payload()
    payload["insight"]["clicks"] = "120"
    payload["insight"]["inline_link_clicks"] = "40"

    result = build_facebook_rule_analysis(payload)

    assert result.metrics["clicks"]["value"] == 120.0
    assert result.metrics["inline_link_clicks"]["value"] == 40.0
    assert result.performance_funnel["landing_page"]["metrics"][
        "landing_page_view_rate"
    ]["value"] == pytest.approx(57.5)


def test_high_frequency_without_time_or_sibling_evidence_does_not_prove_fatigue():
    payload = _traffic_payload()
    payload["insight"]["frequency"] = "4.2"

    result = build_facebook_rule_analysis(payload)

    assert all(item["category"] != "creative_fatigue" for item in result.diagnoses)
    assert any("frequency alone" in warning.lower() for warning in result.warnings)


@pytest.mark.parametrize("sibling_impressions", [0, 19, 99])
def test_sibling_samples_below_one_hundred_impressions_are_insufficient(
    sibling_impressions,
):
    payload = _traffic_payload()
    payload["siblings"] = [
        {
            "facebook_ad_id": "sibling-1",
            "insight": {"impressions": str(sibling_impressions), "spend": "1"},
        },
        {
            "facebook_ad_id": "sibling-2",
            "insight": {"impressions": "0", "spend": "0"},
        },
    ]

    result = build_facebook_rule_analysis(payload)

    assert result.benchmark_comparison["submitted_siblings"]["status"] == "insufficient_data"


def test_traffic_link_clicks_is_meta_aligned_but_business_goal_stays_unknown():
    result = build_facebook_rule_analysis(_traffic_payload())

    assert result.objective_alignment["meta_configuration"] == "aligned"
    assert result.objective_alignment["business_goal"] == "unknown"


def test_all_numeric_outputs_are_finite():
    result = build_facebook_rule_analysis(_traffic_payload())

    def visit(value):
        if isinstance(value, dict):
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)
        elif isinstance(value, float):
            assert math.isfinite(value)

    visit(asdict(result))
```

- [ ] **Step 2: Run the metric tests and confirm the module is missing**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_facebook_ad_metrics.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'backend.app.services.facebook_ad_metrics'`.

- [ ] **Step 3: Implement source-aware metric values and action parsing**

Create immutable result containers:

```python
@dataclass(frozen=True)
class MetricDatum:
    value: float | None
    unit: str
    source: str
    assessment: str
    formula: str | None = None
    reason: str | None = None
    currency: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class FacebookRuleAnalysis:
    metrics: dict[str, dict[str, Any]]
    objective_alignment: dict[str, Any]
    performance_funnel: dict[str, dict[str, Any]]
    diagnoses: list[dict[str, Any]]
    executive_summary: dict[str, Any]
    benchmark_comparison: dict[str, Any]
    data_quality: dict[str, Any]
    warnings: list[str]
    meaningful: bool
    rule_engine_version: str = "facebook_rules_v1"
```

Parse all numeric input through `Decimal(str(value))`, reject non-finite values, and convert to rounded floats only at the output boundary. Parse these action aliases without using `or`, because an explicit zero is meaningful:

```python
ACTION_ALIASES = {
    "landing_page_views": ("landing_page_view", "omni_landing_page_view"),
    "purchase": ("purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase"),
    "add_to_cart": ("add_to_cart", "omni_add_to_cart"),
    "lead": ("lead", "onsite_conversion.lead_grouped"),
    "complete_registration": (
        "complete_registration",
        "offsite_conversion.fb_pixel_complete_registration",
    ),
}
```

`_action_metric()` must return `MetricDatum(None, ..., "missing", "unavailable", reason=...)` when no alias exists, and `MetricDatum(0, ..., "meta_actions", "zero")` when an alias is explicitly present with value zero.

- [ ] **Step 4: Implement objective-aware formulas, funnel states, and deterministic verdict rules**

Use percentage output for all rates and these formulas:

```python
DERIVED_RATE_FORMULAS = {
    "landing_page_view_rate": (
        "landing_page_views",
        "inline_link_clicks",
        "landing_page_views / inline_link_clicks * 100",
    ),
    "video_play_rate": ("video_plays", "impressions", "video_plays / impressions * 100"),
    "video_p25_rate": ("video_p25_views", "video_plays", "video_p25_views / video_plays * 100"),
    "video_p50_rate": ("video_p50_views", "video_plays", "video_p50_views / video_plays * 100"),
    "video_p75_rate": ("video_p75_views", "video_plays", "video_p75_views / video_plays * 100"),
    "video_p95_rate": ("video_p95_views", "video_plays", "video_p95_views / video_plays * 100"),
    "video_p100_rate": ("video_p100_views", "video_plays", "video_p100_views / video_plays * 100"),
}
```

Apply these v1 deterministic thresholds in order:

1. Fewer than 100 impressions or zero/missing spend makes confidence `low`, scale eligibility `not_ready`, and forbids `pause`.
2. At least 20 inline link clicks and landing-page-view rate below 50% makes `landing_page` critical. Below 35% produces a high-severity `landing_page_dropoff` diagnosis.
3. Link CTR at least 3% is `strong`; below 1% with at least 100 impressions is `weak`.
4. Frequency at least 3 with weakening sibling/time evidence may flag `creative_fatigue`; frequency alone never proves fatigue.
5. Missing purchases, value, or ROAS makes conversion profitability unavailable and cannot produce `scale` or `pause`.
6. `OUTCOME_TRAFFIC + LINK_CLICKS` is internally aligned. Business-goal alignment remains `unknown` unless the payload explicitly supplies a business goal.
7. Fewer than two sibling ads or total sibling impressions below 100 returns `submitted_siblings.status="insufficient_data"`; do not rank siblings.
8. The landing-page condition outranks a strong CTR, producing `optimize/landing_page/medium/not_ready/false` for the approved sample.

Every diagnosis must contain `diagnosis_id`, `category`, `severity`, `confidence`, `title`, `conclusion`, `evidence`, and `possible_causes`. Keep `possible_causes` phrased as checks, not confirmed facts.

- [ ] **Step 5: Run metric tests and lint**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_facebook_ad_metrics.py -q
.venv\Scripts\python.exe -m ruff check backend/app/services/facebook_ad_metrics.py tests/test_facebook_ad_metrics.py
```

Expected: the approved vector is stable, every objective/missing-data case passes, and Ruff passes.

- [ ] **Step 6: Commit only Task 6 files**

```powershell
git add backend/app/services/facebook_ad_metrics.py tests/test_facebook_ad_metrics.py
git diff --cached --check
git commit -m "feat: add deterministic facebook ad metrics"
```

### Task 7: Strict `facebook_ad_analysis_v1` Schema and Deterministic Assembly

**Files:**
- Create: `backend/app/schemas/facebook_ad_analysis.py`
- Create: `backend/app/services/facebook_ad_analysis_assembler.py`
- Create: `tests/test_facebook_ad_analysis_schema.py`

**Interfaces:**
- Consumes: Task 6 `FacebookRuleAnalysis`; Task 11 `PublicResearchResult` serialized data; Task 12 `FacebookAdLLMContribution`.
- Produces: strict `FacebookAdAnalysisV1`; strict `FacebookAdLLMContribution`; `assemble_facebook_ad_analysis(...) -> FacebookAdAnalysisV1`; `validate_no_public_performance_fabrication(result) -> None`.

- [ ] **Step 1: Write failing schema and rule-ownership tests**

Create `tests/test_facebook_ad_analysis_schema.py`:

```python
def test_result_rejects_unknown_top_level_fields(valid_result_dict):
    valid_result_dict["ai_analysis"] = {"duplicate": True}
    with pytest.raises(ValidationError, match="ai_analysis"):
        FacebookAdAnalysisV1.model_validate(valid_result_dict)


def test_assembler_keeps_rule_owned_verdict_metrics_and_sources(rule_analysis):
    malicious_llm = FacebookAdLLMContribution.model_validate(
        {
            "key_findings": ["Landing-page delivery needs verification."],
            "creative_analysis": _creative_analysis(),
            "audience_and_delivery_analysis": _audience_analysis(),
            "additional_diagnoses": [],
            "recommended_actions": [_action()],
            "experiment_plan": [_experiment()],
        }
    )
    result = assemble_facebook_ad_analysis(
        rule_analysis=rule_analysis,
        llm_contribution=malicious_llm,
        market_intelligence=_unavailable_market_intelligence(),
        media_analysis=_unavailable_media(),
        analysis_mode="rules_and_llm",
        analysis_scope="current_ad_only",
        model="test-model",
        warnings=[],
    )

    assert result.executive_summary.verdict == "optimize"
    assert result.executive_summary.primary_bottleneck == "landing_page"
    assert (
        result.performance_funnel.landing_page.metrics["landing_page_view_rate"].source
        == "calculated"
    )


@pytest.mark.parametrize("forbidden", ["ctr", "cpc", "cpa", "purchase", "revenue", "roas"])
def test_public_reference_cannot_contain_private_performance_fields(
    valid_result_dict, forbidden
):
    reference = valid_result_dict["market_intelligence"]["selected_reference_ads"][0]
    reference[forbidden] = 9.9
    with pytest.raises((ValidationError, ValueError)):
        FacebookAdAnalysisV1.model_validate(valid_result_dict)
```

Also assert every important inferred conclusion and every recommended action has non-empty evidence; `possible_causes` is a list of strings; public evidence is normally `public_proxy_signals` with `verified=false`; and serialization contains exactly the approved top-level keys.

- [ ] **Step 2: Run the schema tests and confirm both modules are missing**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_facebook_ad_analysis_schema.py -q
```

Expected: collection fails for `backend.app.schemas.facebook_ad_analysis`.

- [ ] **Step 3: Define the complete strict result and LLM-contribution model graph**

Use `ConfigDict(extra="forbid")` on every model. Define these exact enums with `Literal`:

```python
Verdict = Literal["scale", "keep", "watch", "optimize", "pause", "insufficient_data"]
Bottleneck = Literal[
    "delivery", "creative_hook", "click_quality", "landing_page", "conversion",
    "audience", "objective_mismatch", "creative_fatigue", "insufficient_data",
]
Confidence = Literal["low", "medium", "high"]
ScaleEligibility = Literal["ready", "conditional", "not_ready", "unknown"]
ExecutivePriority = Literal["critical", "high", "medium", "low"]
ExperimentPriority = Literal["high", "medium", "low"]
DiagnosisSeverity = Literal["critical", "high", "medium", "low"]
TargetDirection = Literal["increase", "decrease", "maintain", "observe"]
AnalysisScope = Literal[
    "current_ad_and_public_market_research",
    "current_ad_and_limited_market_research",
    "current_ad_only",
    "metrics_and_copy_only",
]
DataQualityStatus = Literal["good", "partial", "limited", "insufficient"]
FunnelStatus = Literal["strong", "normal", "weak", "critical", "unavailable", "not_applicable"]
ResearchStatus = Literal["succeeded", "insufficient_results", "unavailable", "skipped"]
AnalysisMode = Literal["rules_and_llm", "rules_only"]
EvidenceType = Literal[
    "submitted_performance", "historical_internal_performance", "verified_performance",
    "third_party_estimate", "public_proxy_signals", "unknown",
]
MetricSource = Literal[
    "meta", "meta_actions", "meta_action_values", "calculated", "inferred", "missing"
]
MetricAssessment = Literal[
    "strong", "normal", "weak", "critical", "zero", "unavailable", "not_applicable"
]
MetaConfigurationStatus = Literal["aligned", "misaligned", "unknown"]
BusinessGoalStatus = Literal["aligned", "misaligned", "unknown"]
ResearchSourceStatus = Literal["succeeded", "failed", "skipped"]
MediaAnalysisStatus = Literal["available", "degraded", "unavailable", "not_applicable"]
```

Use these aliases consistently rather than free-form `str`. In particular,
`RecommendedAction.priority` remains numeric (`int = Field(ge=1, le=10)`), while
`ExecutiveSummary.priority`, `ExperimentPlan.priority`, and `Diagnosis.severity`
use their string aliases.

Define strict models for:

- `MetricValue(value, unit, source, assessment, formula, reason, currency)`;
- `ExecutiveSummary(verdict, priority, primary_bottleneck, confidence, scale_eligibility, pause_recommended, key_findings)`;
- `ObjectiveAlignment(campaign_objective, optimization_goal, meta_configuration, business_goal, evidence)`;
- `FunnelSection(status, metrics, conclusion)` and `PerformanceFunnel(delivery, click, landing_page, conversion, video)`;
- `DiagnosisEvidence(metric, value, source)` and `Diagnosis(diagnosis_id, category, severity, confidence, title, conclusion, evidence, possible_causes)`;
- `CreativeAnalysis`, `AudienceAndDeliveryAnalysis` with conclusion/evidence fields;
- `ResearchProfile`, `ResearchSearchSummary`, `ResearchSource`, `ProxySignal`, `PerformanceEvidence`, `ReferenceCreativePatterns`, `SelectedReferenceAd`, and `MarketIntelligence`;
- `BenchmarkSection` and `BenchmarkComparison(submitted_siblings, historical_high_performers, web_researched_ads)`;
- `RecommendedAction` and `ExperimentPlan` using the fields from specification sections 12.9 and 12.10;
- `DataQuality(overall_status, score, sample_size, available_fields, missing_or_recommended_fields, warnings)`;
- `MediaAnalysisMetadata`, `PublicResearchMetadata`, and `AnalysisMetadata`;
- `FacebookAdAnalysisV1` with exactly the 14 top-level keys from section 12.2.

The public-reference model must reject fabricated metrics before normal validation:

```python
FORBIDDEN_PUBLIC_PERFORMANCE_KEYS = {
    "ctr", "cpc", "cpa", "purchases", "purchase", "revenue", "roas",
    "spend", "conversion_rate",
}


@model_validator(mode="before")
@classmethod
def reject_private_performance_fields(cls, value: Any) -> Any:
    if isinstance(value, dict):
        forbidden = FORBIDDEN_PUBLIC_PERFORMANCE_KEYS.intersection(value)
        if forbidden:
            raise ValueError(
                "public reference contains forbidden performance fields: "
                + ", ".join(sorted(forbidden))
            )
    return value
```

`PerformanceEvidence` must enforce `verified is False` when `type` is `public_proxy_signals` or `unknown`, and v1 validation must reject `third_party_estimate` in `market_intelligence.selected_reference_ads`.

Define `FacebookAdLLMContribution` as a deliberately narrower model that cannot carry rule-owned metrics, verdict, objective alignment, benchmark facts, research source facts, or analysis metadata:

```python
class FacebookAdLLMContribution(StrictModel):
    key_findings: list[str] = Field(min_length=1, max_length=8)
    creative_analysis: CreativeAnalysis
    audience_and_delivery_analysis: AudienceAndDeliveryAnalysis
    additional_diagnoses: list[Diagnosis] = Field(default_factory=list, max_length=8)
    recommended_actions: list[RecommendedAction] = Field(min_length=1, max_length=10)
    experiment_plan: list[ExperimentPlan] = Field(default_factory=list, max_length=6)
```

- [ ] **Step 4: Implement deterministic merging and final invariant validation**

Create `assemble_facebook_ad_analysis()` with this signature:

```python
def assemble_facebook_ad_analysis(
    *,
    rule_analysis: FacebookRuleAnalysis,
    llm_contribution: FacebookAdLLMContribution | None,
    market_intelligence: dict[str, Any],
    media_analysis: dict[str, Any],
    analysis_mode: AnalysisMode,
    analysis_scope: AnalysisScope,
    model: str | None,
    warnings: list[str],
    llm_error: str | None = None,
    generated_at: datetime | None = None,
) -> FacebookAdAnalysisV1:
```

Build `executive_summary`, `objective_alignment`, `performance_funnel`, rule diagnoses, sibling benchmark, and data quality only from `rule_analysis`. Append validated LLM diagnoses after rule diagnoses, deduplicated by `diagnosis_id`. In rules-only mode, generate concrete fallback creative/audience text, at least one evidence-backed recommended action from the primary deterministic diagnosis, and a directional experiment without an invented numeric threshold. Merge `key_findings` by preserving deterministic findings first and capping at eight.

Before returning, call:

```python
result = FacebookAdAnalysisV1.model_validate(assembled)
validate_no_public_performance_fabrication(result)
return result
```

`validate_no_public_performance_fabrication()` must recursively inspect only `market_intelligence.selected_reference_ads`, reject forbidden keys case-insensitively, require every selected reference to have `source_url`, `collected_at`, `similarity_score`, evidence type, confidence, and limitations, and reject `verified=true` for proxy evidence.

- [ ] **Step 5: Run schema/assembler tests and lint**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_facebook_ad_analysis_schema.py tests/test_facebook_ad_metrics.py -q
.venv\Scripts\python.exe -m ruff check backend/app/schemas/facebook_ad_analysis.py backend/app/services/facebook_ad_analysis_assembler.py tests/test_facebook_ad_analysis_schema.py
```

Expected: strict serialization, rule ownership, evidence requirements, and public-performance rejection all pass.

- [ ] **Step 6: Commit only Task 7 files**

```powershell
git add backend/app/schemas/facebook_ad_analysis.py backend/app/services/facebook_ad_analysis_assembler.py tests/test_facebook_ad_analysis_schema.py
git diff --cached --check
git commit -m "feat: define facebook ad analysis result schema"
```

### Task 8: SSRF-Safe Bounded Public HTTP Client

**Files:**
- Create: `backend/app/services/safe_public_http.py`
- Create: `tests/test_safe_public_http.py`

**Interfaces:**
- Consumes: Absolute public HTTP/HTTPS URLs, optional host allowlists, byte/time limits, injected DNS resolver and HTTPX transport for tests.
- Produces: `SafeHttpResponse`; `SafeTextResponse`; `SafeHttpError(code, message, retryable)`; `SafePublicHttpClient.fetch_bytes(...)`; `SafePublicHttpClient.fetch_text(...)`; `normalize_public_url(url) -> str`.

- [ ] **Step 1: Write failing SSRF, redirect, size, and content tests**

Create `tests/test_safe_public_http.py` with `httpx.MockTransport` and an injected async resolver:

```python
@pytest.mark.asyncio
async def test_client_disables_environment_proxy_and_accepts_public_https():
    seen = []

    async def handler(request):
        seen.append(request.url)
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"\xff\xd8ok")

    client = SafePublicHttpClient(
        transport=httpx.MockTransport(handler),
        resolver=lambda host: _resolved("93.184.216.34"),
    )
    response = await client.fetch_bytes(
        "https://media.example/a.jpg",
        allowed_hosts={"media.example"},
        max_bytes=1024,
        accepted_content_types={"image/jpeg"},
        accepted_magic=(b"\xff\xd8",),
        require_https=True,
    )
    assert response.content == b"\xff\xd8ok"
    assert seen == [httpx.URL("https://media.example/a.jpg")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.8", "169.254.169.254", "::1", "fc00::1", "224.0.0.1"],
)
async def test_dns_resolving_to_non_public_address_is_blocked(address):
    client = SafePublicHttpClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200)),
        resolver=lambda host: _resolved(address),
    )
    with pytest.raises(SafeHttpError, match="non-public address"):
        await client.fetch_text("https://example.test/page", max_bytes=1024)


@pytest.mark.asyncio
async def test_every_redirect_target_is_resolved_and_revalidated():
    async def handler(request):
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})
        return httpx.Response(200, content=b"secret")

    client = SafePublicHttpClient(
        transport=httpx.MockTransport(handler),
        resolver=lambda host: _resolved("93.184.216.34" if host == "public.example" else "127.0.0.1"),
    )
    with pytest.raises(SafeHttpError, match="non-public address"):
        await client.fetch_text("https://public.example/start", max_bytes=1024)


@pytest.mark.asyncio
async def test_stream_aborts_immediately_after_byte_limit():
    client = SafePublicHttpClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "video/mp4"},
                content=b"0" * 1025,
            )
        ),
        resolver=lambda host: _resolved("93.184.216.34"),
    )
    with pytest.raises(SafeHttpError) as exc_info:
        await client.fetch_bytes("https://media.example/a.mp4", max_bytes=1024)
    assert exc_info.value.code == "RESPONSE_TOO_LARGE"
    assert exc_info.value.retryable is False
```

Add these concrete tests to the same file:

```python
@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("https://user:pass@example.test/a", "URL_CREDENTIALS_FORBIDDEN"),
        ("ftp://example.test/a", "UNSUPPORTED_SCHEME"),
        ("file:///etc/passwd", "UNSUPPORTED_SCHEME"),
        ("https://example.test:8443/a", "PORT_NOT_ALLOWED"),
    ],
)
def test_normalize_public_url_rejects_user_info_scheme_and_port(url, code):
    with pytest.raises(SafeHttpError) as exc_info:
        normalize_public_url(url)
    assert exc_info.value.code == code


@pytest.mark.asyncio
async def test_host_allowlist_is_enforced_before_request():
    client = SafePublicHttpClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200)),
        resolver=lambda host: _resolved("93.184.216.34"),
    )
    with pytest.raises(SafeHttpError) as exc_info:
        await client.fetch_text(
            "https://other.example/page",
            allowed_hosts={"allowed.example"},
            max_bytes=1024,
        )
    assert exc_info.value.code == "HOST_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_redirect_loop_is_rejected():
    def handler(request):
        target = "/b" if request.url.path == "/a" else "/a"
        return httpx.Response(302, headers={"location": target})

    client = SafePublicHttpClient(
        transport=httpx.MockTransport(handler),
        resolver=lambda host: _resolved("93.184.216.34"),
    )
    with pytest.raises(SafeHttpError) as exc_info:
        await client.fetch_text("https://example.test/a", max_bytes=1024)
    assert exc_info.value.code == "REDIRECT_LOOP"


@pytest.mark.asyncio
async def test_more_than_three_redirects_is_rejected():
    def handler(request):
        index = int(request.url.path.removeprefix("/r") or "0")
        return httpx.Response(302, headers={"location": f"/r{index + 1}"})

    client = SafePublicHttpClient(
        transport=httpx.MockTransport(handler),
        resolver=lambda host: _resolved("93.184.216.34"),
    )
    with pytest.raises(SafeHttpError) as exc_info:
        await client.fetch_text("https://example.test/r0", max_bytes=1024)
    assert exc_info.value.code == "TOO_MANY_REDIRECTS"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "retryable"), [(429, True), (500, True), (503, True), (404, False)]
)
async def test_http_status_retryability_is_explicit(status, retryable):
    client = SafePublicHttpClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status)),
        resolver=lambda host: _resolved("93.184.216.34"),
    )
    with pytest.raises(SafeHttpError) as exc_info:
        await client.fetch_text("https://example.test/page", max_bytes=1024)
    assert exc_info.value.retryable is retryable


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content_type", "content", "accepted_types", "accepted_magic", "code"),
    [
        ("text/html", b"<html>login</html>", {"video/mp4"}, (b"\x00\x00",), "HTML_DISGUISED_AS_MEDIA"),
        ("video/mp4", b"", {"video/mp4"}, (b"\x00\x00",), "EMPTY_RESPONSE"),
        ("image/png", b"\xff\xd8jpeg", {"image/jpeg"}, (b"\xff\xd8",), "MIME_MISMATCH"),
        ("image/jpeg", b"not-jpeg", {"image/jpeg"}, (b"\xff\xd8",), "MAGIC_MISMATCH"),
    ],
)
async def test_media_body_mime_and_magic_must_agree(
    content_type, content, accepted_types, accepted_magic, code
):
    client = SafePublicHttpClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": content_type}, content=content
            )
        ),
        resolver=lambda host: _resolved("93.184.216.34"),
    )
    with pytest.raises(SafeHttpError) as exc_info:
        await client.fetch_bytes(
            "https://example.test/media",
            max_bytes=1024,
            accepted_content_types=accepted_types,
            accepted_magic=accepted_magic,
        )
    assert exc_info.value.code == code


@pytest.mark.asyncio
async def test_fetch_text_returns_decoded_safe_text_response():
    client = SafePublicHttpClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "application/json; charset=utf-8"},
                content=b'{"ok":true}',
            )
        ),
        resolver=lambda host: _resolved("93.184.216.34"),
    )
    response = await client.fetch_text("https://example.test/data", max_bytes=1024)
    assert isinstance(response, SafeTextResponse)
    assert response.text == '{"ok":true}'
```

- [ ] **Step 2: Run the safe-HTTP tests and confirm the module is missing**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_safe_public_http.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'backend.app.services.safe_public_http'`.

- [ ] **Step 3: Implement URL normalization, DNS checks, and manual redirects**

Define:

```python
@dataclass(frozen=True)
class SafeHttpResponse:
    final_url: str
    status_code: int
    content_type: str | None
    content: bytes
    headers: dict[str, str]


@dataclass(frozen=True)
class SafeTextResponse:
    final_url: str
    status_code: int
    content_type: str | None
    text: str
    headers: dict[str, str]


class SafeHttpError(AppError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(message)
```

`normalize_public_url()` must lowercase the scheme/host, strip fragments, remove default ports, preserve path/query, reject credentials, and reject ports outside 80/443. `_resolve_and_validate()` must resolve all A/AAAA results and require every address to satisfy `ipaddress.ip_address(address).is_global`; explicitly reject `169.254.169.254` and IPv6-mapped non-public addresses.

Build clients only this way:

```python
async with httpx.AsyncClient(
    transport=self._transport,
    trust_env=False,
    follow_redirects=False,
    timeout=httpx.Timeout(connect=5.0, read=read_timeout, write=10.0, pool=5.0),
) as client:
```

For each response with status 301, 302, 303, 307, or 308, resolve `Location` against the current URL, then re-run scheme, host, allowlist, port, and DNS validation before requesting it. Cap redirects at three and never copy inbound API authorization, cookies, or arbitrary caller headers.

- [ ] **Step 4: Implement bounded streaming and media/text validation**

Read with `client.stream()` and `response.aiter_bytes()`, incrementing a byte counter before appending each chunk. Raise `RESPONSE_TOO_LARGE` as soon as the counter exceeds `max_bytes`. Classify timeouts, transport errors, 429, and 5xx as retryable; classify 4xx other than 408/429 as non-retryable.

For `fetch_bytes()`, validate in this order: non-empty body, content length, response MIME (ignoring parameters), accepted MIME set, then one accepted magic prefix. For media calls, reject `text/html`, `application/json`, and bodies beginning with HTML/XML error markers even if the server lies about MIME. For `fetch_text()`, accept `text/*`, `application/json`, and `application/xhtml+xml`, decode from declared charset with UTF-8 fallback, and return the same response metadata plus decoded text in a dedicated `SafeTextResponse` dataclass.

- [ ] **Step 5: Run the safe-HTTP suite and lint**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_safe_public_http.py -q
.venv\Scripts\python.exe -m ruff check backend/app/services/safe_public_http.py tests/test_safe_public_http.py
```

Expected: all SSRF and bounded-download cases pass, no request follows an unvalidated redirect, and Ruff passes.

- [ ] **Step 6: Commit only Task 8 files**

```powershell
git add backend/app/services/safe_public_http.py tests/test_safe_public_http.py
git diff --cached --check
git commit -m "feat: add safe bounded public http client"
```

### Task 9: Private Media Download, Probe, Frame Extraction, and Cleanup

**Files:**
- Create: `backend/app/services/ad_analysis_media_service.py`
- Create: `tests/test_ad_analysis_media.py`

**Interfaces:**
- Consumes: Task 8 `SafePublicHttpClient.fetch_bytes()` and `SafeHttpError`; a validated `creative` object containing either `image_url` or `video_url`; private root `/data/ad-analysis-media/{analysis_id}/`.
- Produces: `MediaProcessingError`; `VideoProbe`; `MediaAnalysisBundle`; `sniff_video_container(header: bytes) -> str | None`; `build_keyframe_timestamps(duration_seconds: float) -> list[float]`; `AdAnalysisMediaService.prepare(analysis_id: str, creative: dict[str, Any]) -> MediaAnalysisBundle`; `AdAnalysisMediaService.cleanup(analysis_id: str) -> None`; `AdAnalysisMediaService.cleanup_orphans(older_than: timedelta) -> int`.

- [ ] **Step 1: Write failing container, duration, frame-boundary, degradation, and cleanup tests**

Create `tests/test_ad_analysis_media.py`:

```python
import json
from datetime import timedelta
from pathlib import Path

import pytest

from backend.app.services.ad_analysis_media_service import (
    AdAnalysisMediaService,
    MediaProcessingError,
    build_keyframe_timestamps,
    sniff_video_container,
)
from backend.app.services.safe_public_http import SafeHttpResponse


class FakeHttpClient:
    def __init__(self, content: bytes, content_type: str) -> None:
        self.content = content
        self.content_type = content_type

    async def fetch_bytes(self, url: str, **kwargs) -> SafeHttpResponse:
        return SafeHttpResponse(
            final_url=url,
            status_code=200,
            content_type=self.content_type,
            content=self.content,
            headers={},
        )


def test_video_container_sniffing_uses_ftyp_at_offset_four_and_webm_ebml():
    assert sniff_video_container(b"\x00\x00\x00\x18ftypisom") == "iso_bmff"
    assert sniff_video_container(b"\x1a\x45\xdf\xa3webm") == "webm"
    assert sniff_video_container(b"ftyp-not-at-offset-four") is None
    assert sniff_video_container(b"\x00\x00\x00\x18moov") is None


def test_keyframe_targets_are_clamped_deduplicated_and_cover_the_opening():
    assert build_keyframe_timestamps(2.4) == [0.2, 1.0, 2.0, 2.28]
    assert build_keyframe_timestamps(12.0) == [0.2, 1.0, 2.0, 3.0, 4.8, 7.2, 9.6, 11.4]


@pytest.mark.asyncio
async def test_valid_h264_video_generates_private_thumbnail_and_eight_frames(tmp_path):
    commands: list[list[str]] = []

    async def runner(command: list[str]) -> tuple[int, str, str]:
        commands.append(command)
        if command[0] == "ffprobe":
            return 0, json.dumps(
                {
                    "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
                    "streams": [
                        {
                            "codec_type": "video",
                            "codec_name": "h264",
                            "duration": "12.0",
                            "width": 1080,
                            "height": 1920,
                        }
                    ],
                }
            ), ""
        output = Path(command[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\xff\xd8frame")
        return 0, "", ""

    service = AdAnalysisMediaService(
        http_client=FakeHttpClient(b"\x00\x00\x00\x18ftypisom-video", "video/mp4"),
        root=tmp_path,
        command_runner=runner,
    )
    bundle = await service.prepare(
        "ana_video",
        {
            "creative_type": "video",
            "video_url": "https://newpixel.messrocts.com/uploads/ad.mp4",
        },
    )

    assert bundle.status == "available"
    assert bundle.source_path.parent == tmp_path / "ana_video"
    assert bundle.thumbnail_path is not None
    assert len(bundle.keyframe_paths) == 8
    assert bundle.visual_analysis_available is True
    assert bundle.probe.codec_name == "h264"
    assert all(path.is_file() for path in bundle.keyframe_paths)
    assert not str(bundle.source_path).startswith("/data/storage")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("duration", "content_size", "code"),
    [(20.01, 1024, "VIDEO_TOO_LONG"), (12.0, 100 * 1024 * 1024 + 1, "VIDEO_TOO_LARGE")],
)
async def test_video_limits_are_hard_failures(tmp_path, duration, content_size, code):
    async def runner(command: list[str]) -> tuple[int, str, str]:
        return 0, json.dumps(
            {
                "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
                "streams": [{"codec_type": "video", "codec_name": "h264", "duration": duration}],
            }
        ), ""

    service = AdAnalysisMediaService(
        http_client=FakeHttpClient(
            b"\x00\x00\x00\x18ftyp" + b"0" * max(content_size - 8, 0),
            "video/mp4",
        ),
        root=tmp_path,
        command_runner=runner,
    )
    with pytest.raises(MediaProcessingError) as exc_info:
        await service.prepare(
            "ana_limit",
            {"creative_type": "video", "video_url": "https://newpixel.messrocts.com/a.mp4"},
        )
    assert exc_info.value.code == code
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_two_frames_degrade_but_three_frames_enable_visual_analysis(tmp_path):
    for valid_count, expected in [(2, False), (3, True)]:
        emitted = 0

        async def runner(command: list[str]) -> tuple[int, str, str]:
            nonlocal emitted
            if command[0] == "ffprobe":
                return 0, json.dumps(
                    {
                        "format": {"format_name": "webm"},
                        "streams": [{"codec_type": "video", "codec_name": "vp9", "duration": "12"}],
                    }
                ), ""
            if "frame-" in command[-1] and emitted >= valid_count:
                return 1, "", "decode failed"
            Path(command[-1]).write_bytes(b"\xff\xd8frame")
            if "frame-" in command[-1]:
                emitted += 1
            return 0, "", ""

        service = AdAnalysisMediaService(
            http_client=FakeHttpClient(b"\x1a\x45\xdf\xa3video", "video/webm"),
            root=tmp_path,
            command_runner=runner,
        )
        bundle = await service.prepare(
            f"ana_{valid_count}",
            {"creative_type": "video", "video_url": "https://newpixel.messrocts.com/a.webm"},
        )
        assert bundle.visual_analysis_available is expected
        assert bundle.status == ("available" if expected else "degraded")


@pytest.mark.asyncio
async def test_cleanup_removes_only_the_analysis_directory_and_orphans(tmp_path):
    service = AdAnalysisMediaService(
        http_client=FakeHttpClient(b"\xff\xd8image", "image/jpeg"),
        root=tmp_path,
    )
    own = tmp_path / "ana_own"
    sibling = tmp_path / "ana_sibling"
    own.mkdir()
    sibling.mkdir()
    (own / "source.jpg").write_bytes(b"x")
    await service.cleanup("ana_own")
    assert not own.exists()
    assert sibling.exists()

    old = tmp_path / "ana_old"
    old.mkdir()
    service._set_mtime_for_test(old, age=timedelta(hours=3))
    assert await service.cleanup_orphans(older_than=timedelta(hours=2)) == 1
    assert not old.exists()
```

- [ ] **Step 2: Run the media tests and confirm the service is missing**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ad_analysis_media.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'backend.app.services.ad_analysis_media_service'`.

- [ ] **Step 3: Implement exact media types, safe path ownership, and container probing**

Define these public types and signatures:

```python
@dataclass(frozen=True)
class VideoProbe:
    container: str
    codec_name: str
    duration_seconds: float
    width: int | None
    height: int | None


@dataclass(frozen=True)
class MediaAnalysisBundle:
    media_type: Literal["image", "video"]
    source_field: Literal["creative.image_url", "creative.video_url"]
    source_path: Path
    thumbnail_path: Path | None
    keyframe_paths: tuple[Path, ...]
    keyframe_timestamps: tuple[float, ...]
    media_bytes: int
    status: Literal["available", "degraded"]
    visual_analysis_available: bool
    probe: VideoProbe | None
    warnings: tuple[str, ...]

    def llm_image_data_urls(self, *, max_images: int = 8) -> list[str]: ...
    def public_summary(self) -> dict[str, Any]: ...


class MediaProcessingError(AppError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(message)
```

Use these exact sniffing and timestamp rules:

```python
def sniff_video_container(header: bytes) -> str | None:
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "iso_bmff"
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    return None


def build_keyframe_timestamps(duration_seconds: float) -> list[float]:
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        return []
    upper = max(duration_seconds * 0.95, 0.0)
    targets = [0.2, 1.0, 2.0, 3.0, duration_seconds * 0.4,
               duration_seconds * 0.6, duration_seconds * 0.8, upper]
    return sorted({round(min(max(value, 0.0), upper), 3) for value in targets})
```

Resolve every analysis directory with `(root / analysis_id).resolve()`, require `candidate.parent == root.resolve()`, and reject analysis IDs containing path separators or not matching `^[A-Za-z0-9_-]{1,128}$`. Write downloads to `source.<ext>.part`, `flush()` and `os.fsync()`, then atomically rename to the final file. Never write below `local_storage_root` and never expose a public URL for any artifact.

- [ ] **Step 4: Implement bounded download, FFprobe validation, FFmpeg extraction, and degradation**

For images, accept JPEG, PNG, and WebP with matching MIME/magic, save one private source image, and use it as the thumbnail/visual input. For videos, call Task 8 with the 100 MB hard limit and accepted MIME values `video/mp4`, `video/quicktime`, and `video/webm`; then require `sniff_video_container()` to agree with FFprobe.

Run FFprobe without a shell:

```python
probe_command = [
    "ffprobe", "-v", "error", "-print_format", "json",
    "-show_format", "-show_streams", str(source_path),
]
```

Require exactly one usable video stream, finite duration `> 0` and `<= 20.0`, and an accepted FFprobe container (`mov,mp4,m4a,3gp,3g2,mj2` for ISO BMFF or `webm,matroska` for WebM). H.264 is preferred but HEVC, VP8, VP9, and AV1 are accepted with a warning. A missing video stream, damaged JSON, mismatched container, or unsupported codec raises a non-retryable `MediaProcessingError`.

Generate the thumbnail and each keyframe with bounded subprocess timeouts:

```python
def frame_command(source: Path, second: float, output: Path) -> list[str]:
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-ss", f"{second:.3f}", "-i", str(source), "-frames:v", "1",
        "-vf", "scale='min(1280,iw)':-2", "-q:v", "3", str(output),
    ]
```

Use the first valid frame as the internally generated thumbnail. Retry the FFmpeg extraction pass once only. Keep only non-empty JPEG files beginning with `FF D8`. At least three valid frames sets `visual_analysis_available=True`; 0–2 frames sets `status="degraded"`, returns the surviving artifacts, and adds a warning instead of failing the job. `llm_image_data_urls()` base64-encodes only the private thumbnail/keyframes in memory and never stores the data URLs in PostgreSQL.

Map Task 8 retryable network failures to retryable `MediaProcessingError`; domain, size, MIME, magic, duration, and codec failures are non-retryable. Retry transient media downloads at most three times with exponential delay plus jitter. Protect download and FFmpeg blocks with separate process-local semaphores whose initial limits are 4 and 2; Task 14 supplies configured limits.

- [ ] **Step 5: Implement exact-directory and orphan cleanup**

`cleanup(analysis_id)` must resolve and validate the child path before `shutil.rmtree()`. `cleanup_orphans(older_than)` iterates direct child directories only, skips symlinks, compares directory `st_mtime` to an aware UTC cutoff, revalidates each resolved child is below the configured root, and deletes only entries older than the cutoff. Cleanup exceptions are logged with `analysis_id` but are not converted into analysis failure.

- [ ] **Step 6: Run media, safe-HTTP, and lint checks**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ad_analysis_media.py tests/test_safe_public_http.py -q
.venv\Scripts\python.exe -m ruff check backend/app/services/ad_analysis_media_service.py tests/test_ad_analysis_media.py
```

Expected: MP4/MOV/WebM detection, 20-second and 100-MB boundaries, 2-versus-3-frame degradation, private-path checks, and cleanup all pass; Ruff passes.

- [ ] **Step 7: Commit only Task 9 files**

```powershell
git add backend/app/services/ad_analysis_media_service.py tests/test_ad_analysis_media.py
git diff --cached --check
git commit -m "feat: process ad analysis media privately"
```

### Task 10: Replaceable Public Research Provider Boundary

**Files:**
- Create: `backend/app/integrations/public_research/__init__.py`
- Create: `backend/app/integrations/public_research/base.py`
- Create: `backend/app/integrations/public_research/disabled_provider.py`
- Create: `backend/app/integrations/public_research/json_search_provider.py`
- Create: `backend/app/integrations/public_research/factory.py`
- Modify: `backend/app/services/safe_public_http.py`
- Create: `tests/test_public_research_providers.py`
- Modify: `tests/test_safe_public_http.py`

**Interfaces:**
- Consumes: Task 8 `SafePublicHttpClient` and `SafeTextResponse`; server settings for provider name, endpoint, API key, allowed/blocked domains, and page size/time limits.
- Produces: `SearchHit`; `PublicPageSnapshot`; `PublicResearchProvider` protocol; `DisabledPublicResearchProvider`; `JsonSearchPublicResearchProvider`; `get_public_research_provider(settings, http_client) -> PublicResearchProvider`.

- [ ] **Step 1: Write failing disabled, JSON-provider, secret-header, and page-fetch tests**

Create `tests/test_public_research_providers.py`:

```python
import json

import httpx
import pytest

from backend.app.core.config import Settings
from backend.app.integrations.public_research.disabled_provider import (
    DisabledPublicResearchProvider,
)
from backend.app.integrations.public_research.factory import get_public_research_provider
from backend.app.integrations.public_research.json_search_provider import (
    JsonSearchPublicResearchProvider,
)
from backend.app.services.safe_public_http import SafePublicHttpClient


def _resolver(host: str):
    async def resolve():
        return ["93.184.216.34"]
    return resolve()


@pytest.mark.asyncio
async def test_disabled_provider_is_explicit_and_returns_no_hits():
    provider = DisabledPublicResearchProvider(reason="provider not configured")
    assert provider.name == "disabled"
    assert provider.available is False
    assert await provider.search("facebook game ad", locale="en", country="US", limit=10) == []
    assert provider.unavailable_reason == "provider not configured"


@pytest.mark.asyncio
async def test_json_provider_normalizes_results_and_never_leaks_secret_into_query():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps(
                {
                    "results": [
                        {
                            "url": "https://www.facebook.com/ads/library/?id=123",
                            "title": "Survival game ad",
                            "snippet": "Beat the horde",
                            "source_type": "meta_ad_library",
                        }
                    ]
                }
            ).encode(),
        )

    safe_http = SafePublicHttpClient(
        transport=httpx.MockTransport(handler),
        resolver=_resolver,
    )
    provider = JsonSearchPublicResearchProvider(
        endpoint="https://search.example/v1/search",
        api_key="server-secret",
        http_client=safe_http,
    )
    hits = await provider.search("survival game facebook ad", locale="en", country="US", limit=5)

    assert hits[0].source_url.endswith("?id=123")
    assert hits[0].source_type == "meta_ad_library"
    assert requests[0].headers["X-API-Key"] == "server-secret"
    assert "server-secret" not in str(requests[0].url)
    assert "authorization" not in requests[0].headers


@pytest.mark.asyncio
async def test_public_page_fetch_uses_safe_client_and_preserves_collection_metadata():
    safe_http = SafePublicHttpClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                content=b"<html><title>Example Ad</title><body>PLAY NOW</body></html>",
            )
        ),
        resolver=_resolver,
    )
    provider = JsonSearchPublicResearchProvider(
        endpoint="https://search.example/v1/search",
        api_key=None,
        http_client=safe_http,
    )
    snapshot = await provider.fetch_public_page("https://brand.example/ad")
    assert snapshot.final_url == "https://brand.example/ad"
    assert snapshot.content_type.startswith("text/html")
    assert "PLAY NOW" in snapshot.text
    assert snapshot.collected_at.tzinfo is not None


def test_factory_degrades_when_enabled_provider_has_no_endpoint():
    settings = Settings(
        public_research_enabled=True,
        public_research_provider="json_search",
        public_research_json_search_url=None,
    )
    provider = get_public_research_provider(settings=settings, http_client=None)
    assert isinstance(provider, DisabledPublicResearchProvider)
    assert "URL" in provider.unavailable_reason
```

- [ ] **Step 2: Run the provider tests and confirm the package is missing**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_public_research_providers.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'backend.app.integrations.public_research'`.

- [ ] **Step 3: Define the provider protocol and immutable transport records**

In `base.py`, define:

```python
@dataclass(frozen=True)
class SearchHit:
    source_url: str
    title: str | None
    snippet: str | None
    source_type: str
    rank: int
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PublicPageSnapshot:
    requested_url: str
    final_url: str
    content_type: str | None
    text: str
    collected_at: datetime


class PublicResearchProvider(Protocol):
    name: str
    available: bool
    unavailable_reason: str | None

    async def search(
        self,
        query: str,
        *,
        locale: str | None,
        country: str | None,
        limit: int,
    ) -> list[SearchHit]: ...

    async def fetch_public_page(self, url: str) -> PublicPageSnapshot: ...
```

`DisabledPublicResearchProvider.search()` returns an empty list, while `fetch_public_page()` raises `PublicResearchUnavailable` with a non-secret reason. Export all public types from `__init__.py`.

- [ ] **Step 4: Implement the JSON search provider with safe outbound behavior**

`JsonSearchPublicResearchProvider.search()` sends a server-built JSON body, not caller-controlled headers:

```python
body = {
    "query": query,
    "locale": locale,
    "country": country,
    "limit": max(1, min(limit, 30)),
}
headers = {"Content-Type": "application/json"}
if self.api_key:
    headers["X-API-Key"] = self.api_key
response = await self.http_client.fetch_text(
    self.endpoint,
    method="POST",
    json_body=body,
    headers=headers,
    max_bytes=2 * 1024 * 1024,
    allowed_hosts={urlsplit(self.endpoint).hostname},
)
```

Task 8 must therefore expose optional fixed `method`, `json_body`, and server-owned `headers` arguments while continuing to strip caller authorization/cookies on redirects. Never log `api_key` or response headers. Accept response arrays under `results`, `items`, or `data.results`; discard entries without a valid public HTTP/HTTPS URL; normalize rank to the returned order; and cap output at `limit`.

`fetch_public_page()` uses `SafePublicHttpClient.fetch_text()` with the configured page timeout, maximum 2 MB, optional approved-domain allowlist, and the global blocked-domain check. It does not submit forms, execute JavaScript, send cookies, or follow any redirect that Task 8 rejects.

- [ ] **Step 5: Implement the non-failing provider factory**

Use this decision table:

```python
def get_public_research_provider(
    *,
    settings: Settings | None = None,
    http_client: SafePublicHttpClient | None = None,
) -> PublicResearchProvider:
    settings = settings or get_settings()
    if not settings.public_research_enabled:
        return DisabledPublicResearchProvider("public research disabled")
    if settings.public_research_provider == "json_search":
        if not settings.public_research_json_search_url:
            return DisabledPublicResearchProvider("PUBLIC_RESEARCH_JSON_SEARCH_URL is missing")
        return JsonSearchPublicResearchProvider(
            endpoint=settings.public_research_json_search_url,
            api_key=settings.public_research_json_search_api_key,
            http_client=http_client or SafePublicHttpClient(),
            allowed_domains=set(settings.public_research_allowed_domains),
            blocked_domains=set(settings.public_research_blocked_domains),
            page_timeout_seconds=settings.public_research_page_timeout_seconds,
        )
    return DisabledPublicResearchProvider(
        f"unsupported public research provider: {settings.public_research_provider}"
    )
```

Missing provider configuration must never prevent Backend, Beat, or Worker startup.

- [ ] **Step 6: Run provider, safe-HTTP, and lint checks**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_public_research_providers.py tests/test_safe_public_http.py -q
.venv\Scripts\python.exe -m ruff check backend/app/integrations/public_research tests/test_public_research_providers.py backend/app/services/safe_public_http.py
```

Expected: disabled mode is explicit, JSON results are normalized, API secrets stay server-side, all public-page requests use Task 8, and Ruff passes.

- [ ] **Step 7: Commit only Task 10 files plus the Task 8 extension used by the provider**

```powershell
git add backend/app/integrations/public_research backend/app/services/safe_public_http.py tests/test_public_research_providers.py tests/test_safe_public_http.py
git diff --cached --check
git commit -m "feat: add public research provider boundary"
```

### Task 11: Automatic Research Inference, Deduplication, and Deterministic Ranking

**Files:**
- Create: `backend/app/services/ad_analysis_research_service.py`
- Create: `tests/test_ad_analysis_research.py`

**Interfaces:**
- Consumes: Task 10 `PublicResearchProvider`, `SearchHit`, and `PublicPageSnapshot`; Task 8 `normalize_public_url()`; normalized request payload, optional Task 9 media summary, and server-owned research limits.
- Produces: immutable `PublicAdCandidate`; immutable `PublicResearchResult`; `infer_research_profile(payload, media_summary=None) -> dict[str, Any]`; `generate_research_queries(profile, *, maximum=12) -> list[str]`; `score_public_ad_similarity(profile, candidate) -> tuple[float, dict[str, float]]`; `AdAnalysisResearchService.research(payload, media_summary=None) -> PublicResearchResult`.

- [ ] **Step 1: Write failing profile, query, deduplication, ranking, deadline, and degradation tests**

Create `tests/test_ad_analysis_research.py` with deterministic fake providers and these core cases:

```python
import asyncio
from datetime import UTC, datetime

import pytest

from backend.app.integrations.public_research.base import PublicPageSnapshot, SearchHit
from backend.app.services.ad_analysis_research_service import (
    SIMILARITY_WEIGHTS,
    AdAnalysisResearchService,
    generate_research_queries,
    infer_research_profile,
    score_public_ad_similarity,
)


class FakeResearchProvider:
    name = "fake_public_search"
    available = True
    unavailable_reason = None

    def __init__(self, hits_by_query=None, pages=None, failures=None):
        self.hits_by_query = hits_by_query or {}
        self.pages = pages or {}
        self.failures = failures or {}
        self.search_calls: list[str] = []

    async def search(self, query, *, locale, country, limit):
        self.search_calls.append(query)
        failure = self.failures.get(query)
        if failure:
            raise failure
        return self.hits_by_query.get(query, [])[:limit]

    async def fetch_public_page(self, url):
        return self.pages.get(
            url,
            PublicPageSnapshot(
                requested_url=url,
                final_url=url,
                content_type="text/html",
                text="",
                collected_at=datetime(2026, 7, 13, tzinfo=UTC),
            ),
        )


def _payload():
    return {
        "campaign": {"name": "Slash the Hordes US", "objective": "OUTCOME_TRAFFIC"},
        "adset": {
            "optimization_goal": "LINK_CLICKS",
            "targeting": {"geo_locations": {"countries": ["US"]}},
        },
        "creative": {
            "creative_type": "video",
            "primary_text": "Can you survive the next wave? Beat my record.",
            "headline": "Slash the Hordes",
            "cta": "PLAY_NOW",
            "link": "https://game.example/play",
        },
        "insight": {},
        "siblings": [],
    }


def test_profile_is_automatic_and_sparse_input_uses_broad_fallbacks():
    rich = infer_research_profile(_payload())
    sparse = infer_research_profile(
        {"campaign": {}, "adset": {}, "creative": {"creative_type": "image"}}
    )

    assert rich["generation_method"] == "automatically_inferred"
    assert rich["country"] == "US"
    assert rich["language"] == "en"
    assert rich["product_name"] == "Slash the Hordes"
    assert sparse["product_category"] == "consumer product or service"
    assert sparse["country"] is None and sparse["language"] is None
    assert any("broad" in warning.lower() for warning in sparse["warnings"])


def test_queries_are_deterministic_bounded_and_cover_fallback_intents():
    profile = infer_research_profile(_payload())
    first = generate_research_queries(profile, maximum=12)
    second = generate_research_queries(profile, maximum=12)

    assert first == second
    assert 8 <= len(first) <= 12
    assert len(first) == len(set(first))
    assert any("Facebook ad" in query for query in first)
    assert any("US" in query for query in first)
    assert any("survival" in query.lower() for query in first)


def test_similarity_weights_are_pinned_and_not_llm_owned():
    assert SIMILARITY_WEIGHTS == {
        "product_or_industry": 0.25,
        "country": 0.10,
        "language": 0.10,
        "creative_type": 0.10,
        "campaign_objective": 0.10,
        "message_angle": 0.15,
        "hook": 0.08,
        "cta": 0.06,
        "landing_page_goal": 0.06,
    }
    assert sum(SIMILARITY_WEIGHTS.values()) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_duplicates_collapse_and_ranking_is_stable():
    query = generate_research_queries(infer_research_profile(_payload()))[0]
    duplicate_hits = [
        SearchHit(
            source_url="https://www.facebook.com/ads/library/?id=123&utm_source=x",
            title="Slash the Hordes",
            snippet="Can you survive the next wave? PLAY NOW",
            source_type="meta_ad_library",
            rank=1,
            metadata={"ad_library_id": "123", "country": "US", "language": "en"},
        ),
        SearchHit(
            source_url="https://facebook.com/ads/library/?id=123",
            title="Slash the Hordes",
            snippet="Can you survive the next wave? PLAY NOW",
            source_type="meta_ad_library",
            rank=2,
            metadata={"ad_library_id": "123", "country": "US", "language": "en"},
        ),
    ]
    provider = FakeResearchProvider(hits_by_query={query: duplicate_hits})
    result = await AdAnalysisResearchService(
        provider=provider,
        maximum_queries=8,
        maximum_candidates=30,
        maximum_selected=5,
    ).research(_payload())

    assert result.status == "insufficient_results"
    assert len(result.candidates) == 1
    assert len(result.selected) == 1
    assert result.selected[0].ad_library_id == "123"
    assert result.selected[0].performance_evidence["type"] == "public_proxy_signals"
    assert result.selected[0].performance_evidence["verified"] is False
    assert result.selected[0].similarity_dimensions


@pytest.mark.asyncio
async def test_limits_deadline_and_transient_retry_are_enforced(monkeypatch):
    active = 0
    maximum_active = 0
    attempts: dict[str, int] = {}

    class BoundedProvider(FakeResearchProvider):
        async def search(self, query, *, locale, country, limit):
            nonlocal active, maximum_active
            attempts[query] = attempts.get(query, 0) + 1
            if attempts[query] == 1:
                error = RuntimeError("HTTP 503")
                error.retryable = True
                raise error
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return []

    result = await AdAnalysisResearchService(
        provider=BoundedProvider(),
        research_concurrency=3,
        total_timeout_seconds=1,
        maximum_queries=12,
    ).research(_payload())

    assert maximum_active <= 3
    assert all(count <= 3 for count in attempts.values())  # initial call plus two retries
    assert result.status == "insufficient_results"
    assert result.completed_at >= result.started_at


@pytest.mark.asyncio
async def test_missing_provider_is_unavailable_but_partial_hits_are_insufficient():
    unavailable = FakeResearchProvider()
    unavailable.available = False
    unavailable.unavailable_reason = "provider not configured"
    missing = await AdAnalysisResearchService(provider=unavailable).research(_payload())
    assert missing.status == "unavailable"
    assert "provider not configured" in missing.warnings

    query = generate_research_queries(infer_research_profile(_payload()))[0]
    partial = await AdAnalysisResearchService(
        provider=FakeResearchProvider(
            hits_by_query={
                query: [
                    SearchHit(
                        source_url="https://brand.example/ad",
                        title="Survival challenge",
                        snippet="Beat the wave",
                        source_type="web_search",
                        rank=1,
                        metadata={},
                    )
                ]
            }
        )
    ).research(_payload())
    assert partial.status == "insufficient_results"
    assert partial.market_intelligence()["search_summary"]["selected_reference_count"] == 1
    assert len(partial.reference_rows()) == 1
```

- [ ] **Step 2: Run the research tests and confirm the service is missing**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ad_analysis_research.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'backend.app.services.ad_analysis_research_service'`.

- [ ] **Step 3: Define immutable research records and automatic inference**

Create these public records in `backend/app/services/ad_analysis_research_service.py`:

```python
ResearchResultStatus = Literal["succeeded", "insufficient_results", "unavailable", "skipped"]

SIMILARITY_WEIGHTS: Final[dict[str, float]] = {
    "product_or_industry": 0.25,
    "country": 0.10,
    "language": 0.10,
    "creative_type": 0.10,
    "campaign_objective": 0.10,
    "message_angle": 0.15,
    "hook": 0.08,
    "cta": 0.06,
    "landing_page_goal": 0.06,
}


@dataclass(frozen=True)
class PublicAdCandidate:
    reference_id: str
    source_type: str
    source_url: str
    source_domain: str
    advertiser_name: str | None
    ad_library_id: str | None
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    is_active: bool | None
    countries: tuple[str, ...]
    languages: tuple[str, ...]
    creative_type: str | None
    campaign_objective: str | None
    primary_text: str | None
    headline: str | None
    hook: str | None
    message_angles: tuple[str, ...]
    cta: str | None
    landing_page_url: str | None
    landing_page_goal: str | None
    image_urls: tuple[str, ...]
    video_url: str | None
    collected_at: datetime
    content_hash: str
    similarity_score: float
    similarity_dimensions: dict[str, float]
    performance_evidence: dict[str, Any]
    creative_patterns: dict[str, Any]
    applicable_learnings: tuple[str, ...]
    raw_excerpt: dict[str, Any]


@dataclass(frozen=True)
class PublicResearchResult:
    status: ResearchResultStatus
    provider: str
    research_profile: dict[str, Any]
    queries: tuple[str, ...]
    candidates: tuple[PublicAdCandidate, ...]
    selected: tuple[PublicAdCandidate, ...]
    sources: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    started_at: datetime
    completed_at: datetime

    def market_intelligence(self) -> dict[str, Any]: ...
    def reference_rows(self) -> list[dict[str, Any]]: ...
    def public_summary(self) -> dict[str, Any]: ...
```

`infer_research_profile()` must inspect campaign/ad-set/creative text, CTA, objective, optimization goal, country targeting, media summary, landing-page host/path, and repeated sibling wording. Every inferred field is represented as a value plus confidence internally, while `market_intelligence()` emits the Task 7 `ResearchProfile` shape with `generation_method="automatically_inferred"`, a top-level profile confidence, and warnings. Never accept caller `research_context`, `search_keywords`, or competitor fields.

If inference cannot identify a product, use `product_category="consumer product or service"`; if country or language is unknown, leave it `None` and generate broad category queries. `generate_research_queries()` must deterministically produce 8–12 unique normalized queries, ordered as exact product/brand, category, message angle, user motivation, similar product, country/language, and Facebook/Meta advertising intent variants. Truncate each query to 180 characters and never exceed the server-owned `maximum`.

- [ ] **Step 4: Normalize, deduplicate, and score without delegating ranking to the LLM**

Normalize `SearchHit` and fetched-page data into `PublicAdCandidate`. Unknown public fields remain `None`; never infer dates, activity, countries, metrics, or advertiser identity without observable evidence. Derive `content_hash` from normalized advertiser/text/headline/CTA/landing-page identity.

Deduplicate in this order while retaining the richest and highest-ranked record:

1. `normalize_public_url(source_url)` with tracking parameters removed;
2. Meta Ad Library ID;
3. content hash or media/content perceptual hash when available;
4. normalized advertiser plus primary-text identity;
5. normalized landing-page URL.

Implement `score_public_ad_similarity()` with the pinned weights above. Each dimension score is deterministic in `{0.0, 0.5, 1.0}` for no match, partial/unknown-compatible match, or exact/strong token match. The final score is the rounded weighted sum in `[0, 1]`; save both the total and all nine dimension contributions. The LLM may later explain selected creatives, but it must never choose candidates or modify these scores.

Sort by `(-similarity_score, source_url, content_hash)`. Keep at most 30 normalized candidates, at most 10 candidates meeting the configured high-relevance threshold (default `0.55`), and at most 5 final references. A result with at least 3 selected references is `succeeded`; 1–2 usable references is `insufficient_results`; zero usable references is `insufficient_results` unless the provider itself was unavailable.

For every selected public reference emit:

```python
performance_evidence = {
    "type": "public_proxy_signals" if observable_signals else "unknown",
    "verified": False,
    "confidence": proxy_confidence,
    "signals": observable_signals,
    "limitations": [
        "真实CTR、CPC和CPA不可用",
        "真实购买量、收入和ROAS不可用",
        "公开活跃时间或素材变体不能证明盈利",
    ],
}
```

`market_intelligence()` must serialize directly into Task 7 `MarketIntelligence`. `reference_rows()` must map every selected item directly to Task 2 fields: `reference_id`, source facts, timestamps, `content_hash`, `similarity_score`, `performance_evidence_json`, `creative_analysis_json`, and `raw_excerpt_json`.

- [ ] **Step 5: Enforce concurrency, retry, and the total research deadline**

Construct `AdAnalysisResearchService` with explicit injectable limits:

```python
class AdAnalysisResearchService:
    def __init__(
        self,
        *,
        provider: PublicResearchProvider | None = None,
        research_concurrency: int = 3,
        total_timeout_seconds: float = 90.0,
        page_timeout_seconds: float = 10.0,
        maximum_queries: int = 12,
        maximum_candidates: int = 30,
        maximum_selected: int = 5,
        high_relevance_threshold: float = 0.55,
    ) -> None: ...

    async def research(
        self,
        payload: dict[str, Any],
        media_summary: dict[str, Any] | None = None,
    ) -> PublicResearchResult: ...
```

Create a process-local research semaphore with initial limit `3`, replaced only when configuration changes. Wrap the complete query/search/fetch/rank operation in `asyncio.timeout(total_timeout_seconds)` and calculate a monotonic deadline so retries and page fetches cannot extend the 90-second budget. Retry timeouts, transport errors, HTTP 429, and 5xx up to two times after the initial call with bounded exponential backoff and jitter. Do not retry permanent 4xx, invalid URLs, blocked pages, authentication walls, or parsing failures.

Provider unavailable or deadline expiry with no usable candidate returns `unavailable`; partial usable candidates return `insufficient_results`; one failed source must not discard candidates from another source. Keep secrets, full response bodies, and inbound Bearer values out of logs.

- [ ] **Step 6: Run research, provider, safe-HTTP, and lint checks**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ad_analysis_research.py tests/test_public_research_providers.py tests/test_safe_public_http.py -q
.venv\Scripts\python.exe -m ruff check backend/app/services/ad_analysis_research_service.py tests/test_ad_analysis_research.py
```

Expected: automatic fallbacks, deterministic 8–12 queries, all five deduplication identities, pinned weight sum, 30/10/5 bounds, concurrency `3`, retries, 90-second deadline, and unavailable/limited states pass; Ruff passes.

- [ ] **Step 7: Commit only Task 11 files**

```powershell
git add backend/app/services/ad_analysis_research_service.py tests/test_ad_analysis_research.py
git diff --cached --check
git commit -m "feat: infer and rank public ad research"
```

### Task 12: Facebook-Specific Strict LLM Contribution and Repair

**Files:**
- Modify: `backend/app/integrations/llm/base.py`
- Modify: `backend/app/integrations/llm/openai_provider.py`
- Modify: `backend/app/integrations/llm/responses_provider.py`
- Modify: `backend/app/integrations/llm/mock_provider.py`
- Create: `backend/app/services/facebook_ad_analysis_prompt.py`
- Create: `tests/test_facebook_ad_analysis_llm.py`

**Interfaces:**
- Consumes: Task 7 `FacebookAdLLMContribution`; current normalized payload, deterministic rule result, Task 9 private visual data URLs, and Task 11 selected public references.
- Produces: `LLMProvider.analyze_facebook_ad(context, *, repair_errors=None) -> dict[str, Any]`; `FacebookAdLLMFailure(code, message, retryable, degradable)`; `build_facebook_ad_analysis_system_prompt(repair_errors=None) -> str`; `build_facebook_ad_analysis_user_content(context) -> dict[str, Any]`; `run_facebook_ad_llm_request(completion, context, repair_errors=None) -> dict[str, Any]`.

- [ ] **Step 1: Write failing protocol, prompt, retry, repair, timeout, and evidence tests**

Create `tests/test_facebook_ad_analysis_llm.py`:

```python
import httpx
import pytest

from backend.app.integrations.llm.mock_provider import MockLLMProvider
from backend.app.integrations.llm.openai_provider import OpenAILLMProvider
from backend.app.integrations.llm.responses_provider import GatewayResponsesLLMProvider
from backend.app.services.facebook_ad_analysis_prompt import (
    FacebookAdLLMFailure,
    build_facebook_ad_analysis_system_prompt,
)


def _context():
    return {
        "rule_analysis": {
            "executive_summary": {
                "verdict": "optimize",
                "primary_bottleneck": "landing_page",
                "confidence": "medium",
            },
            "performance_funnel": {
                "landing_page": {
                    "metrics": {
                        "landing_page_view_rate": {
                            "value": 26.744186,
                            "source": "calculated",
                        }
                    }
                }
            },
        },
        "creative": {"primary_text": "Can you survive?", "cta": "PLAY_NOW"},
        "media": {"visual_analysis_available": False, "images": []},
        "market_intelligence": {"status": "unavailable", "selected_reference_ads": []},
        "data_quality": {"sample_size": {"impressions": 1079, "spend": 0.24}},
    }


def _valid_contribution():
    return {
        "key_findings": ["点击到落地页之间存在明显流失。"],
        "creative_analysis": {
            "conclusion": "文案使用挑战式钩子。",
            "confidence": "medium",
            "evidence": ["creative.primary_text=Can you survive?"],
        },
        "audience_and_delivery_analysis": {
            "conclusion": "现有数据不足以确认受众问题。",
            "confidence": "low",
            "evidence": ["缺少受众分层成效数据"],
        },
        "additional_diagnoses": [],
        "recommended_actions": [
            {
                "action_id": "check_landing_page",
                "priority": 1,
                "category": "landing_page",
                "action": "检查目标地区移动端首屏加载和跳转成功率。",
                "reason": "落地页到达率约26.74%。",
                "evidence": ["landing_page_view_rate=26.744186%"],
                "expected_impact": "减少点击后的访问损失。",
                "success_metric": "landing_page_view_rate",
                "target_direction": "increase",
                "success_criteria": None,
                "owner": "landing_page_team",
            }
        ],
        "experiment_plan": [],
    }


def test_prompt_contains_approved_chinese_opening_and_public_evidence_boundary():
    prompt = build_facebook_ad_analysis_system_prompt()
    assert prompt.startswith("你是一名资深的Facebook/Meta广告投放分析专家。")
    assert "通过采集Facebook投放成效、效果较好的相似广告，并给出可执行的优化建议。" in prompt
    assert "公开表现代理信号" in prompt
    assert "不得" in prompt and "CTR" in prompt and "ROAS" in prompt
    assert "只输出原始JSON" in prompt


@pytest.mark.asyncio
async def test_mock_provider_returns_only_the_strict_contribution_shape():
    result = await MockLLMProvider().analyze_facebook_ad(_context())
    assert set(result) == {
        "key_findings", "creative_analysis", "audience_and_delivery_analysis",
        "additional_diagnoses", "recommended_actions", "experiment_plan",
    }
    assert "executive_summary" not in result and "performance_funnel" not in result
    assert result["recommended_actions"][0]["evidence"]


@pytest.mark.asyncio
async def test_transient_errors_retry_three_provider_attempts(monkeypatch):
    provider = object.__new__(OpenAILLMProvider)
    calls = 0

    async def completion(system, user):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.ReadTimeout("slow")
        return _valid_contribution()

    provider._json_completion = completion
    result = await provider.analyze_facebook_ad(_context())
    assert calls == 3
    assert result["recommended_actions"][0]["action_id"] == "check_landing_page"


@pytest.mark.asyncio
async def test_non_transient_400_is_not_retried():
    provider = object.__new__(OpenAILLMProvider)
    calls = 0

    async def completion(system, user):
        nonlocal calls
        calls += 1
        response = httpx.Response(400, request=httpx.Request("POST", "https://llm.example"))
        raise httpx.HTTPStatusError("bad request", request=response.request, response=response)

    provider._json_completion = completion
    with pytest.raises(FacebookAdLLMFailure) as exc_info:
        await provider.analyze_facebook_ad(_context())
    assert calls == 1
    assert exc_info.value.retryable is False
    assert exc_info.value.degradable is True


@pytest.mark.asyncio
async def test_invalid_success_gets_one_repair_with_exact_validation_errors():
    provider = object.__new__(OpenAILLMProvider)
    prompts: list[str] = []

    async def completion(system, user):
        prompts.append(system)
        if len(prompts) == 1:
            return {**_valid_contribution(), "recommended_actions": []}
        return _valid_contribution()

    provider._json_completion = completion
    result = await provider.analyze_facebook_ad(_context())
    assert len(prompts) == 2
    assert "recommended_actions" in prompts[1]
    assert "validation" in prompts[1].lower()
    assert result["key_findings"]


@pytest.mark.asyncio
async def test_failed_repair_becomes_typed_rules_only_degradation():
    provider = object.__new__(OpenAILLMProvider)

    async def invalid_completion(system, user):
        del system, user
        return {**_valid_contribution(), "recommended_actions": []}

    provider._json_completion = invalid_completion
    with pytest.raises(FacebookAdLLMFailure) as exc_info:
        await provider.analyze_facebook_ad(_context())
    assert exc_info.value.code == "INVALID_LLM_CONTRIBUTION"
    assert exc_info.value.degradable is True


@pytest.mark.asyncio
async def test_gateway_uses_full_text_timeout_for_facebook_analysis(monkeypatch):
    provider = object.__new__(GatewayResponsesLLMProvider)
    provider.timeout_seconds = 180.0
    observed = []

    async def completion(system, user, timeout_seconds=None):
        observed.append(timeout_seconds)
        return _valid_contribution()

    provider._json_completion = completion
    await provider.analyze_facebook_ad(_context())
    assert observed == [180.0]
```

Create explicit validation cases proving an important conclusion without evidence is rejected, an action without evidence is rejected, public references cannot be described as having verified private metrics, and small/incomplete samples cannot receive unsupported “scale now” or “pause immediately” instructions.

- [ ] **Step 2: Run the LLM tests and confirm the new method/module is absent**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_facebook_ad_analysis_llm.py -q
```

Expected: import or attribute failures mention `facebook_ad_analysis_prompt` or `analyze_facebook_ad`.

- [ ] **Step 3: Add the protocol method and the exact prompt contract**

Add to `LLMProvider` without changing the existing `analyze_ad_performance()` method:

```python
async def analyze_facebook_ad(
    self,
    context: dict[str, Any],
    *,
    repair_errors: list[str] | None = None,
) -> dict[str, Any]:
    """Return only a strict FacebookAdLLMContribution JSON object."""
```

In `facebook_ad_analysis_prompt.py`, define:

```python
class FacebookAdLLMFailure(AppError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        degradable: bool = True,
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.degradable = degradable
        super().__init__(message)
```

`build_facebook_ad_analysis_system_prompt()` must begin with the full approved Chinese expert opening from specification section 11.3 and include this sentence verbatim:

```text
通过采集Facebook投放成效、效果较好的相似广告，并给出可执行的优化建议。
```

Immediately clarify that “效果较好的相似广告” means candidates with publicly observable proxy signals such as active duration, repeated message angles, cross-region reuse, or multiple creative variants. It does not mean access to another advertiser's private performance, and it cannot justify invented CTR, CPC, CPA, purchases, revenue, or ROAS.

The prompt must also require all specification section 11.4 rules: submitted `insight` and deterministic calculations are the only current-ad performance truth, missing conversion is not zero, missing currency is not USD, profitability is unavailable without value/ROAS, important conclusions/actions require evidence, weak samples cannot justify immediate scaling/pausing, and output is raw JSON only with no Markdown or surrounding explanation. State that the allowed root is exactly the six fields of `FacebookAdLLMContribution`; the LLM must not return verdict, metrics, objective alignment, benchmark facts, source facts, or metadata.

When `repair_errors` is present, append an exact JSON array of Pydantic error strings and instruct the model to correct only those violations without changing deterministic facts.

- [ ] **Step 4: Implement shared retry, strict validation, and one repair request**

Define the shared runner:

```python
CompletionCall = Callable[[str, Any], Awaitable[dict[str, Any]]]


async def run_facebook_ad_llm_request(
    completion: CompletionCall,
    context: dict[str, Any],
    *,
    repair_errors: list[str] | None = None,
) -> dict[str, Any]: ...
```

For one logical request, call `completion()` up to three provider attempts for timeout, connection reset, HTTP 408/429, and transient 5xx. Use bounded exponential backoff plus jitter. Do not retry other 4xx. Redact provider response bodies and secrets from error text.

After a successful JSON response, validate with:

```python
try:
    contribution = FacebookAdLLMContribution.model_validate(data)
except ValidationError as exc:
    errors = [
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
        for item in exc.errors(include_url=False)
    ]
    if repair_errors is not None:
        raise FacebookAdLLMFailure(
            "INVALID_LLM_CONTRIBUTION",
            "; ".join(errors),
            retryable=False,
            degradable=True,
        ) from exc
    return await run_facebook_ad_llm_request(
        completion,
        context,
        repair_errors=errors,
    )
return contribution.model_dump(mode="json")
```

This permits exactly one repair request. A second invalid success becomes `FacebookAdLLMFailure(code="INVALID_LLM_CONTRIBUTION", degradable=True)` so Task 13 can switch to `analysis_mode="rules_only"`.

Before model validation, reject imperative scale/pause wording when the context says confidence is low, impressions are below 100, spend is missing/zero, or purchase/value/ROAS is unavailable. Require evidence on every `creative_analysis`/`audience_and_delivery_analysis` conclusion, additional diagnosis, and recommended action; Task 7 remains the final strict invariant boundary.

- [ ] **Step 5: Implement all providers, with a full-timeout Gateway override**

For `OpenAILLMProvider`, use the shared runner with its current `_json_completion()`:

```python
async def analyze_facebook_ad(
    self,
    context: dict[str, Any],
    *,
    repair_errors: list[str] | None = None,
) -> dict[str, Any]:
    async def completion(system: str, user: Any) -> dict[str, Any]:
        return await self._json_completion(system=system, user=user)

    return await run_facebook_ad_llm_request(
        completion,
        context,
        repair_errors=repair_errors,
    )
```

`GatewayResponsesLLMProvider` must explicitly override the method rather than inheriting the OpenAI implementation, because `_json_completion()` otherwise defaults to `fast_timeout_seconds`:

```python
async def analyze_facebook_ad(
    self,
    context: dict[str, Any],
    *,
    repair_errors: list[str] | None = None,
) -> dict[str, Any]:
    async def completion(system: str, user: Any) -> dict[str, Any]:
        return await self._json_completion(
            system=system,
            user=user,
            timeout_seconds=self.timeout_seconds,
        )

    return await run_facebook_ad_llm_request(
        completion,
        context,
        repair_errors=repair_errors,
    )
```

`MockLLMProvider.analyze_facebook_ad()` must return a deterministic, strictly validated contribution that cites available rule evidence and never adds private market metrics. If `repair_errors` is supplied, it still returns the same valid object. Any other concrete LLM provider implementing `LLMProvider` must expose the same signature before the protocol test passes.

- [ ] **Step 6: Run LLM, schema, provider, and lint checks**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_facebook_ad_analysis_llm.py tests/test_facebook_ad_analysis_schema.py tests/test_llm_provider.py -q
.venv\Scripts\python.exe -m ruff check backend/app/integrations/llm/base.py backend/app/integrations/llm/openai_provider.py backend/app/integrations/llm/responses_provider.py backend/app/integrations/llm/mock_provider.py backend/app/services/facebook_ad_analysis_prompt.py tests/test_facebook_ad_analysis_llm.py
```

Expected: three-attempt transient retry, non-transient 4xx behavior, one validation repair, typed degradation, strict evidence, approved wording, raw-JSON constraint, and Gateway full timeout pass; Ruff passes.

- [ ] **Step 7: Commit only Task 12 files**

```powershell
git add backend/app/integrations/llm/base.py backend/app/integrations/llm/openai_provider.py backend/app/integrations/llm/responses_provider.py backend/app/integrations/llm/mock_provider.py backend/app/services/facebook_ad_analysis_prompt.py tests/test_facebook_ad_analysis_llm.py
git diff --cached --check
git commit -m "feat: add strict facebook ad llm analysis"
```

### Task 13: Attempt-Owned Analysis Orchestration and Terminal Persistence

**Files:**
- Create: `backend/app/services/ad_analysis_orchestrator.py`
- Create: `tests/test_ad_analysis_orchestrator.py`

**Interfaces:**
- Consumes: Task 2 persistence models; Task 5 GenerationTask lifecycle hook; Task 6 rules; Task 7 assembler/schema; Task 9 media; Task 11 research; Task 12 `analyze_facebook_ad()` and typed degradation.
- Produces: `AdAnalysisRetryableError`; `AdAnalysisOrchestrator.run(session, task) -> dict[str, Any]`; conditional attempt ownership, non-decreasing stages, final result/reference persistence, retry/failure state, and post-commit cleanup.

- [ ] **Step 1: Write failing stage, degradation, ownership, persistence, and cleanup tests**

Create `tests/test_ad_analysis_orchestrator.py` with an async SQLite/PostgreSQL-compatible session fixture and injected fake services. Include these concrete cases:

```python
STAGES = {
    "queued": 0,
    "validating": 5,
    "downloading_media": 12,
    "inspecting_media": 20,
    "extracting_frames": 28,
    "analyzing_current_ad": 40,
    "building_search_queries": 50,
    "searching_market_ads": 58,
    "fetching_reference_ads": 66,
    "ranking_reference_ads": 72,
    "analyzing_reference_creatives": 80,
    "generating_recommendations": 90,
    "finalizing": 96,
    "completed": 100,
    "failed": 100,
}


@pytest.mark.asyncio
async def test_full_run_persists_before_cleanup(session_factory, stored_pair, fakes):
    analysis, task = stored_pair
    task.attempt_count = 1
    events: list[str] = []
    fakes.media.cleanup_callback = lambda analysis_id: events.append("cleanup")
    orchestrator = AdAnalysisOrchestrator(**fakes.kwargs(events=events))

    async with session_factory() as session:
        task = await session.get(GenerationTask, task.id)
        returned = await orchestrator.run(session, task)

    async with session_factory() as session:
        stored = await session.get(AdPerformanceAnalysis, analysis.id)
        references = (await session.execute(select(AdAnalysisReferenceAd))).scalars().all()
        assert stored.status == "succeeded"
        assert stored.stage == "completed" and stored.progress == 100
        assert stored.result_schema_version == "facebook_ad_analysis_v1"
        assert stored.analysis_result["schema_version"] == "facebook_ad_analysis_v1"
        assert stored.analysis_scope == "current_ad_and_public_market_research"
        assert stored.attempt_count == 1
        assert references
        assert returned == stored.analysis_result
    assert events.index("persisted") < events.index("cleanup")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("media_status", "research_status", "expected_scope"),
    [
        ("unavailable", "succeeded", "metrics_and_copy_only"),
        ("available", "unavailable", "current_ad_only"),
        ("available", "insufficient_results", "current_ad_and_limited_market_research"),
        ("available", "succeeded", "current_ad_and_public_market_research"),
    ],
)
async def test_degradation_maps_to_the_four_analysis_scopes(
    session_factory, stored_pair, fakes, media_status, research_status, expected_scope
):
    fakes.media.status = media_status
    fakes.research.status = research_status
    async with session_factory() as session:
        task = await session.get(GenerationTask, stored_pair[1].id)
        task.attempt_count = 1
        await AdAnalysisOrchestrator(**fakes.kwargs()).run(session, task)
    async with session_factory() as session:
        stored = await session.get(AdPerformanceAnalysis, stored_pair[0].id)
        assert stored.status == "succeeded"
        assert stored.analysis_scope == expected_scope


@pytest.mark.asyncio
async def test_llm_failure_succeeds_in_rules_only_mode(session_factory, stored_pair, fakes):
    fakes.llm.failure = FacebookAdLLMFailure(
        "LLM_TIMEOUT", "timed out", retryable=True, degradable=True
    )
    async with session_factory() as session:
        task = await session.get(GenerationTask, stored_pair[1].id)
        task.attempt_count = 1
        result = await AdAnalysisOrchestrator(**fakes.kwargs()).run(session, task)
    assert result["analysis_metadata"]["analysis_mode"] == "rules_only"
    assert result["analysis_metadata"]["analysis_scope"] != "rules_only"


@pytest.mark.asyncio
async def test_no_meaningful_result_persists_business_failure_without_retry(
    session_factory, stored_pair, fakes
):
    fakes.rules.meaningful = False
    fakes.media.status = "unavailable"
    fakes.research.status = "unavailable"
    fakes.llm.failure = FacebookAdLLMFailure(
        "LLM_UNAVAILABLE", "unavailable", retryable=True, degradable=True
    )
    async with session_factory() as session:
        task = await session.get(GenerationTask, stored_pair[1].id)
        task.attempt_count = 1
        returned = await AdAnalysisOrchestrator(**fakes.kwargs()).run(session, task)
    async with session_factory() as session:
        stored = await session.get(AdPerformanceAnalysis, stored_pair[0].id)
        assert stored.status == "failed" and stored.stage == "failed"
        assert stored.progress == 100
        assert stored.error_code == "ANALYSIS_UNAVAILABLE"
        assert stored.error_retryable is False
        assert returned["status"] == "failed"


@pytest.mark.asyncio
async def test_stale_attempt_cannot_overwrite_or_clean_newer_result(
    session_factory, stored_pair, fakes
):
    old_task = stored_pair[1]
    old_task.attempt_count = 1
    async with session_factory() as session:
        analysis = await session.get(AdPerformanceAnalysis, stored_pair[0].id)
        analysis.attempt_count = 2
        analysis.status = "succeeded"
        analysis.analysis_result = {"schema_version": "newer-result"}
        await session.commit()

    async with session_factory() as session:
        task = await session.get(GenerationTask, old_task.id)
        task.attempt_count = 1
        returned = await AdAnalysisOrchestrator(**fakes.kwargs()).run(session, task)

    async with session_factory() as session:
        stored = await session.get(AdPerformanceAnalysis, stored_pair[0].id)
        assert stored.analysis_result == {"schema_version": "newer-result"}
    assert returned["status"] == "stale_attempt_ignored"
    assert fakes.media.cleaned == []


@pytest.mark.asyncio
async def test_retryable_failure_persists_attempt_state_and_final_attempt_is_terminal(
    session_factory, stored_pair, fakes
):
    fakes.media.failure = MediaProcessingError("DOWNLOAD_TIMEOUT", "slow", retryable=True)
    for attempt, expected_status in [(1, "queued"), (2, "failed")]:
        async with session_factory() as session:
            task = await session.get(GenerationTask, stored_pair[1].id)
            task.attempt_count = attempt
            with pytest.raises(AdAnalysisRetryableError):
                await AdAnalysisOrchestrator(**fakes.kwargs()).run(session, task)
        async with session_factory() as session:
            stored = await session.get(AdPerformanceAnalysis, stored_pair[0].id)
            assert stored.status == expected_status
            if attempt == 2:
                assert stored.stage == "failed" and stored.progress == 100
                assert stored.completed_at is not None
```

Also assert stage history uses exactly the `STAGES` mapping, never decreases progress, image jobs skip `extracting_frames`, research-disabled jobs skip public-research stages with an explicit warning, a mismatched `business_id`/`generation_task_id` is rejected, final reference replacement rolls back with the result on insertion failure, and logs never include Bearer tokens, raw media bytes, base64 images, provider secrets, or full submitted payloads.

- [ ] **Step 2: Run the orchestrator tests and confirm the module is missing**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ad_analysis_orchestrator.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'backend.app.services.ad_analysis_orchestrator'`.

- [ ] **Step 3: Define stages, retry type, exact pair loading, and attempt claim**

Create:

```python
AD_ANALYSIS_STAGES: Final[dict[str, int]] = {
    "queued": 0,
    "validating": 5,
    "downloading_media": 12,
    "inspecting_media": 20,
    "extracting_frames": 28,
    "analyzing_current_ad": 40,
    "building_search_queries": 50,
    "searching_market_ads": 58,
    "fetching_reference_ads": 66,
    "ranking_reference_ads": 72,
    "analyzing_reference_creatives": 80,
    "generating_recommendations": 90,
    "finalizing": 96,
    "completed": 100,
    "failed": 100,
}


class AdAnalysisRetryableError(AppError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.retryable = True
        super().__init__(message)


class AdAnalysisOrchestrator:
    async def run(
        self,
        session: AsyncSession,
        task: GenerationTask,
    ) -> dict[str, Any]: ...
```

Load the pair with all exact invariants:

```text
task.queue_name == ad_analysis_queue
task.task_type == ad_performance_analysis
task.business_type == ad_performance_analysis
task.business_id == analysis.id
analysis.generation_task_id == task.id
analysis.analysis_id == task.payload_json.analysis_id
analysis.external_request_id == task.payload_json.external_request_id
```

The first write must atomically claim ownership by updating the matching analysis only when `analysis.attempt_count < task.attempt_count` and its status is `queued` or `processing`. Set `analysis.attempt_count=task.attempt_count`, `status="processing"`, `stage="validating"`, `progress=5`, clear the previous attempt error fields, and set `started_at` only if absent. If zero rows are updated, return `{"status":"stale_attempt_ignored", "analysis_id": ...}` without running services or cleanup.

Every later progress write must include both ownership predicates:

```text
analysis.attempt_count == task.attempt_count
analysis.generation_task_id == task.id
```

and require `analysis.progress <= requested_progress`. Update only stage/progress/summary fields; never decrease progress. If the conditional write affects zero rows, stop as a stale attempt.

- [ ] **Step 4: Execute the pipeline in fixed order with independent degradation**

Execute only in this order:

```text
rules -> media -> research -> LLM -> assembly -> persistence -> cleanup
```

Use stage updates exactly as follows:

1. validate identifiers at `validating=5`;
2. run Task 6 deterministic rules at `analyzing_current_ad=40` (media stages may occur before this because execution order still starts by computing rules in memory, but persist the externally visible media stages before `analyzing_current_ad`);
3. media download/probe/frame stages `12/20/28`; image jobs skip `extracting_frames`;
4. research inference/search/fetch/ranking stages `50/58/66/72` unless disabled/unavailable;
5. selected-reference creative interpretation at `80`;
6. LLM recommendations at `90`;
7. strict assembly at `finalizing=96`.

The computation order remains rules first: compute the deterministic result before invoking media, but emit stage `analyzing_current_ad=40` only after the applicable media stages so progress never decreases.

Media, research, and LLM failures are independent. Convert permanent or degradable media failure into an unavailable media summary; convert provider/research failure into `unavailable`; convert typed LLM degradation into `llm_contribution=None`, `analysis_mode="rules_only"`, and an `llm_error` warning. Do not use `rules_only` as an `AnalysisScope`.

Choose scope with this precedence:

```python
if media_status == "unavailable":
    analysis_scope = "metrics_and_copy_only"
elif research_status == "succeeded":
    analysis_scope = "current_ad_and_public_market_research"
elif research_status == "insufficient_results":
    analysis_scope = "current_ad_and_limited_market_research"
else:
    analysis_scope = "current_ad_only"
```

Meaningful deterministic metrics/copy analysis may succeed without media, research, or LLM. Use `ANALYSIS_UNAVAILABLE` only when rules are not meaningful and no media/research/LLM contribution can create a truthful useful result. Persist that handled business failure and return a failure summary without raising, so `GenerationTaskService` does not request a task retry.

- [ ] **Step 5: Persist terminal state and references with attempt ownership**

Assemble through Task 7 and prepare Task 11 reference rows. In one final transaction:

1. conditionally update `AdPerformanceAnalysis` where its ID, `generation_task_id`, and `attempt_count` still match;
2. set `status="succeeded"`, `stage="completed"`, `progress=100`, `analysis_scope`, `result_schema_version="facebook_ad_analysis_v1"`, `analysis_result`, deterministic `metrics`, `media_summary`, `research_summary`, `completed_at`, and clear error fields;
3. only after the conditional update returns one row, delete old `AdAnalysisReferenceAd` children and insert the selected Task 11 references;
4. commit the result and references together.

If the conditional terminal update affects zero rows, roll back pending reference changes, return `stale_attempt_ignored`, and do not clean artifacts. A reference insertion error must roll back the result update as well.

Only after the winning commit may `cleanup(analysis_id)` run. Cleanup failure is logged and does not change the succeeded result. Return the exact persisted `facebook_ad_analysis_v1` object; then the existing `GenerationTaskService._mark_succeeded()` may safely persist its own terminal task result.

- [ ] **Step 6: Persist retryable and final-exhausted failures before raising**

For retryable infrastructure/worker errors, first perform an ownership-checked analysis write. If `task.attempt_count < task.max_attempts`, set analysis `status="queued"`, preserve the highest reached stage/progress, save the sanitized `error_code`, `error_message`, `error_retryable=true`, and leave `completed_at=None`; then commit and raise `AdAnalysisRetryableError` so the existing GenerationTask retry handling runs.

If `task.attempt_count >= task.max_attempts`, conditionally set analysis `status="failed"`, `stage="failed"`, `progress=100`, the final sanitized error, `error_retryable=false`, and `completed_at=utcnow()` before re-raising. This guarantees the final exhausted attempt cannot leave analysis in `processing` even though `GenerationTaskService` marks the task separately.

If any failure-state conditional update affects zero rows, treat it as stale: do not overwrite the newer attempt, do not delete references, do not clean artifacts, and return `stale_attempt_ignored` instead of raising from the obsolete attempt.

Use structured logs containing only `analysis_id`, `generation_task_id`, attempt, stage, provider name, duration, candidate counts, selected counts, status, scope, and sanitized error code. Never log request JSON, media bytes/paths containing secrets, data URLs, API keys, Authorization values, or public-provider response bodies.

- [ ] **Step 7: Run orchestrator, lifecycle, schema, and lint checks**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ad_analysis_orchestrator.py tests/test_generation_tasks.py tests/test_facebook_ad_analysis_schema.py tests/test_facebook_ad_metrics.py -q
.venv\Scripts\python.exe -m ruff check backend/app/services/ad_analysis_orchestrator.py tests/test_ad_analysis_orchestrator.py
```

Expected: exact stages, all four scopes, rules-only mode, business failure, retry persistence, final exhaustion, stale ownership, reference atomicity, post-commit cleanup, and safe logging pass; Ruff passes.

- [ ] **Step 8: Commit only Task 13 files**

```powershell
git add backend/app/services/ad_analysis_orchestrator.py tests/test_ad_analysis_orchestrator.py
git diff --cached --check
git commit -m "feat: orchestrate asynchronous ad analysis"
```

### Task 14: Runtime Configuration, Dedicated Worker, Beat Maintenance, and Private Volume

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/worker/celery_app.py`
- Modify: `backend/app/worker/tasks.py`
- Modify: `infra/docker/backend.Dockerfile`
- Modify: `docker-compose.prod.yml`
- Modify: `.env.example`
- Modify: `.env.production.example`
- Create: `tests/test_ad_analysis_runtime_config.py`

**Interfaces:**
- Consumes: Task 5 queue/recovery service and its explicit `queue_name=task.queue_name` publication; Task 9 orphan cleanup; all server-owned limits used by Tasks 9, 11, 12, and 13.
- Produces: validated `Settings` fields for every `AD_ANALYSIS_*`/`PUBLIC_RESEARCH_*` variable; dedicated routing only for Beat tasks `ad_analysis.recover_stale` and `ad_analysis.cleanup_media`; Docker image with FFmpeg/FFprobe; Compose service `worker_ad_analysis`; private volume `ad_analysis_media_data`.

- [ ] **Step 1: Write failing defaults, env parsing, routes, schedule, image, and Compose topology tests**

Create `tests/test_ad_analysis_runtime_config.py`:

```python
import json
import os
import subprocess
from pathlib import Path

from backend.app.core.config import Settings
from backend.app.services import generation_task_dispatcher as dispatcher
from backend.app.worker.celery_app import celery_app
from backend.app.worker.tasks import process_generation_task


def _render_production_compose() -> dict:
    env = os.environ.copy()
    env.setdefault("POSTGRES_PASSWORD", "test-only-compose-password")
    env.setdefault("APP_ENV_FILE", ".env.production.example")
    completed = subprocess.run(
        [
            "docker", "compose", "-f", "docker-compose.prod.yml",
            "--env-file", ".env.production.example", "config", "--format", "json",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    return json.loads(completed.stdout)


def test_ad_analysis_v1_defaults_are_exact():
    settings = Settings(_env_file=None)
    assert settings.ad_analysis_queue == "ad_analysis_queue"
    assert settings.ad_analysis_max_attempts == 2
    assert settings.ad_analysis_stale_timeout_seconds == 600
    assert settings.ad_analysis_media_root == "/data/ad-analysis-media"
    assert settings.ad_analysis_media_allowed_hosts == ["newpixel.messrocts.com"]
    assert settings.ad_analysis_video_max_bytes == 104857600
    assert settings.ad_analysis_video_max_duration_seconds == 20.0
    assert settings.ad_analysis_download_concurrency == 4
    assert settings.ad_analysis_ffmpeg_concurrency == 2
    assert settings.ad_analysis_llm_concurrency == 4
    assert settings.public_research_enabled is False
    assert settings.public_research_provider == "disabled"
    assert settings.public_research_total_timeout_seconds == 90
    assert settings.public_research_page_timeout_seconds == 10
    assert settings.public_research_max_queries == 12
    assert settings.public_research_max_candidates == 30
    assert settings.public_research_max_selected == 5


def test_comma_separated_host_and_domain_lists_are_normalized(monkeypatch):
    monkeypatch.setenv(
        "AD_ANALYSIS_MEDIA_ALLOWED_HOSTS",
        "newpixel.messrocts.com, media.example.com ,newpixel.messrocts.com",
    )
    monkeypatch.setenv("PUBLIC_RESEARCH_ALLOWED_DOMAINS", "facebook.com,example.com")
    monkeypatch.setenv("PUBLIC_RESEARCH_BLOCKED_DOMAINS", "localhost,internal.example")
    settings = Settings(_env_file=None)
    assert settings.ad_analysis_media_allowed_hosts == [
        "newpixel.messrocts.com", "media.example.com"
    ]
    assert settings.public_research_allowed_domains == ["facebook.com", "example.com"]
    assert settings.public_research_blocked_domains == ["localhost", "internal.example"]


def test_missing_public_provider_configuration_does_not_fail_settings_startup(monkeypatch):
    monkeypatch.setenv("PUBLIC_RESEARCH_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_RESEARCH_PROVIDER", "json_search")
    monkeypatch.delenv("PUBLIC_RESEARCH_JSON_SEARCH_URL", raising=False)
    settings = Settings(_env_file=None)
    assert settings.public_research_enabled is True
    assert settings.public_research_json_search_url is None


def test_celery_routes_and_beat_schedule_are_dedicated():
    routes = dict(celery_app.conf.task_routes or {})
    assert "generation_tasks.process" not in routes
    assert routes["ad_analysis.recover_stale"]["queue"] == "ad_analysis_queue"
    assert routes["ad_analysis.cleanup_media"]["queue"] == "ad_analysis_queue"
    schedule = celery_app.conf.beat_schedule
    assert schedule["recover-stale-ad-analysis"]["task"] == "ad_analysis.recover_stale"
    assert schedule["recover-stale-ad-analysis"]["options"]["queue"] == "ad_analysis_queue"
    assert schedule["cleanup-old-ad-analysis-media"]["task"] == "ad_analysis.cleanup_media"
    assert (
        schedule["cleanup-old-ad-analysis-media"]["options"]["queue"]
        == "ad_analysis_queue"
    )
    assert celery_app.conf.worker_prefetch_multiplier == 1


def test_generic_generation_task_publication_keeps_the_explicit_caller_queue(monkeypatch):
    published = []

    def record_apply_async(*, args, queue, priority, countdown):
        published.append((args[0], queue, priority, countdown))

    monkeypatch.setattr(process_generation_task, "apply_async", record_apply_async)
    for queue_name in (
        "text_queue", "image_queue", "video_queue", "callback_queue", "ad_analysis_queue"
    ):
        dispatcher._enqueue_celery_generation_task(
            f"{queue_name}-task", queue_name, priority=3, countdown_seconds=0
        )

    assert [item[1] for item in published] == [
        "text_queue", "image_queue", "video_queue", "callback_queue", "ad_analysis_queue"
    ]


def test_backend_image_installs_ffmpeg_and_ffprobe():
    dockerfile = Path("infra/docker/backend.Dockerfile").read_text(encoding="utf-8")
    assert "apt-get install" in dockerfile
    assert "ffmpeg" in dockerfile


def test_compose_adds_only_the_required_private_media_mounts():
    compose = _render_production_compose()
    services = compose["services"]
    assert "worker_ad_analysis" in services
    command = " ".join(services["worker_ad_analysis"]["command"])
    assert "--queues=ad_analysis_queue" in command
    assert "--concurrency=4" in command
    mounted = {
        name
        for name, service in services.items()
        if any(
            volume.get("type") == "volume"
            and volume.get("source") == "ad_analysis_media_data"
            and volume.get("target") == "/data/ad-analysis-media"
            for volume in service.get("volumes", [])
        )
    }
    assert mounted == {"backend", "worker_ad_analysis", "worker_beat"}
    assert "ad_analysis_media_data" in compose["volumes"]
    assert "worker_text" in services and "worker_video" in services and "web" in services
```

This test deliberately uses the standard library plus `docker compose config --format json`; do not add PyYAML or another production dependency solely to inspect Compose.

- [ ] **Step 2: Run runtime tests and confirm settings/routes/worker are absent**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ad_analysis_runtime_config.py -q
```

Expected: assertions fail for missing `Settings.ad_analysis_queue`, Celery routes/schedules, FFmpeg package, and `worker_ad_analysis`.

- [ ] **Step 3: Add every exact environment-backed setting and parser**

Add these fields to `Settings`:

```python
ad_analysis_queue: str = "ad_analysis_queue"
ad_analysis_max_attempts: int = Field(default=2, ge=1, le=5)
ad_analysis_stale_timeout_seconds: int = Field(default=600, ge=60, le=86400)
ad_analysis_media_root: str = "/data/ad-analysis-media"
ad_analysis_media_allowed_hosts: Annotated[list[str], NoDecode] = Field(
    default_factory=lambda: ["newpixel.messrocts.com"]
)
ad_analysis_video_max_bytes: int = Field(default=104857600, ge=1)
ad_analysis_video_max_duration_seconds: float = Field(default=20.0, gt=0, le=20)
ad_analysis_download_concurrency: int = Field(default=4, ge=1, le=32)
ad_analysis_ffmpeg_concurrency: int = Field(default=2, ge=1, le=16)
ad_analysis_llm_concurrency: int = Field(default=4, ge=1, le=32)

public_research_enabled: bool = False
public_research_provider: Literal["disabled", "json_search"] = "disabled"
public_research_total_timeout_seconds: float = Field(default=90.0, ge=1, le=300)
public_research_page_timeout_seconds: float = Field(default=10.0, ge=1, le=60)
public_research_max_queries: int = Field(default=12, ge=8, le=12)
public_research_max_candidates: int = Field(default=30, ge=1, le=30)
public_research_max_selected: int = Field(default=5, ge=1, le=5)
public_research_allowed_domains: Annotated[list[str], NoDecode] = Field(default_factory=list)
public_research_blocked_domains: Annotated[list[str], NoDecode] = Field(default_factory=list)
public_research_json_search_url: str | None = None
public_research_json_search_api_key: str | None = None
```

Use one `mode="before"` validator for the three comma-separated host/domain fields. Trim whitespace, lowercase hostnames, remove blanks and duplicates while preserving first occurrence. Do not validate the JSON search URL/API key as required at Settings startup; Task 10's factory deliberately turns incomplete provider configuration into disabled/degraded mode.

Document every variable with the exact defaults in both env example files. Add empty `PUBLIC_RESEARCH_JSON_SEARCH_URL` and `PUBLIC_RESEARCH_JSON_SEARCH_API_KEY` entries because Task 10 consumes them; no real keys or tokens may appear.

The env example files already contain unrelated edits. Before staging this task, require the implementer to inspect and interactively stage only these lines:

```powershell
git diff -- .env.example .env.production.example
git add -p .env.example .env.production.example
```

- [ ] **Step 4: Route the two dedicated maintenance tasks and preserve explicit job publication**

Preserve `task_acks_late=True`, `task_reject_on_worker_lost=True`, `task_track_started=True`, every existing Beat entry, and `worker_prefetch_multiplier=1`. Do **not** add a static route for `generation_tasks.process`: that task is shared by text, image, video, callback, and ad-analysis jobs, and Task 5 already publishes every instance with the row's explicit `queue_name`.

Build merged route/schedule dictionaries before `celery_app.conf.update()`:

```python
task_routes = dict(celery_app.conf.task_routes or {})
task_routes.update({
    "ad_analysis.recover_stale": {"queue": settings.ad_analysis_queue},
    "ad_analysis.cleanup_media": {"queue": settings.ad_analysis_queue},
})

beat_schedule = dict(celery_app.conf.beat_schedule or {})
beat_schedule.update({
    "recover-stale-ad-analysis": {
        "task": "ad_analysis.recover_stale",
        "schedule": 60.0,
        "options": {"queue": settings.ad_analysis_queue},
    },
    "cleanup-old-ad-analysis-media": {
        "task": "ad_analysis.cleanup_media",
        "schedule": 3600.0,
        "options": {"queue": settings.ad_analysis_queue},
    },
})

celery_app.conf.update(
    # preserve all existing settings in this call
    task_routes=task_routes,
    beat_schedule=beat_schedule,
)
```

The safety invariant is exact: `generation_tasks.process` has no ad-analysis static route; `schedule_generation_task_id()`/`_enqueue_celery_generation_task()` always pass `queue=queue_name`; the four existing queues therefore retain their current destinations, while an ad-analysis row is published explicitly to `ad_analysis_queue`. Both Beat entries carry explicit queue options and also have dedicated task routes so manual publication cannot fall through to `text_queue`.

Task 5 already defines the only stale-recovery task, with this exact public name:

```python
@celery_app.task(name="ad_analysis.recover_stale", ignore_result=True)
def recover_stale_ad_analysis_tasks() -> None:
    settings = get_settings()
    _run_async(
        AdAnalysisDispatchService().recover_stale(
            AsyncSessionLocal,
            stale_after_seconds=settings.ad_analysis_stale_timeout_seconds,
        )
    )
```

Do not redefine or rename that function in this task. Import its module through Celery's existing
`include=["backend.app.worker.tasks"]`, register it in the route/schedule dictionaries above, and
add only the cleanup task below to `backend/app/worker/tasks.py`. Ensure `timedelta` and
`AdAnalysisMediaService` are imported in that module using the module's existing import style:

```python
@celery_app.task(name="ad_analysis.cleanup_media", ignore_result=True)
def cleanup_old_ad_analysis_media() -> None:
    _run_async(
        AdAnalysisMediaService().cleanup_orphans(older_than=timedelta(hours=2))
    )
```

The stale task handles only Task 5 ad-analysis rows; the existing generic recovery remains in place for every other queue. The cleanup task deletes only validated direct child directories older than two hours through Task 9.

- [ ] **Step 5: Install FFmpeg and add the isolated worker/private volume**

Before Python installation in `infra/docker/backend.Dockerfile`, install FFmpeg and remove apt indexes:

```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
```

The Debian `ffmpeg` package must provide both `ffmpeg` and `ffprobe`; Task 16 verifies both binaries inside the worker.

In `docker-compose.prod.yml`:

1. preserve every existing service, healthcheck, queue, replica, and `storage_data` mount;
2. declare `ad_analysis_media_data:` under top-level `volumes`;
3. mount `ad_analysis_media_data:/data/ad-analysis-media` only into `backend`, `worker_ad_analysis`, and `worker_beat`;
4. do not mount it into `web`, nginx, `worker_text`, `worker_image`, `worker_video`, or `worker_callback`;
5. set `AD_ANALYSIS_MEDIA_ROOT=/data/ad-analysis-media` on the three mounted services;
6. add `worker_ad_analysis` using the backend image, DB/Redis environment, and dependencies already used by other workers;
7. use this exact command:

```yaml
command: >
  celery -A backend.app.worker.celery_app:celery_app worker
  --loglevel=INFO
  --queues=${AD_ANALYSIS_QUEUE:-ad_analysis_queue}
  --concurrency=${AD_ANALYSIS_LLM_CONCURRENCY:-4}
  --hostname=ad-analysis@%h
```

Do not expose the media volume through nginx or create host bind mounts for it.

- [ ] **Step 6: Run settings, Compose rendering, worker, and lint checks**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ad_analysis_runtime_config.py tests/test_ad_analysis_dispatch.py tests/test_ad_analysis_media.py -q
.venv\Scripts\python.exe -m ruff check backend/app/core/config.py backend/app/worker/celery_app.py backend/app/worker/tasks.py tests/test_ad_analysis_runtime_config.py
docker compose -f docker-compose.prod.yml --env-file .env.production config --quiet
docker compose -f docker-compose.prod.yml --env-file .env.production config --services
```

Expected: defaults/parsing/routes/schedules pass, Compose is valid, all previous services plus `worker_ad_analysis` are listed, and missing search-provider configuration does not prevent configuration import.

- [ ] **Step 7: Commit only Task 14 changes with interactive env-example staging**

```powershell
git add backend/app/core/config.py backend/app/worker/celery_app.py backend/app/worker/tasks.py infra/docker/backend.Dockerfile docker-compose.prod.yml tests/test_ad_analysis_runtime_config.py
git diff -- .env.example .env.production.example
git add -p .env.example .env.production.example
git diff --cached --check
git diff --cached --name-only
git commit -m "feat: add ad analysis worker runtime"
```

Before committing, verify the cached file list contains only the seven Task 14 target files plus the two env examples, and no unrelated pre-existing env edits.

### Task 15: End-to-End API Regression Tests and External Caller Documentation

**Files:**
- Create: `tests/test_external_ad_performance_integration.py`
- Create: `docs/EXTERNAL_FACEBOOK_AD_ANALYSIS_API.md`
- Create: `docs/examples/external-facebook-ad-analysis-request.json`

**Interfaces:**
- Consumes: Tasks 1–14 complete API, database, worker, media, research, strict result, and error contracts; approved sample at `C:\Users\panda\Downloads\Telegram Desktop\广告数据分析包.json`.
- Produces: caller-facing request/poll/error documentation, a repository-safe request example, and integrated regression proof that the new async API coexists with the original synchronous route and all previous queues.

- [ ] **Step 1: Write one self-contained integrated lifecycle, auth, persistence, and regression module**

Create the complete module `tests/test_external_ad_performance_integration.py`. Do not
depend on another test module's fixtures. The fixture deliberately uses a file-backed
SQLite database (not `:memory:`), because the synchronous `TestClient` and the
`asyncio.run()` persistence helpers must observe the same database. Import the model
package before `Base.metadata.create_all()` so the new reference-ad model is registered.

```python
import asyncio
import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.db.models  # noqa: F401 -- registers every mapped model with Base
from backend.app.core.config import get_settings
from backend.app.db.base import Base, utcnow
from backend.app.db.models.ad_performance_analysis import AdPerformanceAnalysis
from backend.app.db.models.generation_task import GenerationTask
from backend.app.db.session import get_session
from backend.app.main import create_app
from backend.app.schemas.external_ad_performance_analysis import (
    ExternalAdPerformanceAnalysisCreate,
    ad_analysis_payload_hash,
    canonicalize_ad_analysis_payload,
)
from backend.app.schemas.facebook_ad_analysis import FacebookAdAnalysisV1
from backend.app.integrations.llm.mock_provider import MockLLMProvider
from backend.app.services.ad_analysis_media_service import (
    MediaAnalysisBundle,
    MediaProcessingError,
)
from backend.app.services.ad_analysis_orchestrator import AdAnalysisOrchestrator
from backend.app.services.ad_analysis_research_service import (
    PublicAdCandidate,
    PublicResearchResult,
)
from backend.app.services.external_ad_performance_analysis_service import (
    ExternalAdPerformanceAnalysisService,
)
from backend.app.services.facebook_ad_metrics import build_facebook_rule_analysis
from backend.app.services import generation_task_dispatcher
from backend.app.services.generation_task_service import GenerationTaskService
from backend.app.worker.celery_app import celery_app
import backend.app.services.ad_analysis_orchestrator as ad_analysis_orchestrator
import backend.app.services.generation_task_service as generation_task_service

JOBS_PATH = "/api/v1/integrations/ad-performance/analysis-jobs"
APPROVED_SAMPLE = Path(r"C:\Users\panda\Downloads\Telegram Desktop\广告数据分析包.json")
EXAMPLE_PATH = Path("docs/examples/external-facebook-ad-analysis-request.json")


def _documented_example() -> dict:
    return json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))


def _approved_payload() -> dict:
    payload = json.loads(APPROVED_SAMPLE.read_text(encoding="utf-8"))
    payload["external_request_id"] = "approved-sample-test-request"
    return payload


@pytest.fixture
def api_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_ADS_ACCESS_TOKEN", "analysis-token")
    monkeypatch.setenv("GENERATION_TASK_EXECUTION_BACKEND", "celery")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PUBLIC_RESEARCH_ENABLED", "false")
    monkeypatch.setenv("PUBLIC_RESEARCH_PROVIDER", "disabled")
    get_settings.cache_clear()

    database_path = tmp_path / "external-ad-analysis.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path.as_posix()}")

    async def prepare_database() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(prepare_database())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with session_factory() as session:
            yield session

    published: list[tuple[str, str, int, int]] = []

    # Patch the actual publisher called by schedule_generation_task_id(), not a
    # router-level convenience wrapper. The exact optional countdown signature is
    # also a compatibility check for all existing generation queues.
    def fake_publish(task_id, queue_name, priority, countdown_seconds=0):
        published.append((task_id, queue_name, priority, countdown_seconds))

    monkeypatch.setattr(
        generation_task_dispatcher,
        "_enqueue_celery_generation_task",
        fake_publish,
    )
    app = create_app()
    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield SimpleNamespace(
            client=test_client,
            session_factory=session_factory,
            published=published,
        )
    app.dependency_overrides.clear()
    asyncio.run(engine.dispose())
    get_settings.cache_clear()


@pytest.fixture
def client(api_runtime):
    return api_runtime.client


@pytest.fixture
def bearer_headers():
    return {"Authorization": "Bearer analysis-token"}


@pytest.fixture
def stored_job(api_runtime):
    """Synchronously persist one legal analysis/task lifecycle pair for GET tests."""

    def create(*, status: str, result: dict | None = None, error_code: str | None = None):
        lifecycle = {
            "queued": ("queued", "queued", 0),
            "processing": ("running", "analyzing_current_ad", 40),
            "succeeded": ("succeeded", "completed", 100),
            "failed": ("failed", "failed", 100),
        }
        task_status, stage, progress = lifecycle[status]
        analysis_id = f"ana_fixture_{uuid4().hex}"
        external_request_id = f"fixture-{uuid4().hex}"

        async def persist():
            async with api_runtime.session_factory() as session:
                payload = _documented_example()
                payload["external_request_id"] = external_request_id
                analysis = AdPerformanceAnalysis(
                    analysis_id=analysis_id,
                    external_request_id=external_request_id,
                    payload_hash=hashlib.sha256(external_request_id.encode()).hexdigest(),
                    request_payload=payload,
                    normalized_payload=payload,
                    status=status,
                    stage=stage,
                    progress=progress,
                    analysis_result=result or {},
                    error_code=error_code,
                    error_message="deterministic fixture failure" if error_code else None,
                    error_retryable=False,
                    started_at=utcnow() if status != "queued" else None,
                    completed_at=utcnow() if status in {"succeeded", "failed"} else None,
                    max_attempts=2,
                )
                session.add(analysis)
                await session.flush()
                task = GenerationTask(
                    queue_name="ad_analysis_queue",
                    task_type="ad_performance_analysis",
                    business_type="ad_performance_analysis",
                    business_id=analysis.id,
                    status=task_status,
                    payload_json={
                        "analysis_record_id": analysis.id,
                        "analysis_id": analysis.analysis_id,
                        "external_request_id": analysis.external_request_id,
                    },
                    max_attempts=2,
                    queued_at=utcnow(),
                )
                session.add(task)
                await session.flush()
                analysis.generation_task_id = task.id
                await session.commit()
                return analysis

        analysis = asyncio.run(persist())
        create.analysis_id = analysis.analysis_id
        create.analysis_record_id = analysis.id
        return analysis

    return create


def test_async_create_replay_conflict_and_single_poll_key(
    api_runtime, client, bearer_headers
):
    payload = _documented_example()
    created = client.post(JOBS_PATH, json=payload, headers=bearer_headers)
    replay = client.post(JOBS_PATH, json=payload, headers=bearer_headers)
    changed_payload = deepcopy(payload)
    changed_payload["insight"]["spend"] = "9.99"
    changed = client.post(JOBS_PATH, json=changed_payload, headers=bearer_headers)
    analysis_id = created.json()["data"]["analysis_id"]

    assert (created.status_code, created.json()["code"]) == (202, 1001)
    assert len(api_runtime.published) == 1
    assert api_runtime.published[0][1] == "ad_analysis_queue"
    assert (replay.status_code, replay.json()["code"]) == (200, 0)
    assert replay.json()["data"]["analysis_id"] == analysis_id
    assert len(api_runtime.published) == 1
    assert (changed.status_code, changed.json()["code"]) == (409, 4001)
    assert changed.json()["data"] == {
        "error_code": "IDEMPOTENCY_CONFLICT",
        "external_request_id": payload["external_request_id"],
        "analysis_id": analysis_id,
    }
    assert client.get(f"{JOBS_PATH}/{analysis_id}", headers=bearer_headers).status_code == 200
    assert (
        "/api/v1/integrations/ad-performance/analysis-jobs/"
        "by-external-request/{external_request_id}"
        not in client.app.openapi()["paths"]
    )


@pytest.mark.parametrize("status", ["queued", "processing", "succeeded", "failed"])
def test_every_known_lifecycle_state_returns_http_200(
    client, bearer_headers, stored_job, status
):
    analysis = stored_job(
        status=status,
        result={"schema_version": "facebook_ad_analysis_v1"} if status == "succeeded" else None,
        error_code="ANALYSIS_UNAVAILABLE" if status == "failed" else None,
    )
    response = client.get(f"{JOBS_PATH}/{analysis.analysis_id}", headers=bearer_headers)
    assert response.status_code == 200
    assert response.json()["data"]["status"] == status


def test_complete_401_404_and_422_envelopes(client, bearer_headers):
    unknown = client.get(f"{JOBS_PATH}/ana_missing", headers=bearer_headers)
    unauthorized = client.post(JOBS_PATH, json=_documented_example())
    invalid = client.post(
        JOBS_PATH,
        json={**_documented_example(), "creative": {"creative_type": "image"}},
        headers=bearer_headers,
    )
    assert unknown.json() == {
        "code": 4001,
        "message": "analysis job not found",
        "data": {"error_code": "NOT_FOUND"},
    }
    assert (unknown.status_code, unauthorized.status_code, invalid.status_code) == (404, 401, 422)
    assert unauthorized.json()["code"] == 4003
    assert invalid.json()["code"] == 4001
    assert all(set(response.json()) == {"code", "message", "data"} for response in (unknown, unauthorized, invalid))


def test_get_reads_postgresql_and_never_celery_result_backend(
    client, bearer_headers, stored_job, monkeypatch
):
    analysis = stored_job(status="succeeded", result={"schema_version": "facebook_ad_analysis_v1"})
    monkeypatch.setattr(
        celery_app,
        "AsyncResult",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Celery backend queried")),
    )
    response = client.get(f"{JOBS_PATH}/{analysis.analysis_id}", headers=bearer_headers)
    assert response.status_code == 200
    assert response.json()["data"]["result"]["schema_version"] == "facebook_ad_analysis_v1"


def test_publication_keeps_all_existing_queues_and_adds_only_ad_analysis_queue(monkeypatch):
    captured: list[tuple[str, str]] = []

    def record_apply_async(*, args, queue, priority, countdown):
        captured.append((args[0], queue))

    monkeypatch.setattr(
        "backend.app.worker.tasks.process_generation_task.apply_async", record_apply_async
    )
    for queue_name in ("text_queue", "image_queue", "video_queue", "callback_queue", "ad_analysis_queue"):
        generation_task_dispatcher._enqueue_celery_generation_task(
            f"{queue_name}-id", queue_name, priority=0, countdown_seconds=0
        )
    assert [queue for _, queue in captured] == [
        "text_queue", "image_queue", "video_queue", "callback_queue", "ad_analysis_queue"
    ]


def test_documented_example_and_authoritative_hashes_are_pinned():
    payload = _documented_example()
    creative = payload["creative"]
    assert bool(creative.get("image_url")) ^ bool(creative.get("video_url"))
    assert {"thumbnail_url", "video_keyframes"}.isdisjoint(creative)
    assert {"callback_url", "external_account_id", "research_context"}.isdisjoint(payload)

    raw = APPROVED_SAMPLE.read_bytes()
    approved = ExternalAdPerformanceAnalysisCreate.model_validate(_approved_payload())
    # Do not confuse these three independently valuable regression vectors:
    # 0691... is Task 1's synthetic normalizer vector; a682... is this canonical
    # idempotency vector; fba7... is only the downloaded file's raw-byte checksum.
    assert ad_analysis_payload_hash(approved) == (
        "a6826e07143a295a46b43b51d5dca838f28c434e6a63df73472a4de65771abf7"
    )
    assert hashlib.sha256(raw).hexdigest() == (
        "fba7a1c218b96adeaaf57246873117829ea23f29b4aa8da43dd6e01239591e56"
    )
    canonical = json.dumps(
        canonicalize_ad_analysis_payload(approved),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(canonical) == 4_238


def test_schema_rejects_unknown_fields_and_media_hard_failure_degrades_to_metrics_copy_only(
    api_runtime,
):
    with pytest.raises(ValidationError):
        FacebookAdAnalysisV1.model_validate({"schema_version": "facebook_ad_analysis_v1"})
    # The deterministic Task 13 integration fixture injects a MediaProcessingError
    # (VIDEO_DURATION_LIMIT) and runs GenerationTaskService.process_task(). It must
    # reach `succeeded`, not fail, with this externally visible degradation.
    completed = _run_deterministic_task(
        api_runtime, media_error="VIDEO_DURATION_LIMIT", research="skipped"
    )
    assert completed["analysis_metadata"]["analysis_scope"] == "metrics_and_copy_only"
    assert completed["analysis_metadata"]["media_analysis"]["status"] == "unavailable"
    FacebookAdAnalysisV1.model_validate(completed)


def test_disabled_research_is_truthful_and_selected_references_are_complete(api_runtime):
    disabled = _run_deterministic_task(api_runtime, media_error=None, research="skipped")
    market = disabled["market_intelligence"]
    assert market["status"] == "skipped"
    assert market["selected_reference_ads"] == []
    assert disabled["analysis_metadata"]["analysis_scope"] == "current_ad_only"
    assert any("public research" in warning.lower() for warning in disabled["analysis_metadata"]["warnings"])

    researched = _run_deterministic_task(api_runtime, media_error=None, research="succeeded")
    reference = researched["market_intelligence"]["selected_reference_ads"][0]
    assert {
        "source_url", "collected_at", "similarity_score", "performance_evidence"
    } <= reference
    evidence = reference["performance_evidence"]
    assert {"type", "confidence", "limitations"} <= evidence
    assert evidence["verified"] is False
    assert not {"ctr", "cpc", "cpa", "purchase", "purchases", "revenue", "roas", "spend"}.intersection(reference)


def test_real_deterministic_completion_strictly_validates_and_sync_route_is_unchanged(
    api_runtime, client, bearer_headers
):
    result = _run_deterministic_task(api_runtime, media_error=None, research="skipped")
    strict = FacebookAdAnalysisV1.model_validate(result)
    assert strict.executive_summary.verdict == "optimize"
    assert strict.executive_summary.primary_bottleneck == "landing_page"
    assert strict.executive_summary.confidence == "medium"
    assert strict.executive_summary.scale_eligibility == "not_ready"
    assert strict.executive_summary.pause_recommended is False
    assert strict.performance_funnel.landing_page.metrics["landing_page_view_rate"].value == pytest.approx(26.744186)

    sync_payload = _documented_example()
    sync_payload.pop("external_request_id")
    response = client.post(
        "/api/v1/integrations/ad-performance/analyses",
        json=sync_payload,
        headers=bearer_headers,
    )
    assert response.status_code == 201
    assert "analysis_id" in response.json() and "code" not in response.json()
```

Immediately below the code above, implement `_run_deterministic_task()` in the same
module rather than leaving it implicit. Give the helper `api_runtime` as its first
parameter and call it from each test as `_run_deterministic_task(api_runtime, ...)`.
It must create a valid Task 3 pair in the fixture database, inject Task 13's fake
media/research/LLM collaborators into `AdAnalysisOrchestrator`, and invoke
`GenerationTaskService.process_task(task.id)`. Its `media_error` branch must raise
Task 9 `MediaProcessingError`; its `research` branch must return Task 11's typed
`PublicResearchResult` (`skipped` or one complete `succeeded` public reference).
Return the persisted `analysis_result` after a fresh database read. This is
deliberately the one end-to-end execution point: it proves the Task 5 route, Task 13
persistence, and Task 7 strict result all cooperate without real HTTP, FFmpeg, public
search, or a live LLM.

Use the following concrete helper shape after importing `MediaAnalysisBundle`,
`MediaProcessingError`, `PublicAdCandidate`, `PublicResearchResult`,
`AdAnalysisOrchestrator`, and the Task 6 rule builder. The Task 13 constructor
signature below names the production `AdAnalysisMediaService` and
`AdAnalysisResearchService` types; the integration module itself deliberately does not
import unused production classes. Keep its fakes small: the real orchestrator,
assembler, and generation-task lifecycle must still run.

```python
def _run_deterministic_task(api_runtime, *, media_error: str | None, research: str) -> dict:
    payload = _approved_payload()
    payload["external_request_id"] = f"deterministic-{uuid4().hex}"

    class FakeMedia:
        async def prepare(self, analysis_id, creative):
            if media_error:
                raise MediaProcessingError(media_error, "deterministic media limit", retryable=False)
            return MediaAnalysisBundle(
                media_type="image",
                source_field="creative.image_url",
                source_path=Path("/private/source.jpg"),
                thumbnail_path=Path("/private/source.jpg"),
                keyframe_paths=(),
                keyframe_timestamps=(),
                media_bytes=1,
                status="available",
                visual_analysis_available=True,
                probe=None,
                warnings=(),
            )

        def cleanup(self, analysis_id):
            return None

    class FakeResearch:
        async def research(self, payload, media_summary=None):
            now = datetime.now(UTC)
            if research == "skipped":
                return PublicResearchResult(
                    status="skipped", provider="disabled", research_profile={}, queries=(),
                    candidates=(), selected=(), sources=(),
                    warnings=("Public research is disabled by server configuration.",),
                    started_at=now, completed_at=now,
                )
            candidate = PublicAdCandidate(
                reference_id="ref-deterministic", source_type="meta_ad_library",
                source_url="https://www.facebook.com/ads/library/?id=deterministic",
                source_domain="facebook.com", advertiser_name="Example Studio",
                ad_library_id="deterministic", first_seen_at=None, last_seen_at=None,
                is_active=True, countries=("US",), languages=("en",),
                creative_type="image", campaign_objective="OUTCOME_TRAFFIC",
                primary_text="Survive the next wave", headline="Beat my record",
                hook="Survival challenge", message_angles=("survival challenge",),
                cta="PLAY_NOW", landing_page_url="https://example.com/game",
                landing_page_goal="game install", image_urls=(), video_url=None,
                collected_at=now, content_hash="c" * 64, similarity_score=0.87,
                similarity_dimensions={"product_or_industry": 1.0},
                performance_evidence={
                    "type": "public_proxy_signals", "verified": False,
                    "confidence": "medium", "signals": ["currently_active"],
                    "limitations": ["真实CTR、CPC和CPA不可用", "真实购买量、收入和ROAS不可用"],
                },
                creative_patterns={"hook": "Survival challenge", "message_angle": "challenge"},
                applicable_learnings=("Show the challenge immediately.",), raw_excerpt={},
            )
            selected = tuple(
                replace(candidate, reference_id=f"ref-deterministic-{index}")
                for index in range(1, 4)
            )
            return PublicResearchResult(
                status="succeeded", provider="fake", research_profile={}, queries=("game ad",),
                candidates=selected, selected=selected, sources=(), warnings=(),
                started_at=now, completed_at=now,
            )

    async def create_pair():
        async with api_runtime.session_factory() as session:
            creation = await ExternalAdPerformanceAnalysisService().create_job(
                session, ExternalAdPerformanceAnalysisCreate.model_validate(payload)
            )
            return creation.task.id, creation.analysis.id

    task_id, analysis_record_id = asyncio.run(create_pair())
    real_orchestrator = AdAnalysisOrchestrator(
        media_service=FakeMedia(), research_service=FakeResearch(),
        rule_builder=build_facebook_rule_analysis, llm_provider=MockLLMProvider(),
    )
    monkeypatch = pytest.MonkeyPatch()
    # GenerationTaskService owns this module-local session factory; patch it before
    # process_task() so the worker lifecycle reads/writes the fixture's file-backed DB.
    monkeypatch.setattr(
        generation_task_service, "AsyncSessionLocal", api_runtime.session_factory
    )
    # Task 5 lazily imports this class inside its ad-analysis handler. Patch that exact
    # module attribute before process_task() so the real lifecycle uses our fakes.
    monkeypatch.setattr(
        ad_analysis_orchestrator,
        "AdAnalysisOrchestrator",
        lambda: real_orchestrator,
    )
    try:
        asyncio.run(GenerationTaskService().process_task(task_id))
        async def read_result():
            async with api_runtime.session_factory() as session:
                analysis = await session.get(AdPerformanceAnalysis, analysis_record_id)
                assert analysis is not None and analysis.status == "succeeded"
                return analysis.analysis_result
        return asyncio.run(read_result())
    finally:
        monkeypatch.undo()
```

`MockLLMProvider.analyze_facebook_ad()` must return a valid
`FacebookAdLLMContribution`, not an already assembled result. Task 13 must expose the
dependency-injection constructor used above:

```python
class AdAnalysisOrchestrator:
    def __init__(
        self,
        *,
        media_service: AdAnalysisMediaService | None = None,
        research_service: AdAnalysisResearchService | None = None,
        llm_provider: LLMProvider | None = None,
        rule_builder: Callable[[dict[str, Any]], FacebookRuleAnalysis] = build_facebook_rule_analysis,
    ) -> None: ...
```

The no-argument form remains the production default required by Task 5. Update every
invocation above to pass `api_runtime`; for example:

```python
completed = _run_deterministic_task(
    api_runtime, media_error="VIDEO_DURATION_LIMIT", research="skipped"
)
```

- [ ] **Step 2: Run the integration tests and confirm documentation/example are absent**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_external_ad_performance_integration.py -q
```

Expected: collection or example-loading fails because `docs/examples/external-facebook-ad-analysis-request.json` does not exist.

- [ ] **Step 3: Create a repository-safe valid caller example and pin the approved sample hash**

Create `docs/examples/external-facebook-ad-analysis-request.json` as valid UTF-8 JSON with:

```json
{
  "external_request_id": "replace-with-a-globally-unique-request-id",
  "campaign": {
    "facebook_campaign_id": "example-campaign-id",
    "name": "Slash the Hordes US Traffic",
    "objective": "OUTCOME_TRAFFIC"
  },
  "adset": {
    "facebook_adset_id": "example-adset-id",
    "name": "US Mobile Link Clicks",
    "optimization_goal": "LINK_CLICKS",
    "targeting": {"geo_locations": {"countries": ["US"]}}
  },
  "creative": {
    "facebook_ad_id": "example-ad-id",
    "creative_type": "image",
    "primary_text": "Can you survive the next wave?",
    "headline": "Beat my record",
    "cta": "PLAY_NOW",
    "link": "https://example.com/game",
    "image_url": "https://newpixel.messrocts.com/uploads/example-ad.jpg"
  },
  "insight": {
    "date_start": "2026-07-12",
    "date_stop": "2026-07-12",
    "account_currency": "USD",
    "spend": "0.24",
    "impressions": "1079",
    "reach": "1000",
    "clicks": "120",
    "inline_link_clicks": "86",
    "ctr": "7.970343",
    "actions": [
      {"action_type": "landing_page_view", "value": "23"}
    ]
  },
  "siblings": [],
  "metadata_json": {"source": "external-facebook-ads-system"}
}
```

Use placeholder IDs and URLs only; do not copy secrets or personal data from the downloaded sample. Keep exactly one of `image_url`/`video_url` in this example.

In the test, load the authoritative downloaded sample, replace/add only `external_request_id`, validate it through Task 1, and assert its canonical idempotency hash is exactly:

```text
a6826e07143a295a46b43b51d5dca838f28c434e6a63df73472a4de65771abf7
```

Keep all three hash meanings explicit in comments/documentation:

```text
0691b999c66db8f94340f95657aa10e97cee95758e6a150500e4b2672dabda29
= Task 1 synthetic fixed vector, including the Pydantic default metadata_json={}

a6826e07143a295a46b43b51d5dca838f28c434e6a63df73472a4de65771abf7
= canonical SHA-256 of the approved sample after the exact Task 1 normalizer

fba7a1c218b96adeaaf57246873117829ea23f29b4aa8da43dd6e01239591e56
= raw UTF-8 file-byte SHA-256 of the authoritative downloaded sample
```

The canonical normalized approved sample is 4,238 UTF-8 bytes. Never use the raw file hash as the API idempotency `payload_hash`.

- [ ] **Step 4: Write the external API guide with exact responsibilities and envelopes**

Create `docs/EXTERNAL_FACEBOOK_AD_ANALYSIS_API.md` in Chinese. Document:

```http
POST https://ai.ggcss.xyz/api/v1/integrations/ad-performance/analysis-jobs
GET  https://ai.ggcss.xyz/api/v1/integrations/ad-performance/analysis-jobs/{analysis_id}
```

State plainly:

- `external_request_id` is created by the external system, must be globally unique, and is used only for POST idempotency;
- `analysis_id` is created by this system and is the only polling key;
- there is no GET-by-`external_request_id`, callback, cancellation API, `external_account_id`, or caller-supplied research configuration in v1;
- poll every 3–5 seconds initially and back off to 10 seconds for long jobs; stop when status is `succeeded` or `failed`;
- use `Authorization: Bearer <shared-access-token>` and never place a real token in the document;
- same-server media still uses a full HTTPS URL such as `https://newpixel.messrocts.com/uploads/...`;
- caller sends only one original `creative.image_url` or `creative.video_url`; the AI system downloads it and internally generates thumbnail/keyframes;
- accepted video containers are MP4/MOV/WebM, maximum 20 seconds and 100 MB, with MP4 H.264 preferred;
- media failure can degrade to metrics/copy analysis rather than failing the job;
- public research is automatic, server-configured, and may be disabled/unavailable without changing the request;
- public “similar high-performing ads” means observable proxy signals, never another advertiser's fabricated CTR, CPC, CPA, purchases, revenue, or ROAS;
- missing purchase/currency remains unavailable and is never converted to zero/USD.

Include complete request, 202 creation, 200 replay, 409 conflict, queued/processing/succeeded/failed GET, 401, 404, and 422 envelope examples. Explain all `code/message/data`, `status`, `stage`, `progress`, `result`, and `error` fields. State that the existing synchronous `POST /api/v1/integrations/ad-performance/analyses` still exists but is not this new polling protocol.

- [ ] **Step 5: Run integration, documentation consistency, full queue regressions, and lint**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_external_ad_performance_integration.py tests/test_external_ad_performance_api.py tests/test_external_ad_performance_jobs.py tests/test_generation_tasks.py tests/test_ad_performance_analysis.py -q
.venv\Scripts\python.exe -m ruff check tests/test_external_ad_performance_integration.py
.venv\Scripts\python.exe -c "import json, pathlib; json.loads(pathlib.Path('docs/examples/external-facebook-ad-analysis-request.json').read_text(encoding='utf-8')); print('example json ok')"
```

Expected: create/replay/conflict/poll/auth/database/error/media/research/schema and all existing queue/synchronous regressions pass; example JSON parses; Ruff passes.

- [ ] **Step 6: Commit only Task 15 files**

```powershell
git add tests/test_external_ad_performance_integration.py docs/EXTERNAL_FACEBOOK_AD_ANALYSIS_API.md docs/examples/external-facebook-ad-analysis-request.json
git diff --cached --check
git commit -m "docs: publish external facebook analysis api"
```

### Task 16: Production-Like Docker Verification Script and Release Gate

**Files:**
- Create: `scripts/verify_external_ad_performance_analysis.ps1`

**Interfaces:**
- Consumes: `.env.production`, production Compose topology, the approved downloaded sample, Bearer token, and Tasks 1–15 endpoints/results.
- Produces: a safe repeatable local production-like verification that never destroys volumes and exits nonzero on API, worker, media, schema, deterministic-result, regression, build, Compose, or log failure.

- [ ] **Step 1: Create the complete safe, cross-version PowerShell verification script**

Create the full file `scripts/verify_external_ad_performance_analysis.ps1`; do not
leave helper behavior as prose. It must work under Windows PowerShell 5.1 and modern
PowerShell 7+. In particular, do **not** use `-SkipHttpErrorCheck` until the script
has verified the parameter exists.

```powershell
[CmdletBinding()]
param(
    [string]$EnvFile = ".env.production",
    [string]$SamplePath = "C:\Users\panda\Downloads\Telegram Desktop\广告数据分析包.json",
    [int]$PollIntervalSeconds = 3,
    [int]$PollTimeoutSeconds = 300,
    [string]$TestServerHealthUrl = "https://ai.ggcss.xyz/api/v1/health/live",
    [switch]$VerifyTestServerHealth
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

function Invoke-CheckedNative {
    param([string]$Description, [scriptblock]$Command)
    # A PowerShell-only command leaves LASTEXITCODE untouched, so clear it before
    # every gate and capture it immediately after the command completes.
    $global:LASTEXITCODE = 0
    & $Command
    $exitCode = $global:LASTEXITCODE
    if ($null -ne $exitCode -and $exitCode -ne 0) {
        throw "$Description failed with exit code $exitCode"
    }
}

function Read-DotEnvValue {
    param([string]$Path, [string]$Name)
    $line = Get-Content -LiteralPath $Path -Encoding UTF8 |
        Where-Object { $_ -match "^$([regex]::Escape($Name))=" } |
        Select-Object -Last 1
    if (-not $line) { return $null }
    return ($line -split "=", 2)[1].Trim().Trim('"').Trim("'")
}

function ConvertFrom-JsonCompat {
    param([string]$Text)
    $command = Get-Command ConvertFrom-Json
    if ($command.Parameters.ContainsKey("Depth")) {
        return $Text | ConvertFrom-Json -Depth 100
    }
    return $Text | ConvertFrom-Json
}

function ConvertFrom-HttpJson {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) { return $null }
    try { return ConvertFrom-JsonCompat $Text } catch { return $null }
}

function Invoke-JsonHttp {
    param(
        [ValidateSet("GET", "POST")][string]$Method,
        [string]$Uri,
        [hashtable]$Headers = @{},
        [string]$Body
    )
    $request = @{
        Method = $Method
        Uri = $Uri
        Headers = $Headers
        UseBasicParsing = $true
    }
    if ($PSBoundParameters.ContainsKey("Body")) {
        $request["Body"] = $Body
        $request["ContentType"] = "application/json"
    }
    $rawBody = ""
    $statusCode = $null
    try {
        if ((Get-Command Invoke-WebRequest).Parameters.ContainsKey("SkipHttpErrorCheck")) {
            $response = Invoke-WebRequest @request -SkipHttpErrorCheck
            $statusCode = [int]$response.StatusCode
            $rawBody = [string]$response.Content
        } else {
            $response = Invoke-WebRequest @request
            $statusCode = [int]$response.StatusCode
            $rawBody = [string]$response.Content
        }
    } catch [System.Net.WebException] {
        $exception = $_.Exception
        if ($null -eq $exception.Response) { throw }
        $statusCode = [int]$exception.Response.StatusCode
        $stream = $exception.Response.GetResponseStream()
        $reader = [System.IO.StreamReader]::new($stream)
        try { $rawBody = $reader.ReadToEnd() } finally {
            $reader.Dispose()
            $stream.Dispose()
            $exception.Response.Dispose()
        }
    }
    $jsonBody = ConvertFrom-HttpJson $rawBody
    if ($null -ne $jsonBody) {
        return [PSCustomObject]@{ StatusCode = [int]$statusCode; Body = $jsonBody; RawBody = $null }
    }
    return [PSCustomObject]@{ StatusCode = [int]$statusCode; Body = $null; RawBody = $rawBody }
}

function Assert-HttpJson {
    param(
        [PSCustomObject]$Response,
        [int]$ExpectedStatus,
        [string]$Description
    )
    Assert-True ($Response.StatusCode -eq $ExpectedStatus) (
        "$Description expected HTTP $ExpectedStatus but received $($Response.StatusCode)"
    )
    Assert-True ($null -ne $Response.Body) "$Description returned non-JSON content"
}

function Get-ObjectPropertyValue {
    param([object]$Object, [string]$Name)
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Assert-NoForbiddenPublicMetrics {
    param([object]$Value)
    $forbidden = @("ctr", "cpc", "cpa", "purchase", "purchases", "revenue", "roas", "spend")
    if ($null -eq $Value) { return }
    if ($Value -is [System.Collections.IEnumerable] -and $Value -isnot [string] -and $Value -isnot [pscustomobject]) {
        foreach ($item in $Value) { Assert-NoForbiddenPublicMetrics $item }
        return
    }
    foreach ($property in $Value.PSObject.Properties) {
        Assert-True (-not ($forbidden -contains $property.Name.ToLowerInvariant())) (
            "Public reference exposes forbidden metric '$($property.Name)'"
        )
        Assert-NoForbiddenPublicMetrics $property.Value
    }
}

Assert-True (Test-Path -LiteralPath $EnvFile) "Environment file not found: $EnvFile"
Assert-True (Test-Path -LiteralPath $SamplePath) "Approved sample not found: $SamplePath"
Assert-True ($PollIntervalSeconds -ge 1) "PollIntervalSeconds must be at least 1"
Assert-True ($PollTimeoutSeconds -ge $PollIntervalSeconds) "Poll timeout must exceed interval"
$token = Read-DotEnvValue -Path $EnvFile -Name "AI_ADS_ACCESS_TOKEN"
Assert-True (-not [string]::IsNullOrWhiteSpace($token)) "AI_ADS_ACCESS_TOKEN is missing"
$headers = @{ Authorization = "Bearer $token" }
$localBaseUrl = "http://127.0.0.1"
$localHealthUrl = "http://127.0.0.1/api/v1/health/live"
$jobsUrl = "$localBaseUrl/api/v1/integrations/ad-performance/analysis-jobs"
Write-Host "Loaded access-token presence from the selected env file; value remains hidden."
```

Never output `$token`, `$headers`, the full env file, API keys, Authorization values,
or full HTTP response headers. `Invoke-JsonHttp` returns a `PSCustomObject` with a
numeric `StatusCode`, parsed JSON `Body`, and `RawBody` only when JSON parsing fails.

- [ ] **Step 2: Add non-destructive Compose startup, nginx health, worker, and binary checks**

Append this exact operational sequence. It starts or rebuilds the local
production-like stack but never destroys Docker volumes, resets the database, or
deletes files. Health verification must traverse nginx at port 80, not backend port
8001.

```powershell
Invoke-CheckedNative "Production-like Compose startup" {
    docker compose -f docker-compose.prod.yml --env-file $EnvFile up -d --build
}
Invoke-CheckedNative "Production-like Compose status" {
    docker compose -f docker-compose.prod.yml --env-file $EnvFile ps
}

$healthDeadline = [DateTimeOffset]::UtcNow.AddSeconds(180)
$health = $null
do {
    try {
        $health = Invoke-JsonHttp -Method GET -Uri $localHealthUrl
        if ($health.StatusCode -eq 200 -and $null -ne $health.Body -and $health.Body.status -eq "ok") {
            break
        }
    } catch {
        # Nginx may accept no connection while the Compose stack is still starting.
        Write-Host "Local nginx health endpoint is not ready; retrying."
    }
    Start-Sleep -Seconds 3
} while ([DateTimeOffset]::UtcNow -lt $healthDeadline)
Assert-True ($null -ne $health) "Local nginx health endpoint never became reachable"
Assert-HttpJson $health 200 "Local nginx health endpoint"
Assert-True ($health.Body.status -eq "ok") "Local nginx health body must be {\"status\":\"ok\"}"

Invoke-CheckedNative "Dedicated analysis worker running check" {
    docker compose -f docker-compose.prod.yml --env-file $EnvFile ps --status running worker_ad_analysis
}
Invoke-CheckedNative "FFmpeg availability" {
    docker compose -f docker-compose.prod.yml --env-file $EnvFile exec -T worker_ad_analysis ffmpeg -version
}
Invoke-CheckedNative "FFprobe availability" {
    docker compose -f docker-compose.prod.yml --env-file $EnvFile exec -T worker_ad_analysis ffprobe -version
}
```

Do not put `down -v`, `docker volume rm`, `docker system prune`, destructive file
deletion, or database reset commands anywhere in the script.

- [ ] **Step 3: Add the approved-sample POST/replay/conflict/poll flow**

Append this flow. It uses `external_request_id` only for POST idempotency and
`analysis_id` only for polling. It never constructs or calls a GET-by-external-request
URL.

```powershell
$payload = ConvertFrom-JsonCompat (Get-Content -Raw -LiteralPath $SamplePath -Encoding UTF8)
$externalRequestId = "codex-verify-$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())-$([guid]::NewGuid().ToString('N'))"
$payload | Add-Member -NotePropertyName external_request_id -NotePropertyValue $externalRequestId -Force
foreach ($serverGeneratedField in @("thumbnail_url", "video_keyframes")) {
    if ($payload.creative.PSObject.Properties.Name -contains $serverGeneratedField) {
        $payload.creative.PSObject.Properties.Remove($serverGeneratedField)
    }
}
foreach ($unsupportedField in @("callback_url", "external_account_id", "research_context", "search_keywords", "competitor_names")) {
    if ($payload.PSObject.Properties.Name -contains $unsupportedField) {
        $payload.PSObject.Properties.Remove($unsupportedField)
    }
}
$requestBody = $payload | ConvertTo-Json -Depth 100 -Compress

$created = Invoke-JsonHttp -Method POST -Uri $jobsUrl -Headers $headers -Body $requestBody
Assert-HttpJson $created 202 "Async analysis creation"
Assert-True ($created.Body.code -eq 1001) "New job must use code=1001"
$analysisId = [string]$created.Body.data.analysis_id
Assert-True (-not [string]::IsNullOrWhiteSpace($analysisId)) "Creation response lacks analysis_id"

$replayed = Invoke-JsonHttp -Method POST -Uri $jobsUrl -Headers $headers -Body $requestBody
Assert-HttpJson $replayed 200 "Idempotent replay"
Assert-True ($replayed.Body.code -eq 0) "Replay must use code=0"
Assert-True ($replayed.Body.data.analysis_id -eq $analysisId) "Replay returned a different analysis_id"

$conflictPayload = ConvertFrom-JsonCompat ($payload | ConvertTo-Json -Depth 100)
$conflictPayload.insight.spend = "9.99"
$conflictBody = $conflictPayload | ConvertTo-Json -Depth 100 -Compress
$conflict = Invoke-JsonHttp -Method POST -Uri $jobsUrl -Headers $headers -Body $conflictBody
Assert-HttpJson $conflict 409 "Idempotency conflict"
Assert-True ($conflict.Body.code -eq 4001) "Conflict must use code=4001"
Assert-True ($conflict.Body.data.error_code -eq "IDEMPOTENCY_CONFLICT") "Conflict error code drifted"
Assert-True ($conflict.Body.data.analysis_id -eq $analysisId) "Conflict must name the original analysis_id"

$pollDeadline = [DateTimeOffset]::UtcNow.AddSeconds($PollTimeoutSeconds)
do {
    $poll = Invoke-JsonHttp -Method GET -Uri "$jobsUrl/$analysisId" -Headers $headers
    Assert-HttpJson $poll 200 "Analysis polling"
    $status = [string]$poll.Body.data.status
    if ($status -in @("succeeded", "failed")) { break }
    Start-Sleep -Seconds $PollIntervalSeconds
} while ([DateTimeOffset]::UtcNow -lt $pollDeadline)
Assert-True ($status -in @("succeeded", "failed")) "Analysis did not reach a terminal state before timeout"
if ($status -eq "failed") {
    throw "Analysis failed: $($poll.Body.data.error.error_code) $($poll.Body.data.error.message)"
}
$result = $poll.Body.data.result
Assert-True ($null -ne $result) "Succeeded job has no result"
```

- [ ] **Step 4: Add strict result, deterministic verdict, research-boundary, and synchronous-route checks**

Append the exact result assertions below. They verify the approved sample's stable
rules outcome and protect the no-fabricated-public-metrics boundary. Keep the missing
purchase and currency expectations as unavailable/null, never implicit zero/USD.

```powershell
$topLevelKeys = @(
    "schema_version", "platform", "executive_summary", "objective_alignment",
    "performance_funnel", "diagnoses", "creative_analysis", "audience_and_delivery_analysis",
    "market_intelligence", "benchmark_comparison", "recommended_actions", "experiment_plan",
    "data_quality", "analysis_metadata"
)
Assert-True (
    ((@($result.PSObject.Properties.Name | Sort-Object) -join ",") -eq
     (($topLevelKeys | Sort-Object) -join ","))
) "Result top-level schema drifted"
Assert-True ($result.schema_version -eq "facebook_ad_analysis_v1") "Unexpected schema version"
Assert-True ($result.platform -eq "facebook") "Unexpected platform"
Assert-True ($result.executive_summary.verdict -eq "optimize") "Unexpected verdict"
Assert-True ($result.executive_summary.primary_bottleneck -eq "landing_page") "Unexpected primary bottleneck"
Assert-True ($result.executive_summary.confidence -eq "medium") "Unexpected confidence"
Assert-True ($result.executive_summary.scale_eligibility -eq "not_ready") "Unexpected scale eligibility"
Assert-True ($result.executive_summary.pause_recommended -eq $false) "Small sample must not recommend pause"
$lpvRate = [double]$result.performance_funnel.landing_page.metrics.landing_page_view_rate.value
Assert-True ([math]::Abs($lpvRate - 26.744186) -lt 0.01) "Unexpected LPV rate: $lpvRate"
$purchase = $result.performance_funnel.conversion.metrics.purchase
Assert-True ($null -eq $purchase.value -and $purchase.assessment -eq "unavailable") "Missing purchase was fabricated"
$spendCurrency = $result.performance_funnel.delivery.metrics.spend.currency
$payloadInsight = Get-ObjectPropertyValue $payload "insight"
$insightCurrency = Get-ObjectPropertyValue $payloadInsight "account_currency"
$payloadCurrency = Get-ObjectPropertyValue $payload "account_currency"
if ($null -eq $insightCurrency -and $null -eq $payloadCurrency) {
    Assert-True ($null -eq $spendCurrency) "Missing currency was defaulted"
}

foreach ($reference in @($result.market_intelligence.selected_reference_ads)) {
    Assert-True ($reference.source_url -and $reference.collected_at -and $null -ne $reference.similarity_score) "Incomplete public reference"
    Assert-True ($reference.performance_evidence.type -and $reference.performance_evidence.confidence) "Reference lacks evidence type/confidence"
    Assert-True (@($reference.performance_evidence.limitations).Count -gt 0) "Reference lacks limitations"
    Assert-True ($reference.performance_evidence.verified -eq $false) "Public proxy evidence cannot be verified"
    Assert-NoForbiddenPublicMetrics $reference
}

$researchEnabled = (Read-DotEnvValue -Path $EnvFile -Name "PUBLIC_RESEARCH_ENABLED")
if ($researchEnabled -match "^(?i:true|1|yes)$") {
    Write-Host "Configured public research is enabled; skipped disabled-provider-only assertion."
} else {
    Assert-True ($result.market_intelligence.status -eq "skipped") "Disabled research must be reported as skipped"
    Assert-True (@($result.analysis_metadata.warnings | Where-Object { $_ -match "(?i)public research" }).Count -gt 0) "Missing explicit public-research warning"
}

$syncPayload = ConvertFrom-JsonCompat ($payload | ConvertTo-Json -Depth 100)
$syncPayload.PSObject.Properties.Remove("external_request_id")
$sync = Invoke-JsonHttp -Method POST -Uri "$localBaseUrl/api/v1/integrations/ad-performance/analyses" -Headers $headers -Body ($syncPayload | ConvertTo-Json -Depth 100 -Compress)
Assert-HttpJson $sync 201 "Existing synchronous analysis route"
Assert-True ($null -ne $sync.Body.analysis_id) "Synchronous response lacks analysis_id"
Assert-True ($null -eq $sync.Body.code) "Synchronous response was incorrectly envelope-wrapped"
```

- [ ] **Step 5: Add focused/full tests, lint, frontend, Compose, and safe log gates**

Finish the script with executable gates. A test or build failure stops the script. The
log scan is intentionally narrow and allows expected disabled-research degradations;
it does not print env values or token-bearing commands.

```powershell
Invoke-CheckedNative "Focused external-analysis tests" {
    .venv\Scripts\python.exe -m pytest tests/test_external_ad_performance_integration.py tests/test_ad_analysis_orchestrator.py tests/test_ad_analysis_runtime_config.py -q
}
Invoke-CheckedNative "Full backend tests" { .venv\Scripts\python.exe -m pytest -q }
Invoke-CheckedNative "Backend lint" { .venv\Scripts\python.exe -m ruff check backend tests }
Push-Location frontend\web-admin
try {
    Invoke-CheckedNative "Frontend production build" { npm run build }
} finally {
    Pop-Location
}
Invoke-CheckedNative "Compose render" {
    docker compose -f docker-compose.prod.yml --env-file $EnvFile config --quiet
}
Invoke-CheckedNative "Final Compose status" {
    docker compose -f docker-compose.prod.yml --env-file $EnvFile ps
}
$global:LASTEXITCODE = 0
$composePs = @(docker compose -f docker-compose.prod.yml --env-file $EnvFile ps --format json)
$composeExitCode = $global:LASTEXITCODE
Assert-True ($composeExitCode -eq 0) "Could not read Compose status"
$composeLines = @($composePs | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
Assert-True ($composeLines.Count -gt 0) "Compose status did not return any services"
if ($composeLines[0].TrimStart().StartsWith("[")) {
    $composeServices = @(ConvertFrom-JsonCompat ($composeLines -join [Environment]::NewLine))
} else {
    $composeServices = @($composeLines | ForEach-Object { ConvertFrom-JsonCompat ([string]$_) })
}
$global:LASTEXITCODE = 0
$expectedServices = @(docker compose -f docker-compose.prod.yml --env-file $EnvFile config --services)
$expectedExitCode = $global:LASTEXITCODE
Assert-True ($expectedExitCode -eq 0) "Could not list expected Compose services"
foreach ($expectedService in $expectedServices) {
    $service = @($composeServices | Where-Object {
        (Get-ObjectPropertyValue $_ "Service") -eq $expectedService
    } | Select-Object -First 1)
    Assert-True ($service.Count -eq 1) "Compose status is missing expected service '$expectedService'"
    $state = [string](Get-ObjectPropertyValue $service[0] "State")
    $statusText = [string](Get-ObjectPropertyValue $service[0] "Status")
    Assert-True ($state -in @("running", "")) "Compose service '$expectedService' is not running (state=$state)"
    Assert-True ($statusText -notmatch '(?i)unhealthy|exited|dead') "Compose service '$expectedService' is unhealthy or exited"
}
Assert-True (($expectedServices -contains "worker_ad_analysis")) "Dedicated analysis worker is absent from rendered Compose services"
$global:LASTEXITCODE = 0
$logs = docker compose -f docker-compose.prod.yml --env-file $EnvFile logs --tail=200 backend worker_ad_analysis worker_beat
$logsExitCode = $global:LASTEXITCODE
Assert-True ($logsExitCode -eq 0) "Could not read recent Compose logs"
$unexpectedLogFailure = $logs | Where-Object {
    $_ -match '(?i)traceback|unhandled exception|migration failure|unknown queue|ffmpeg.*not found|ffprobe.*not found|ad.analysis.*(crash|fatal)' -and
    $_ -notmatch '(?i)public research.*(disabled|skipped|unavailable)'
}
Assert-True ($null -eq $unexpectedLogFailure) "Recent service logs include an unexpected critical failure"

if ($VerifyTestServerHealth) {
    $testServerHealth = Invoke-JsonHttp -Method GET -Uri $TestServerHealthUrl
    Assert-HttpJson $testServerHealth 200 "Test-server health endpoint"
    $exactHealthJson = $testServerHealth.Body | ConvertTo-Json -Compress
    Assert-True ($exactHealthJson -eq '{"status":"ok"}') "Test-server health body must equal {\"status\":\"ok\"}"
    Write-Host "Test-server health endpoint returned the required healthy body."
} else {
    Write-Host "Local verification passed. Test-server health remains unverified; this alone does not satisfy acceptance criterion 17."
}
```

- [ ] **Step 6: Parse, inspect, and execute the final script**

First run this parser/safety scan. It must find no parser errors and no destructive or
secret-output patterns:

```powershell
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
    (Resolve-Path 'scripts/verify_external_ad_performance_analysis.ps1'),
    [ref]$null,
    [ref]$errors
) | Out-Null
if ($errors.Count) { $errors | Format-List; exit 1 }
$safetyMatches = Select-String -LiteralPath scripts/verify_external_ad_performance_analysis.ps1 -Pattern 'down\s+-v|volume\s+rm|system\s+prune|Write-Host.*\$token|Write-Host.*\$headers|echo.*\$token' -CaseSensitive:$false
if ($safetyMatches) { $safetyMatches | Format-Table -AutoSize; exit 1 }
```

Then run both the local and optional test-server forms:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify_external_ad_performance_analysis.ps1
powershell -ExecutionPolicy Bypass -File scripts/verify_external_ad_performance_analysis.ps1 -VerifyTestServerHealth
```

Expected: the first command proves the local nginx path, worker/binaries,
202/200/409, analysis-id-only polling, strict schema, deterministic verdict, research
degradation, synchronous regression, focused/full Pytest, Ruff, frontend build,
Compose state, and log gates without resetting any volume. The second additionally
requires that the test server respond HTTP 200 with exactly `{"status":"ok"}`; it is
the part of acceptance standard 17 that local verification cannot establish.

- [ ] **Step 7: Commit only the verification script after all gates pass**

```powershell
git add scripts/verify_external_ad_performance_analysis.ps1
git diff --cached --check
git commit -m "test: verify external ad analysis stack"
```
