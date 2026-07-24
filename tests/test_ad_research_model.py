from __future__ import annotations

import json

import httpx
import pytest

from backend.app.core.config import get_settings
from backend.app.services.ad_research_media import PreparedAdMedia
from backend.app.services.ad_research_model import (
    AdResearchModel,
    PlannedQuery,
    QueryPerformance,
    QueryPlan,
    RedisGlobalLimiter,
    _non_negative_int,
    _normalize_planned_queries,
    _round_review_from_summary,
    _validated_visual_score,
)
from backend.app.services.ad_research_orchestrator import _planner_query_performance
from backend.app.services.ad_research_ranking import is_quality_candidate


class Lease:
    async def release(self) -> None:
        return None


class Limiter:
    async def acquire(self, *args, **kwargs) -> Lease:
        return Lease()


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def gateway_model_that_captures_request(
    monkeypatch, captured: dict[str, object]
) -> tuple[AdResearchModel, httpx.AsyncClient]:
    monkeypatch.setenv("LLM_PROVIDER", "gateway")
    monkeypatch.setenv("MODEL_GATEWAY_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("MODEL_GATEWAY_API_KEY", "test-key")
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "visual_priority": "game_gambling",
                        "game_context_present": True,
                        "betting_context_present": True,
                        "money_only_promo": False,
                        "negative_visual_type": "none",
                        "component_scores": {
                            "gameplay_ui": 35,
                            "betting_mechanism": 20,
                            "in_game_value_ui": 12,
                            "gambling_style": 8,
                            "visual_clarity": 9,
                            "media_quality": 5,
                        },
                        "analysis_confidence": 0.8,
                        "visual_evidence": [{"frame_index": 0, "detail": "slot reels are visible"}],
                        "retrieval_hints": ["slot ui"],
                    }
                )
            },
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://gateway.example/v1/"
    )
    return AdResearchModel(http_client=client, limiter=Limiter()), client


def prepared_media_with_three_frames(tmp_path) -> PreparedAdMedia:
    frames = []
    for name in ("frame_20.jpg", "frame_50.jpg", "frame_80.jpg"):
        path = tmp_path / name
        path.write_bytes(b"fake-jpeg-payload")
        frames.append(path)
    return PreparedAdMedia(
        cover_url="https://ai.example/storage/ad-research/job/ad/cover.jpg",
        cover_source="generated_frame",
        frame_urls=tuple(
            f"https://ai.example/storage/ad-research/job/ad/{path.name}" for path in frames
        ),
        local_frame_paths=tuple(frames),
        duration_source="collector",
        duration_probe_attempts=0,
    )


def test_research_model_limiter_falls_back_to_celery_broker(monkeypatch) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://redis:6379/0")
    get_settings.cache_clear()

    assert RedisGlobalLimiter().redis_url == "redis://redis:6379/0"


def test_user_keywords_keep_exact_queries_and_eighty_percent_budget() -> None:
    planned = (
        PlannedQuery(
            "r1_q01",
            "777 bonus",
            "game_gambling",
            "expand",
            (),
            "user_expanded",
            "777",
        ),
        PlannedQuery(
            "r1_q02",
            "slot spin",
            "game_gambling",
            "expand",
            (),
            "user_expanded",
            "slot",
        ),
        PlannedQuery(
            "r1_q03",
            "casino ui",
            "format_exploration",
            "explore",
            (),
            "model_exploration",
            None,
        ),
    )

    result = _normalize_planned_queries(
        planned,
        seed_keywords=["777", "slot"],
        country="IN",
        category="gambling",
        round_number=1,
        max_queries=10,
    )

    assert [(q.query, q.parent_keyword) for q in result if q.query_origin == "user_exact"] == [
        ("777", "777"),
        ("slot", "slot"),
    ]
    assert sum(q.query_origin in {"user_exact", "user_expanded"} for q in result) == 8
    assert sum(q.query_origin in {"model_exploration", "model_recovery"} for q in result) == 2


