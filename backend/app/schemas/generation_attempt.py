from datetime import datetime

from pydantic import Field

from backend.app.db.models.generation_attempt import GenerationAttempt
from backend.app.schemas.common import TimestampedRead


class GenerationAttemptRead(TimestampedRead):
    business_type: str
    business_id: str
    job_id: str | None = None
    campaign_id: str | None = None
    stage: str
    status: str
    total_count: int
    success_count: int
    failed_count: int
    provider: str | None = None
    model: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool
    metadata: dict = Field(default_factory=dict)
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None

    @classmethod
    def from_model(cls, attempt: GenerationAttempt) -> "GenerationAttemptRead":
        return cls(
            id=attempt.id,
            created_at=attempt.created_at,
            updated_at=attempt.updated_at,
            business_type=attempt.business_type,
            business_id=attempt.business_id,
            job_id=attempt.job_id,
            campaign_id=attempt.campaign_id,
            stage=attempt.stage,
            status=attempt.status,
            total_count=attempt.total_count,
            success_count=attempt.success_count,
            failed_count=attempt.failed_count,
            provider=attempt.provider,
            model=attempt.model,
            error_code=attempt.error_code,
            error_message=attempt.error_message,
            retryable=attempt.retryable,
            metadata=attempt.metadata_json or {},
            started_at=attempt.started_at,
            finished_at=attempt.finished_at,
            duration_ms=attempt.duration_ms,
        )
