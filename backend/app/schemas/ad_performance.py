from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.schemas.common import TimestampedRead

AnalysisConfidence = Literal["low", "medium", "high"]
AnalysisSeverity = Literal["info", "warning", "critical"]
AnalysisPriority = Literal["low", "medium", "high"]
AnalysisMode = Literal[
    "llm_only",
    "llm_failed",
    "rules_only",
    "rules_and_llm",
    "rules_with_llm_fallback",
]
DataCompletenessLevel = Literal["unknown", "low", "medium", "high"]
OptimizationAction = Literal[
    "keep",
    "regenerate",
    "rewrite",
    "check",
    "watch",
    "reduce",
    "increase",
    "pause",
    "create_draft",
    "missing",
]
OptimizationSource = Literal["rules", "ai", "rules_and_ai"]


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


class AdPerformanceVisualAnalysis(BaseModel):
    summary: str | None = None
    observed_elements: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    source_image_url: str | None = None
    source_video_url: str | None = None
    confidence_note: str | None = None


class AdPerformanceDataCompleteness(BaseModel):
    level: DataCompletenessLevel = "unknown"
    score: int = Field(default=0, ge=0, le=100)
    available: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    can_analyze: list[str] = Field(default_factory=list)
    cannot_analyze: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AdPerformanceOptimizationFieldAdvice(BaseModel):
    field: str
    label: str
    current_value: Any | None = None
    action: OptimizationAction
    priority: AnalysisPriority = "medium"
    suggested_value: Any | None = None
    suggested_direction: str | None = None
    generation_prompt: str | None = None
    reason: str
    source: OptimizationSource = "ai"
    can_apply_to_generation: bool = False
    missing: bool = False


class AdPerformanceOptimizationWorkOrder(BaseModel):
    schema_version: str = "ad_performance_optimization_work_order_v1"
    operator_summary: str = ""
    priority: AnalysisPriority = "medium"
    overall_action: str = "review"
    next_step: str | None = None
    modules_to_change: list[str] = Field(default_factory=list)
    modules_to_keep: list[str] = Field(default_factory=list)
    modules_to_watch: list[str] = Field(default_factory=list)
    campaign: list[AdPerformanceOptimizationFieldAdvice] = Field(default_factory=list)
    adset: list[AdPerformanceOptimizationFieldAdvice] = Field(default_factory=list)
    creative: list[AdPerformanceOptimizationFieldAdvice] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AdPerformanceAIAnalysis(BaseModel):
    summary: str
    root_causes: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    next_tests: list[str] = Field(default_factory=list)
    creative_feedback: list[str] = Field(default_factory=list)
    audience_feedback: list[str] = Field(default_factory=list)
    landing_page_feedback: list[str] = Field(default_factory=list)
    budget_delivery_feedback: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    visual_analysis: AdPerformanceVisualAnalysis | None = None
    optimization_work_order: AdPerformanceOptimizationWorkOrder | None = None
    confidence_note: str | None = None


class AdPerformanceAnalysisResult(BaseModel):
    summary: str
    confidence: AnalysisConfidence
    analysis_mode: AnalysisMode = "rules_only"
    data_completeness: AdPerformanceDataCompleteness = Field(
        default_factory=AdPerformanceDataCompleteness
    )
    optimization_work_order: AdPerformanceOptimizationWorkOrder = Field(
        default_factory=AdPerformanceOptimizationWorkOrder
    )
    problems: list[AdPerformanceProblem] = Field(default_factory=list)
    recommendations: list[AdPerformanceRecommendation] = Field(default_factory=list)
    next_checks: list[str] = Field(default_factory=list)
    ai_analysis: AdPerformanceAIAnalysis | None = None
    rule_summary: str | None = None
    llm_error: str | None = None


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