def test_multiple_user_keywords_are_independent() -> None:
    result = _normalize_planned_queries(
        (),
        seed_keywords=["777", "slot", "teen patti"],
        country="IN",
        category="gambling",
        round_number=2,
        max_queries=10,
    )

    exact = {q.query for q in result if q.query_origin == "user_exact"}
    assert exact == {"777", "slot", "teen patti"}
    assert "777 slot teen patti" not in {q.query for q in result}


@pytest.mark.parametrize("keyword_count", [9, 10, 11, 12, 24])
def test_round_one_exact_keyword_capacity_boundaries(keyword_count: int) -> None:
    keywords = [f"keyword-{index:02d}" for index in range(1, keyword_count + 1)]

    result = _normalize_planned_queries(
        (),
        seed_keywords=keywords,
        country="IN",
        category="gambling",
        round_number=1,
    )

    exact = [query.query for query in result if query.query_origin == "user_exact"]
    assert exact == keywords[:10]


def test_user_exact_keywords_cover_twenty_four_across_deterministic_round_slices() -> None:
    keywords = [f"keyword-{index:02d}" for index in range(1, 25)]

    exact_by_round = []
    for round_number in range(1, 4):
        result = _normalize_planned_queries(
            (),
            seed_keywords=keywords,
            country="IN",
            category="gambling",
            round_number=round_number,
        )
        exact_by_round.append(
            [query.query for query in result if query.query_origin == "user_exact"]
        )

    assert exact_by_round == [keywords[:10], keywords[10:20], keywords[20:24]]
    assert set().union(*(set(batch) for batch in exact_by_round)) == set(keywords)

    round_four = _normalize_planned_queries(
        (),
        seed_keywords=keywords,
        country="IN",
        category="gambling",
        round_number=4,
    )
    round_four_exact = [
        query.query for query in round_four if query.query_origin == "user_exact"
    ]
    assert round_four_exact == keywords[:10]


def test_max_length_keyword_keeps_eighty_twenty_budget_with_safe_expansions() -> None:
    keyword = "x" * 160

    result = _normalize_planned_queries(
        (),
        seed_keywords=[keyword],
        country="IN",
        category="gambling",
        round_number=1,
    )

    user_family = [query for query in result if query.query_origin.startswith("user_")]
    model_family = [query for query in result if query.query_origin.startswith("model_")]
    expanded = [query for query in result if query.query_origin == "user_expanded"]
    assert len(user_family) == 8
    assert len(model_family) == 2
    assert len(expanded) == 7
    assert all(query.parent_keyword == keyword for query in expanded)
    assert all(0 < len(query.query) <= 160 for query in result)
    assert all(query.query != keyword for query in expanded)
    assert len({query.query.casefold() for query in result}) == 10


def test_max_length_keyword_slices_keep_reachable_per_round_budget() -> None:
    keywords = [f"{index:02d}" + ("x" * 158) for index in range(1, 25)]

    plans = [
        _normalize_planned_queries(
            (),
            seed_keywords=keywords,
            country="IN",
            category="gambling",
            round_number=round_number,
        )
        for round_number in range(1, 4)
    ]

    assert [
        sum(query.query_origin == "user_exact" for query in plan) for plan in plans
    ] == [10, 10, 4]
    assert [
        sum(query.query_origin.startswith("user_") for query in plan) for plan in plans
    ] == [10, 10, 8]
    assert [
        sum(query.query_origin.startswith("model_") for query in plan) for plan in plans
    ] == [0, 0, 2]
    assert all(len(query.query) <= 160 for plan in plans for query in plan)
    round_three_expanded = [
        query for query in plans[2] if query.query_origin == "user_expanded"
    ]
    assert len(round_three_expanded) == 4
    assert all(query.parent_keyword in keywords[20:24] for query in round_three_expanded)


