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
    _normalize_planned_queries,
    _validated_visual_score,
)


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
                        "gameplay_gambling_points": 40,
                        "multi_signal_style_points": 8,
                        "betting_mechanism_points": 4,
                        "gambling_visual_style_points": 2,
                        "visual_clarity_points": 10,
                        "media_quality_points": 5,
                        "analysis_confidence": 0.8,
                        "gambling_signals": ["slot reels", "coins"],
                        "game_visual_present": True,
                        "visual_evidence": [{"frame_index": 0, "detail": "slot reels are visible"}],
                        "retrieval_hints": ["slot ui"],
                        "uncertain": False,
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
async def test_visual_score_sends_exact_pure_visual_metadata_and_three_input_images(
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

    user_content = captured["input"][1]["content"]
    text_blocks = [part["text"] for part in user_content if part["type"] == "input_text"]
    assert len(text_blocks) == 1
    assert json.loads(text_blocks[0]) == {
        "category": "gambling",
        "media": {"duration_seconds": 15.0, "frame_count": 3},
    }
    assert len([part for part in user_content if part["type"] == "input_image"]) == 3
    forbidden_terms = (
        "active_days",
        "advertiser",
        "\u4e3b\u9875",
        "\u6b63\u6587",
        "\u6807\u9898",
        "CTA",
        "URL",
        "\u843d\u5730\u9875",
        "query",
        "\u5173\u952e\u8bcd",
        "public_continuity_points",
    )
    request_text = "\n".join([str(captured["input"][0]["content"]), *text_blocks])
    assert all(term.casefold() not in request_text.casefold() for term in forbidden_terms)
    assert captured["reasoning"] == {"effort": "none"}
    assert result["gameplay_gambling_points"] == 40.0
    await client.aclose()


def test_visual_score_validation_clamps_dimensions_recomputes_total_and_sanitizes_output() -> None:
    result = _validated_visual_score(
        {
            "visual_priority": "not-allowed",
            "gameplay_gambling_points": 99,
            "multi_signal_style_points": -4,
            "betting_mechanism_points": 99,
            "gambling_visual_style_points": 99,
            "visual_clarity_points": 99,
            "media_quality_points": 99,
            "visual_total": 99999,
            "analysis_confidence": 9,
            "gambling_signals": ["  slot reels  ", "", 8, "x" * 121],
            "game_visual_present": 1,
            "visual_evidence": [
                {"frame_index": 0, "detail": " valid ", "ignored": "x"},
                {"frame_index": 5, "detail": "out of range"},
                {"frame_index": "x", "detail": "invalid"},
            ],
            "retrieval_hints": ["  slot ui  ", "", 9, "x" * 121],
            "uncertain": True,
        },
        frame_count=3,
    )

    assert result["visual_priority"] == "unrelated"
    assert result["gameplay_gambling_points"] == 40.0
    assert result["multi_signal_style_points"] == 0.0
    assert result["betting_mechanism_points"] == 15.0
    assert result["gambling_visual_style_points"] == 10.0
    assert result["visual_clarity_points"] == 10.0
    assert result["media_quality_points"] == 5.0
    assert result["visual_total"] == 80.0
    assert result["analysis_confidence"] == 1.0
    assert result["gambling_signals"] == ["slot reels", "8"]
    assert result["game_visual_present"] is True
    assert result["visual_evidence"] == [{"frame_index": 0, "detail": "valid"}]
    assert result["retrieval_hints"] == ["slot ui", "9"]
    assert result["uncertain"] is True


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

    assert result["media_quality_points"] == 5.0
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
