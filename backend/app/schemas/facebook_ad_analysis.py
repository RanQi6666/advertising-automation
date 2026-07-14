from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FlexibleSection(BaseModel):
    """A bounded-but-forward-compatible nested section.

    The top-level result contract is strict, while several analysis subtrees need to
    preserve provider/model annotations without forcing a migration for every new
    insight field.
    """

    model_config = ConfigDict(extra="allow")


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    source: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    metric: str | None = None
    value: Any | None = None
    formula: str | None = None


class RecommendedAction(BaseModel):
    model_config = ConfigDict(extra="allow")

    action_id: str = Field(min_length=1)
    priority: int = Field(ge=1, le=10)
    category: str = Field(min_length=1)
    action: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    evidence: list[EvidenceItem] = Field(min_length=1)
    success_metric: str = Field(min_length=1)
    target_direction: Literal["increase", "decrease", "maintain", "verify"]


class PerformanceEvidence(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str = Field(min_length=1)
    verified: bool = False
    confidence: Literal["low", "medium", "high", "unknown"] = "unknown"
    signals: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def public_proxy_signals_are_never_verified(self) -> PerformanceEvidence:
        if self.type == "public_proxy_signals":
            self.verified = False
            if not self.limitations:
                self.limitations = [
                    "Public sources can show creative/proxy signals, but cannot verify "
                    "Meta delivery metrics."
                ]
        return self


class ReferenceAd(BaseModel):
    model_config = ConfigDict(extra="allow")

    reference_id: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    advertiser_name: str | None = None
    collected_at: str = Field(min_length=1)
    similarity_score: float = Field(ge=0, le=1)
    performance_evidence: PerformanceEvidence
    creative_patterns: dict[str, Any] = Field(default_factory=dict)
    applicable_learnings: list[Any] = Field(default_factory=list)


class MarketIntelligence(FlexibleSection):
    status: str = Field(default="skipped")
    selected_reference_ads: list[ReferenceAd] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ExecutiveSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    verdict: Literal["scale", "optimize", "monitor", "pause"]
    priority: Literal["low", "medium", "high", "critical"]
    primary_bottleneck: str
    confidence: Literal["low", "medium", "high"]
    scale_eligibility: Literal["ready", "review", "not_ready"]
    pause_recommended: bool
    key_findings: list[str] = Field(default_factory=list)


class FacebookAdAnalysisResult(BaseModel):
    """Strict public result returned by the async Facebook ad analysis API."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["facebook_ad_analysis_v1"]
    platform: Literal["facebook"]
    executive_summary: ExecutiveSummary
    objective_alignment: dict[str, Any]
    performance_funnel: dict[str, Any]
    diagnoses: list[dict[str, Any]]
    creative_analysis: dict[str, Any]
    audience_and_delivery_analysis: dict[str, Any]
    market_intelligence: MarketIntelligence
    benchmark_comparison: dict[str, Any]
    recommended_actions: list[RecommendedAction]
    experiment_plan: dict[str, Any]
    data_quality: dict[str, Any]
    analysis_metadata: dict[str, Any]

    @field_validator("recommended_actions")
    @classmethod
    def require_recommended_actions(cls, value: list[RecommendedAction]) -> list[RecommendedAction]:
        if not value:
            raise ValueError("recommended_actions must contain at least one action")
        return value