def test_query_performance_accepts_round_ten_and_safe_metrics() -> None:
    payload = QueryPerformance(
        query_id="r10_q12",
        collected_count=50,
        technical_qualified_count=17,
        scored_count=16,
        quality_candidate_count=8,
        best_visual_score=81.5,
        final_selected_count=5,
        rejected_count=34,
    ).as_dict()

    assert payload["query_id"] == "r10_q12"
    assert payload["quality_candidate_count"] == 8
    assert payload["best_visual_score"] == 81.5


def test_round_review_maps_legacy_selected_count_to_scored_count_only() -> None:
    legacy_performance = _planner_query_performance(
        [
            {
                "query_id": "r2_q03",
                "query": "legacy query text",
                "raw_collected": 8,
                "model_scored": 5,
            }
        ]
    )
    assert legacy_performance == [
        {
            "query_id": "r2_q03",
            "query": "legacy query text",
            "collected_count": 8,
            "selected_count": 5,
            "rejected_count": 3,
        }
    ]

    review = _round_review_from_summary(
        {"query_performance": legacy_performance},
        quality_supplement_allowed=False,
    )

    assert review.query_performance[0].as_dict() == {
        "query_id": "r2_q03",
        "collected_count": 8,
        "technical_qualified_count": 0,
        "scored_count": 5,
        "quality_candidate_count": 0,
        "best_visual_score": 0.0,
        "final_selected_count": 0,
        "rejected_count": 3,
    }


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf"), "not-a-number", object()],
)
def test_non_negative_int_safely_zeroes_non_finite_and_unconvertible_values(value: object) -> None:
    assert _non_negative_int(value) == 0


@pytest.mark.asyncio
async def test_model_planner_returns_structured_query_plan(monkeypatch) -> None:
    captured: dict[str, object] = {}
    model, client = gateway_model_that_captures_request(monkeypatch, captured)

    async def complete_json(**kwargs):
        return {
            "queries": [
                {
                    "query_id": "r1_q01",
                    "query": "rummy bonus",
                    "intent": "game_gambling",
                    "rationale": "Seed expansion for public-library recall.",
                    "query_origin": "user_expanded",
                    "parent_keyword": "rummy",
                }
            ],
            "summary": "Use a game-gambling seed expansion.",
        }

    model._complete_json = complete_json
    plan = await model.plan_queries(
        country="IN", category="gambling", seed_keywords=["rummy"], round_number=1
    )

    assert isinstance(plan, QueryPlan)
    assert len(plan) == 10
    assert plan.queries[0].as_dict() == {
        "query_id": "r1_q01",
        "query": "rummy",
        "intent": "game_gambling",
        "rationale": "Execute the original user keyword.",
        "expected_visuals": [],
        "query_origin": "user_exact",
        "parent_keyword": "rummy",
    }
    expanded = next(item for item in plan if item.query == "rummy bonus")
    assert (expanded.query_origin, expanded.parent_keyword) == ("user_expanded", "rummy")
    assert sum(item.query_origin.startswith("user_") for item in plan) == 8
    assert sum(item.query_origin.startswith("model_") for item in plan) == 2
    assert plan.summary == "Use a game-gambling seed expansion."
    await client.aclose()


