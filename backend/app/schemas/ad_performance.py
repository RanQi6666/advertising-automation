from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.schemas.common import TimestampedRead

AnalysisConfidence = Literal["low", "medium", "high"]
AnalysisSeverity = Literal["info", "warning", "critical"]
AnalysisPriority = Literal["low", "medium", "high"]


class AdPerformanceAnalysisCreate(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_type: str = "unknown"
    external_user_id: str | None = None
    date_preset: str | None = None
    date_start: str | None = None
    date_stop: str | None = None
    campaign: dict[str, Any] = Field(default_factory=dict)
    adset: dict[str, Any] = Field(default_factory=dict)
    creative: dict[str, Any] = Field(default_factory=dict)
    insight: dict[str, Any] = Field(default_factory=dict)
    siblings: list[dict[str, Any]] = Field(default_factory=list)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class AdPerformanceProblem(BaseModel):
    code: str
    severity: AnalysisSeverity
    area: str
    title: str
    evidence: list[str] = Field(default_factory=list)
    diagnosis: str


class AdPerformanceRecommendation(BaseModel):
    code: str
    priority: AnalysisPriority
    area: str
    action: str
    detail: str


class AdPerformanceAnalysisResult(BaseModel):
    summary: str
    confidence: AnalysisConfidence
    problems: list[AdPerformanceProblem] = Field(default_factory=list)
    recommendations: list[AdPerformanceRecommendation] = Field(default_factory=list)
    next_checks: list[str] = Field(default_factory=list)


class AdPerformanceAnalysisRead(TimestampedRead):
    analysis_id: str
    external_user_id: str | None = None
    source_type: str
    status: str
    campaign_external_id: str | None = None
    campaign_name: str | None = None
    adset_external_id: str | None = None
    adset_name: str | None = None
    creative_external_id: str | None = None
    creative_name: str | None = None
    date_start: str | None = None
    date_stop: str | None = None
    request_payload: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    analysis_result: AdPerformanceAnalysisResult
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
