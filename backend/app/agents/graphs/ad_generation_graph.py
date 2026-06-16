try:
    from langgraph.graph import END, START, StateGraph
except ImportError:  # pragma: no cover
    END = START = StateGraph = None

from backend.app.agents.nodes import (
    collect_signals_node,
    generate_copy_node,
    generate_creatives_node,
    generate_topics_node,
    prepare_delivery_package_node,
    return_delivery_package_node,
    wait_copy_review_node,
    wait_creative_review_node,
    wait_final_review_node,
    wait_topic_review_node,
)
from backend.app.agents.state import AdWorkflowState


def build_ad_generation_graph():
    if StateGraph is None:
        raise RuntimeError("langgraph is required to build the ad generation graph.")

    graph = StateGraph(AdWorkflowState)
    graph.add_node("collect_signals", collect_signals_node)
    graph.add_node("generate_topics", generate_topics_node)
    graph.add_node("wait_topic_review", wait_topic_review_node)
    graph.add_node("generate_copy", generate_copy_node)
    graph.add_node("wait_copy_review", wait_copy_review_node)
    graph.add_node("generate_creatives", generate_creatives_node)
    graph.add_node("wait_creative_review", wait_creative_review_node)
    graph.add_node("prepare_delivery_package", prepare_delivery_package_node)
    graph.add_node("wait_final_review", wait_final_review_node)
    graph.add_node("return_delivery_package", return_delivery_package_node)

    graph.add_edge(START, "collect_signals")
    graph.add_edge("collect_signals", "generate_topics")
    graph.add_edge("generate_topics", "wait_topic_review")
    graph.add_edge("wait_topic_review", "generate_copy")
    graph.add_edge("generate_copy", "wait_copy_review")
    graph.add_edge("wait_copy_review", "generate_creatives")
    graph.add_edge("generate_creatives", "wait_creative_review")
    graph.add_edge("wait_creative_review", "prepare_delivery_package")
    graph.add_edge("prepare_delivery_package", "wait_final_review")
    graph.add_edge("wait_final_review", "return_delivery_package")
    graph.add_edge("return_delivery_package", END)

    return graph.compile()