@pytest.mark.asyncio
async def test_model_planner_drops_illegal_intent_and_keeps_valid_structured_query(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    model, client = gateway_model_that_captures_request(monkeypatch, captured)

    async def complete_json(**kwargs):
        return {
            "queries": [
                {
                    "query_id": "r1_q01",
                    "query": "invalid intent",
                    "intent": "freeform",
                    "rationale": "Must not be accepted.",
                    "query_origin": "user_expanded",
                    "parent_keyword": "rummy",
                },
                {
                    "query_id": "r1_q02",
                    "query": "invalid origin",
                    "intent": "game_gambling",
                    "rationale": "Must not be accepted.",
                    "query_origin": "other",
                    "parent_keyword": None,
                },
                {
                    "query_id": "r1_q03",
                    "query": "missing parent",
                    "intent": "game_gambling",
                    "rationale": "Must not be accepted.",
                    "query_origin": "user_expanded",
                    "parent_keyword": None,
                },
                {
                    "query_id": "r1_q04",
                    "query": "rummy app",
                    "intent": "game_gambling",
                    "rationale": "A valid seed variation.",
                    "query_origin": "user_expanded",
                    "parent_keyword": "rummy",
                },
            ]
        }

    model._complete_json = complete_json
    plan = await model.plan_queries(
        country="IN", category="gambling", seed_keywords=["rummy"], round_number=1
    )

    queries = {item.query: item for item in plan}
    assert "invalid intent" not in queries
    assert "invalid origin" not in queries
    assert "missing parent" not in queries
    assert (queries["rummy app"].query_origin, queries["rummy app"].parent_keyword) == (
        "user_expanded",
        "rummy",
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_model_planner_drops_duplicate_ids_queries_and_empty_values(monkeypatch) -> None:
    captured: dict[str, object] = {}
    model, client = gateway_model_that_captures_request(monkeypatch, captured)

    async def complete_json(**kwargs):
        return {
            "queries": [
                {
                    "query_id": "r2_q01",
                    "query": "  rummy bonus  ",
                    "intent": "game_gambling",
                    "rationale": "First query.",
                    "query_origin": "user_expanded",
                    "parent_keyword": "rummy",
                },
                {
                    "query_id": "r2_q01",
                    "query": "rummy cash",
                    "intent": "game_gambling",
                    "rationale": "Duplicate id.",
                    "query_origin": "user_expanded",
                    "parent_keyword": "rummy",
                },
                {
                    "query_id": "r2_q03",
                    "query": "RUMMY BONUS",
                    "intent": "game_gambling",
                    "rationale": "Duplicate query.",
                    "query_origin": "user_expanded",
                    "parent_keyword": "rummy",
                },
                {
                    "query_id": "r2_q04",
                    "query": "   ",
                    "intent": "game_gambling",
                    "rationale": "Empty query.",
                    "query_origin": "user_expanded",
                    "parent_keyword": "rummy",
                },
            ]
        }

    model._complete_json = complete_json
    plan = await model.plan_queries(
        country="IN", category="gambling", seed_keywords=["rummy"], round_number=2
    )

    queries = [item.query for item in plan]
    assert "rummy bonus" in queries
    assert "rummy cash" not in queries
    assert sum(query.casefold() == "rummy bonus" for query in queries) == 1
    assert all(query.strip() for query in queries)
    await client.aclose()


@pytest.mark.asyncio
async def test_model_planner_invalid_model_response_uses_deterministic_fallback(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    model, client = gateway_model_that_captures_request(monkeypatch, captured)

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"output_text": "not valid JSON"})

    await client.aclose()
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://gateway.example/v1/"
    )
    model = AdResearchModel(http_client=client, limiter=Limiter())
    first = await model.plan_queries(
        country="IN", category="gambling", seed_keywords=["rummy", "rummy"], round_number=3
    )
    second = await model.plan_queries(
        country="IN", category="gambling", seed_keywords=["rummy", "rummy"], round_number=3
    )

    assert first == second
    assert len(first) == 10
    assert first.queries[0].query == "rummy"
    assert first.queries[0].query_origin == "user_exact"
    assert [item.query_id for item in first] == [f"r3_q{index:02d}" for index in range(1, 11)]
    assert sum(item.query_origin.startswith("user_") for item in first) == 8
    assert sum(item.query_origin.startswith("model_") for item in first) == 2
    assert captured["reasoning"] == {"effort": "none"}
    await client.aclose()


