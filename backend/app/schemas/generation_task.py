from datetime import datetime

from pydantic import BaseModel, Field

from backend.app.db.models.generation_task import GenerationTask
from backend.app.schemas.common import TimestampedRead
from backend.app.services.generation_task_service import GenerationTaskListResult


class GenerationTaskRead(TimestampedRead):
    queue_name: str
    task_type: str
    business_type: str
    business_id: str
    campaign_id: str | None = None
    status: str
    priority: int
    payload: dict = Field(default_factory=dict)
    result: dict | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool
    attempt_count: int
    max_attempts: int
    queued_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    metadata: dict = Field(default_factory=dict)

    @classmethod
    def from_model(cls, task: GenerationTask) -> "GenerationTaskRead":
        return cls(
            id=task.id,
            created_at=task.created_at,
            updated_at=task.updated_at,
            queue_name=task.queue_name,
            task_type=task.task_type,
            business_type=task.business_type,
            business_id=task.business_id,
            campaign_id=task.campaign_id,
            status=task.status,
            priority=task.priority,
            payload=task.payload_json or {},
            result=task.result_json,
            error_code=task.error_code,
            error_message=task.error_message,
            retryable=task.retryable,
            attempt_count=task.attempt_count,
            max_attempts=task.max_attempts,
            queued_at=task.queued_at,
            started_at=task.started_at,
            finished_at=task.finished_at,
            duration_ms=task.duration_ms,
            metadata=task.metadata_json or {},
        )


class GenerationTaskListResponse(BaseModel):
    items: list[GenerationTaskRead]
    total: int
    limit: int
    offset: int
    summary: dict = Field(default_factory=dict)

    @classmethod
    def from_result(cls, result: GenerationTaskListResult) -> "GenerationTaskListResponse":
        return cls(
            items=[GenerationTaskRead.from_model(task) for task in result.items],
            total=result.total,
            limit=result.limit,
            offset=result.offset,
            summary=result.summary,
        )
