from typing import Any

try:
    from langgraph.types import interrupt
except ImportError:  # pragma: no cover
    interrupt = None

from backend.app.agents.state import AdWorkflowState


def _interrupt(payload: dict[str, Any]) -> dict[str, Any]:
    if interrupt is None:
        raise RuntimeError("langgraph is required to use human-in-the-loop workflow nodes.")
    return interrupt(payload)


async def collect_signals_node(state: AdWorkflowState) -> AdWorkflowState:
    return {"signals": state.get("signals", {})}


async def generate_topics_node(state: AdWorkflowState) -> AdWorkflowState:
    # Runtime services should call TopicService and pass generated IDs into the graph state.
    return {"generated_topic_ids": state.get("generated_topic_ids", [])}


async def wait_topic_review_node(state: AdWorkflowState) -> AdWorkflowState:
    decision = _interrupt(
        {
            "kind": "topic_review",
            "campaign_id": state.get("campaign_id"),
            "topic_ids": state.get("generated_topic_ids", []),
        }
    )
    return {
        "selected_topic_id": decision.get("selected_topic_id"),
        "review_feedback": decision.get("feedback", ""),
    }


async def generate_copy_node(state: AdWorkflowState) -> AdWorkflowState:
    return {"copy_draft_id": state.get("copy_draft_id", "")}


async def wait_copy_review_node(state: AdWorkflowState) -> AdWorkflowState:
    decision = _interrupt(
        {
            "kind": "copy_review",
            "campaign_id": state.get("campaign_id"),
            "copy_draft_id": state.get("copy_draft_id"),
        }
    )
    return {"review_feedback": decision.get("feedback", "")}


async def generate_creatives_node(state: AdWorkflowState) -> AdWorkflowState:
    return {"creative_asset_ids": state.get("creative_asset_ids", [])}


async def wait_creative_review_node(state: AdWorkflowState) -> AdWorkflowState:
    decision = _interrupt(
        {
            "kind": "creative_review",
            "campaign_id": state.get("campaign_id"),
            "creative_asset_ids": state.get("creative_asset_ids", []),
        }
    )
    return {"review_feedback": decision.get("feedback", "")}


async def prepare_delivery_package_node(state: AdWorkflowState) -> AdWorkflowState:
    return {"delivery_job_id": state.get("delivery_job_id", "")}


async def wait_final_review_node(state: AdWorkflowState) -> AdWorkflowState:
    decision = _interrupt(
        {
            "kind": "final_review",
            "campaign_id": state.get("campaign_id"),
            "delivery_job_id": state.get("delivery_job_id"),
        }
    )
    return {"review_feedback": decision.get("feedback", "")}


async def return_delivery_package_node(state: AdWorkflowState) -> AdWorkflowState:
    return {"delivery_job_id": state.get("delivery_job_id", "")}