@pytest.mark.asyncio
async def test_visual_score_sends_new_pure_visual_contract_and_quality_candidate(
    monkeypatch, tmp_path
) -> None:
    captured: dict[str, object] = {}
    model, client = gateway_model_that_captures_request(monkeypatch, captured)
    media = prepared_media_with_three_frames(tmp_path)

    result = await model.score_visual(
        category="gambling",
        duration_seconds=15.0,
        media=media,
    )

    system_prompt = captured["input"][0]["content"]
    user_content = captured["input"][1]["content"]
    text_blocks = [part["text"] for part in user_content if part["type"] == "input_text"]
    assert len(text_blocks) == 1
    assert json.loads(text_blocks[0]) == {
        "media": {"duration_seconds": 15.0, "frame_count": 3},
    }
    assert len([part for part in user_content if part["type"] == "input_image"]) == 3

    required_fields = (
        "visual_priority",
        "game_context_present",
        "betting_context_present",
        "money_only_promo",
        "negative_visual_type",
        "component_scores",
        "analysis_confidence",
        "visual_evidence",
        "retrieval_hints",
    )
    component_limits = (
        "gameplay_ui <= 35",
        "betting_mechanism <= 25",
        "in_game_value_ui <= 15",
        "gambling_style <= 10",
        "visual_clarity <= 10",
        "media_quality <= 5",
    )
    assert all(field in system_prompt for field in required_fields)
    assert all(limit in system_prompt for limit in component_limits)

    forbidden_terms = (
        "gameplay_gambling_points",
        "multi_signal_style_points",
        "betting_mechanism_points",
        "gambling_visual_style_points",
        "visual_clarity_points",
        "media_quality_points",
        "game_visual_present",
        "gambling_signals",
        "uncertain",
        "category_match",
        "is_obviously_unrelated",
        "gameplay_ui <= 40",
        "multi_signal_style_points 20",
        "betting_mechanism_points 15",
        "gambling_visual_style_points 10",
        "visual_clarity_points 10",
        "media_quality_points 5",
        "active_days",
        "advertiser",
        "category",
        "\u4e3b\u9875",
        "\u6b63\u6587",
        "\u6807\u9898",
        "CTA",
        "URL",
        "\u843d\u5730\u9875",
        "keyword",
        "\u5173\u952e\u8bcd",
        "query_origin",
        "public_continuity_points",
    )
    request_text = "\n".join([system_prompt, *text_blocks])
    assert all(term.casefold() not in request_text.casefold() for term in forbidden_terms)
    assert captured["reasoning"] == {"effort": "none"}
    assert result["visual_total"] > 0
    assert is_quality_candidate(result)
    await client.aclose()


def test_visual_score_validation_uses_component_contract_and_negative_caps() -> None:
    result = _validated_visual_score(
        {
            "visual_priority": "game_gambling",
            "game_context_present": True,
            "betting_context_present": True,
            "money_only_promo": False,
            "negative_visual_type": "weak_gambling_game",
            "component_scores": {
                "gameplay_ui": 99,
                "betting_mechanism": 99,
                "in_game_value_ui": -4,
                "gambling_style": 99,
                "visual_clarity": 99,
                "media_quality": 99,
            },
            "analysis_confidence": 9,
            "visual_evidence": [
                {"frame_index": 0, "detail": "valid"},
                {"frame_index": 5, "detail": "out of range"},
            ],
            "retrieval_hints": ["slot ui"],
            "category_match": True,
            "is_obviously_unrelated": False,
        },
        frame_count=3,
    )

    assert result["visual_priority"] == "game_gambling"
    assert result["component_scores"] == {
        "gameplay_ui": 35.0,
        "betting_mechanism": 25.0,
        "in_game_value_ui": 0.0,
        "gambling_style": 10.0,
        "visual_clarity": 10.0,
        "media_quality": 5.0,
    }
    assert result["visual_total"] == 49.0
    assert result["analysis_confidence"] == 1.0
    assert result["visual_evidence"] == [
        {"frame_index": 0, "detail": "valid"},
        {"frame_index": 5, "detail": "out of range"},
    ]
    assert result["retrieval_hints"] == ["slot ui"]
    assert "category_match" not in result
    assert "is_obviously_unrelated" not in result
    assert not {
        "gameplay_gambling_points",
        "multi_signal_style_points",
        "betting_mechanism_points",
        "gambling_visual_style_points",
        "visual_clarity_points",
        "media_quality_points",
    }.intersection(result)


