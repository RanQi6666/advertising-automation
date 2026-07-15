from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

ShortText = Annotated[str, Field(min_length=1, max_length=100)]
OperatorText = Annotated[str, Field(min_length=1, max_length=600)]
OptionalAdCopy = Annotated[str | None, Field(max_length=1200)]


class StrictPublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OverallDecision(StrictPublicModel):
    action: Literal["scale", "optimize", "monitor", "pause"]
    priority: Literal["high", "medium", "low"]
    main_problem: ShortText


class TargetingAnalysisItem(StrictPublicModel):
    dimension: Literal["country", "audience", "age", "gender", "device", "placement"]
    current: OperatorText
    decision: Literal["adjust", "test", "monitor"]
    problem: OperatorText
    suggestion: OperatorText
    reason: OperatorText


class AdjustmentPlan(StrictPublicModel):
    priority: Literal["high", "medium", "low"]
    category: Literal[
        "landing_page",
        "tracking",
        "copywriting",
        "media",
        "targeting",
        "budget",
        "campaign_setup",
    ]
    title: ShortText
    action: OperatorText
    reason: OperatorText
    expected_effect: OperatorText
    what_to_watch: OperatorText


class CopywritingAnalysis(StrictPublicModel):
    summary: ShortText
    problems: list[OperatorText] = Field(default_factory=list, max_length=3)
    suggestions: list[OperatorText] = Field(default_factory=list, max_length=3)
    recommended_primary_text: OptionalAdCopy = None
    recommended_headline: OptionalAdCopy = None
    recommended_description: OptionalAdCopy = None


class MediaImprovement(StrictPublicModel):
    location: OperatorText
    problem: OperatorText
    action: OperatorText


class MediaAnalysis(StrictPublicModel):
    media_type: Literal["image", "video", "carousel"]
    summary: ShortText
    improvements: list[MediaImprovement] = Field(default_factory=list, max_length=3)


class MarketReference(StrictPublicModel):
    advertiser_name: OperatorText
    source_url: Annotated[str, Field(min_length=1, max_length=2000)]
    observed_pattern: OperatorText
    applicable_idea: OperatorText


class MarketIntelligence(StrictPublicModel):
    status: Literal["completed", "partial", "unavailable"]
    summary: ShortText
    references: list[MarketReference] = Field(default_factory=list, max_length=3)
    limitation: OperatorText


class FacebookAdAnalysisResult(StrictPublicModel):
    """Concise operator result returned by the async Facebook analysis API."""

    schema_version: Literal["facebook_ad_analysis_v1"]
    platform: Literal["facebook"]
    summary: ShortText
    overall_decision: OverallDecision
    targeting_analysis: list[TargetingAnalysisItem] = Field(default_factory=list, max_length=3)
    adjustment_plans: list[AdjustmentPlan] = Field(min_length=1, max_length=5)
    copywriting_analysis: CopywritingAnalysis
    media_analysis: MediaAnalysis
    market_intelligence: MarketIntelligence
    data_gaps: list[OperatorText] = Field(default_factory=list, max_length=3)
