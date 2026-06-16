from typing import Any, TypedDict


class AdWorkflowState(TypedDict, total=False):
    campaign_id: str
    signals: dict[str, Any]
    generated_topic_ids: list[str]
    selected_topic_id: str
    copy_draft_id: str
    creative_asset_ids: list[str]
    delivery_job_id: str
    review_feedback: str
    errors: list[str]