@pytest.mark.asyncio
async def test_mock_visual_score_uses_only_prepared_media(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    get_settings.cache_clear()
    model = AdResearchModel(limiter=Limiter())

    result = await model.score_visual(
        category="gambling",
        duration_seconds=15.0,
        media=prepared_media_with_three_frames(tmp_path),
    )

    assert result["component_scores"]["media_quality"] == 5.0
    assert result["visual_total"] == 5.0
    assert result["visual_priority"] == "unrelated"


@pytest.mark.asyncio
async def test_model_planner_prompt_has_schema_and_allowed_intents(monkeypatch) -> None:
    captured: dict[str, object] = {}
    model, client = gateway_model_that_captures_request(monkeypatch, captured)

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        output = {
            "queries": [
                {
                    "query_id": "r2_q01",
                    "query": "short promo",
                    "intent": "format_exploration",
                    "rationale": "Explore short-form formats.",
                    "query_origin": "model_exploration",
                    "parent_keyword": None,
                }
            ]
        }
        return httpx.Response(200, json={"output_text": f"```json\n{json.dumps(output)}\n```"})

    await client.aclose()
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://gateway.example/v1/"
    )
    model = AdResearchModel(http_client=client, limiter=Limiter())
    gap_summary = {
        "previous_queries": ["old query"],
        "technical_rejection_summary": {"duration_over_30": 12},
        "duplicate_count": 7,
        "high_score_visible_elements": ["slot reels", "coins"],
        "ad_text": "must not be forwarded",
        "landing_url": "https://must-not-be-forwarded.example",
    }

    plan = await model.plan_queries(
        country="IN",
        category="gambling",
        seed_keywords=[],
        round_number=2,
        gap_summary=gap_summary,
    )

    assert plan.queries[0].as_dict() == {
        "query_id": "r2_q01",
        "query": "short promo",
        "intent": "format_exploration",
        "rationale": "Explore short-form formats.",
        "expected_visuals": [],
        "query_origin": "model_exploration",
        "parent_keyword": None,
    }
    assert plan.queries[1].query_origin == "model_recovery"
    system_prompt = captured["input"][0]["content"]
    assert '"queries"' in system_prompt
    assert '"expected_visuals"' in system_prompt
    assert '"query_origin"' in system_prompt
    assert '"parent_keyword"' in system_prompt
    assert "independent queries" in system_prompt
    assert "80 percent" in system_prompt
    for origin in ("user_exact", "user_expanded", "model_exploration", "model_recovery"):
        assert origin in system_prompt
    for intent in (
        "game_gambling",
        "sports_betting",
        "local_exploration",
        "format_exploration",
    ):
        assert intent in system_prompt
    user_payload = json.loads(captured["input"][1]["content"])
    assert user_payload["round_review"]["high_score_visible_elements"] == ["slot reels"]
    assert "coins" not in json.dumps(user_payload)
    assert "ad_text" not in json.dumps(user_payload)
    assert "must-not-be-forwarded" not in json.dumps(user_payload)
    await client.aclose()


@pytest.mark.asyncio
async def test_model_planner_serializes_only_whitelisted_round_review_data_to_responses(
    monkeypatch,
) -> None:
    requests: list[dict[str, object]] = []
    monkeypatch.setenv("LLM_PROVIDER", "gateway")
    monkeypatch.setenv("MODEL_GATEWAY_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("MODEL_GATEWAY_API_KEY", "test-key")
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        user_payload = json.loads(payload["input"][1]["content"])
        round_number = user_payload["round_number"]
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "queries": [
                            {
                                "query_id": f"r{round_number}_q01",
                                "query": f"controlled query {round_number}",
                                "intent": "game_gambling",
                                "rationale": "Controlled quality supplement.",
                                "expected_visuals": ["slot reels"],
                                "query_origin": "model_exploration",
                                "parent_keyword": None,
                            }
                        ]
                    }
                )
            },
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://gateway.example/v1/"
    )
    model = AdResearchModel(http_client=client, limiter=Limiter())
    sensitive_values = (
        "https://sensitive.example/offer",
        "OCR WIN 5000",
        "Jane Example",
        "Install now for the VIP jackpot bonus",
    )
    gap_summary = {
        "quality_supplement_mode": True,
        "query_performance": [
            {
                "query_id": "r4_q01",
                "query": sensitive_values[0],
                "collected_count": 8,
                "technical_qualified_count": 6,
                "scored_count": 5,
                "quality_candidate_count": 3,
                "best_visual_score": 81.239,
                "final_selected_count": 1,
                "rejected_count": 7,
                "intent": "game_gambling",
                "ad_text": sensitive_values[3],
                "ocr_text": sensitive_values[1],
                "advertiser_name": sensitive_values[2],
                "uncontrolled_nested": {"url": sensitive_values[0]},
            }
        ],
        "priority_gaps": ["game ui", "slot reels", "uncontrolled text"],
        "missing_signals": ["wallet or balance ui", "https://must-not-be-forwarded.example"],
        "previous_queries": ["must not be copied into query_performance"],
        "ocr_text": "must not be forwarded",
    }

    for round_number in range(1, 6):
        await model.plan_queries(
            country="IN",
            category="gambling",
            seed_keywords=["rummy"],
            round_number=round_number,
            gap_summary=gap_summary,
        )

    assert len(requests) == 5
    serialized_user_payloads = [request["input"][1]["content"] for request in requests]
    serialized_system_prompts = [request["input"][0]["content"] for request in requests]
    for sensitive_value in sensitive_values:
        assert all(sensitive_value not in payload for payload in serialized_user_payloads)
        assert all(sensitive_value not in prompt for prompt in serialized_system_prompts)

    round_five = json.loads(serialized_user_payloads[4])["round_review"]
    assert round_five == {
        "query_performance": [
            {
                "query_id": "r4_q01",
                "collected_count": 8,
                "technical_qualified_count": 6,
                "scored_count": 5,
                "quality_candidate_count": 3,
                "best_visual_score": 81.24,
                "final_selected_count": 1,
                "rejected_count": 7,
            }
        ],
        "quality_supplement_mode": True,
        "priority_gaps": ["game ui", "slot reels"],
        "missing_signals": ["wallet or balance ui"],
        "high_score_visible_elements": [],
        "technical_rejection_summary": {},
        "duplicate_count": 0,
    }
    for round_number in range(1, 5):
        review = json.loads(serialized_user_payloads[round_number - 1])["round_review"]
        assert review["quality_supplement_mode"] is False
    assert "P1-P3" in serialized_system_prompts[4]
    assert "quality_supplement_mode" in serialized_system_prompts[4]
    await client.aclose()


@pytest.mark.parametrize("query_id", ["r0_q01", "r11_q01", "r1_q00", "r1_q13", "r1_q99"])
def test_planned_query_rejects_query_ids_outside_supported_rounds_and_slots(query_id: str) -> None:
    with pytest.raises(ValueError, match="query_id"):
        PlannedQuery(
            query_id=query_id,
            query="rummy bonus",
            intent="local_exploration",
            rationale="Validation boundary coverage.",
        )


def test_planned_query_accepts_round_ten_and_last_slot() -> None:
    query = PlannedQuery(
        query_id="r10_q12",
        query="rummy bonus",
        intent="local_exploration",
        rationale="Validation boundary coverage.",
    )

    assert query.query_id == "r10_q12"


@pytest.mark.asyncio
async def test_model_planner_normalizes_expected_visuals(monkeypatch) -> None:
    captured: dict[str, object] = {}
    model, client = gateway_model_that_captures_request(monkeypatch, captured)

    async def complete_json(**kwargs):
        return {
            "queries": [
                {
                    "query_id": "r1_q01",
                    "query": "rummy bonus",
                    "intent": "local_exploration",
                    "rationale": "Controlled visual expectation.",
                    "expected_visuals": [
                        "  slot reels  ",
                        "SLOT REELS",
                        "",
                        "x" * 81,
                        *[f"visual {index}" for index in range(1, 10)],
                    ],
                    "query_origin": "user_expanded",
                    "parent_keyword": "rummy",
                }
            ]
        }

    model._complete_json = complete_json
    plan = await model.plan_queries(
        country="IN", category="gambling", seed_keywords=["rummy"], round_number=1
    )

    expanded = next(item for item in plan if item.query == "rummy bonus")
    assert expanded.expected_visuals == (
        "slot reels",
        "visual 1",
        "visual 2",
        "visual 3",
        "visual 4",
        "visual 5",
        "visual 6",
        "visual 7",
    )
    assert expanded.as_dict()["expected_visuals"] == [
        "slot reels",
        "visual 1",
        "visual 2",
        "visual 3",
        "visual 4",
        "visual 5",
        "visual 6",
        "visual 7",
    ]
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("round_number", "expected_prefix"),
    [(0, "r1_"), (7, "r7_"), (99, "r10_")],
)
async def test_model_planner_fallback_clamps_round_number(
    monkeypatch, round_number: int, expected_prefix: str
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    get_settings.cache_clear()
    model = AdResearchModel(limiter=Limiter())

    plan = await model.plan_queries(
        country="IN",
        category="gambling",
        seed_keywords=["rummy"],
        round_number=round_number,
    )

    assert [item.query_id for item in plan] == [
        f"{expected_prefix}q{index:02d}" for index in range(1, 11)
    ]
    assert plan.queries[0].query == "rummy"
    assert plan.queries[0].query_origin == "user_exact"
    assert sum(item.query_origin.startswith("user_") for item in plan) == 8
    assert sum(item.query_origin.startswith("model_") for item in plan) == 2
    assert all(item.expected_visuals == () for item in plan)


@pytest.mark.asyncio
async def test_model_planner_round_review_only_forwards_visual_taxonomy(monkeypatch) -> None:
    captured: dict[str, object] = {}
    model, client = gateway_model_that_captures_request(monkeypatch, captured)

    async def complete_json(**kwargs):
        captured.update({"system": kwargs["system"], "user": kwargs["user"]})
        return {
            "queries": [
                {
                    "query_id": "r2_q01",
                    "query": "rummy bonus",
                    "intent": "local_exploration",
                    "rationale": "Controlled round review.",
                    "expected_visuals": ["slot reels"],
                    "query_origin": "user_expanded",
                    "parent_keyword": "rummy",
                }
            ]
        }

    model._complete_json = complete_json
    forbidden = [
        "Alicia Player",
        "Channel 42",
        "3-2 final score",
        "https://tracker.example/path",
        "Click now and win $500",
        "avoid review detection",
    ]
    plan = await model.plan_queries(
        country="IN",
        category="gambling",
        seed_keywords=["rummy"],
        round_number=2,
        gap_summary={
            "priority_gaps": forbidden,
            "missing_signals": forbidden,
            "high_score_visible_elements": [" slot reels ", *forbidden],
        },
    )

    assert plan.round_review is not None
    assert plan.round_review.priority_gaps == ()
    assert plan.round_review.missing_signals == ()
    assert plan.round_review.high_score_visible_elements == ("slot reels",)
    payload = json.dumps(captured, ensure_ascii=False)
    assert "slot reels" in payload
    for value in forbidden:
        assert value not in payload
    await client.aclose()
