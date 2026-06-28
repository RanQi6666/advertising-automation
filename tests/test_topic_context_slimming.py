import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from backend.app.core.config import get_settings
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.topic import ContentTopic
from backend.app.integrations.llm.mock_provider import MockLLMProvider
from backend.app.integrations.llm.openai_provider import (
    OpenAILLMProvider,
    _topic_stream_system_prompt,
    _TopicNDJSONStreamParser,
)
from backend.app.schemas.ai import TopicCandidate
from backend.app.services.topic_service import (
    TopicService,
    _angle_plan_item_for_candidate,
)


@pytest.fixture(autouse=True)
def mock_llm_provider(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_topic_service_builds_slim_signals_without_raw_work_order() -> None:
    service = TopicService()
    long_excerpt = "landing page detail " * 200
    campaign = Campaign(
        id="campaign-1",
        name="India TV campaign",
        objective="purchase",
        product_name="India TV",
        audience_description="male age 25-45",
        metadata_json={
            "work_order": {
                "raw_content": "SECRET RAW WORK ORDER SHOULD NOT BE SENT",
                "country": "IN",
                "media": "facebook",
                "landing_url": "https://www.example.com/tv",
                "parsed_fields": {
                    "event_name": "purchase",
                    "age_min": 25,
                    "age_max": 45,
                    "gender": "male",
                    "audience_description_raw": "male age 25-45",
                },
            },
            "landing_page": {
                "url": "https://www.example.com/tv",
                "status": "fetched",
                "title": "Streaming TV for families",
                "description": "HD entertainment at home",
                "text_excerpt": long_excerpt,
                "extracted_data": {
                    "headings": [
                        "Live TV channels",
                        "Easy home setup",
                        "HD entertainment",
                    ]
                },
            },
        },
    )

    signals = await service._build_effective_signals(  # noqa: SLF001
        session=None,  # type: ignore[arg-type]
        campaign=campaign,
        request_signals={
            "integration": "publishing_jump_workflow",
            "topic_revision_feedback": "Avoid price-led hooks.",
        },
    )

    serialized = json.dumps(signals, ensure_ascii=False)
    assert "SECRET RAW WORK ORDER" not in serialized
    assert "raw_content" not in serialized
    assert signals["work_order"]["landing_domain"] == "example.com"
    assert signals["work_order"]["event_name"] == "purchase"
    assert signals["landing_page"]["domain"] == "example.com"
    assert len(signals["landing_page"]["text_excerpt"]) <= 600
    assert signals["selling_points"][:2] == ["Live TV channels", "Easy home setup"]
    assert signals["topic_revision_feedback"] == "Avoid price-led hooks."


@pytest.mark.asyncio
async def test_topic_service_adds_game_creative_strategy_to_signals() -> None:
    service = TopicService()
    mini_game_brief = (
        "\u5c0f\u6e38\u620f\u6d41\u91cf\u6c60\uff0c\u6700\u7ec8"
        "\u5bfc\u5230 GAJA \u91cc\u7684\u5c0f\u6e38\u620f\u5408\u96c6"
    )
    campaign = Campaign(
        id="campaign-gaja",
        name="GAJA777 India",
        objective="first recharge",
        product_name="GAJA777",
        audience_description="India game users age 18-65",
        metadata_json={
            "work_order": {
                "raw_content": mini_game_brief,
                "country": "IN",
                "media": "fb",
                "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
                "parsed_fields": {
                    "event_name": "first recharge",
                    "age_min": 18,
                    "age_max": 65,
                    "brief": "Mini game challenge with level rewards",
                },
            },
            "landing_page": {
                "url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
                "status": "fetched",
                "title": "GAJA777 game hub",
                "description": "Casual game hub with challenge rewards",
            },
        },
    )

    signals = await service._build_effective_signals(  # noqa: SLF001
        session=None,  # type: ignore[arg-type]
        campaign=campaign,
        request_signals={"integration": "publishing_jump_workflow"},
    )

    assert signals["creative_strategy"]["schema_version"] == "creative_strategy.v2"
    assert signals["creative_strategy"]["vertical"] == "game"
    assert len(signals["creative_strategy"]["topic_angle_plan"]) == 3
    assert signals["creative_strategy"]["topic_angle_plan"][0]["angle_type"] == "challenge_failure"
    serialized = json.dumps(signals, ensure_ascii=False)
    assert "raw_content" not in serialized
    assert mini_game_brief not in serialized


def test_topic_source_data_promotes_creative_strategy_for_downstream() -> None:
    topic = TopicService()._topic_from_candidate(  # noqa: SLF001
        campaign_id="campaign-gaja",
        candidate=TopicCandidate(
            title="GAJA777 game hub",
            angle="Lead with easy platform navigation.",
            audience="India users",
            selling_points=["Casual game hub"],
            risk_notes="Avoid outcome promises.",
            rationale="Keeps the topic aligned with GAJA platform traffic.",
            score=0.9,
        ),
        signals={"creative_strategy": {"template_id": "gaja_brand"}},
        streamed=True,
        provider="mock",
        model="mock-model",
    )

    assert topic.source_data["creative_strategy"]["template_id"] == "gaja_brand"


def test_topic_source_data_records_angle_plan_slot() -> None:
    strategy = {
        "schema_version": "creative_strategy.v2",
        "vertical": "game",
        "topic_angle_plan": [
            {
                "slot": 1,
                "angle_type": "challenge_failure",
                "purpose": "Test failure hook.",
                "avoid_repeating": ["reward_burst"],
            }
        ],
    }

    topic = TopicService()._topic_from_candidate(  # noqa: SLF001
        campaign_id="campaign-game",
        candidate=TopicCandidate(
            title="Can you beat level 10?",
            angle="challenge_failure: Show the failed attempt.",
            angle_type="challenge_failure",
            audience="game users",
            selling_points=["Fast challenge"],
            risk_notes="Avoid guarantees.",
            rationale="Uses slot 1.",
            score=0.9,
        ),
        signals={"creative_strategy": strategy},
        streamed=False,
        provider="mock",
        model="mock-model",
        angle_plan_item=strategy["topic_angle_plan"][0],
    )

    assert topic.source_data["topic_angle"]["slot"] == 1
    assert topic.source_data["topic_angle"]["angle_type"] == "challenge_failure"


def test_angle_plan_item_prefers_unique_angle_type_match_over_position() -> None:
    plan = [
        {
            "slot": 1,
            "angle_type": "challenge_failure",
            "purpose": "Failed attempt first.",
        },
        {
            "slot": 2,
            "angle_type": "comeback_growth",
            "purpose": "Weak to strong progression.",
        },
        {
            "slot": 3,
            "angle_type": "reward_burst",
            "purpose": "Reward payoff reveal.",
        },
    ]

    matched = _angle_plan_item_for_candidate(
        plan,
        0,
        TopicCandidate(
            title="Big reward after the win",
            angle="reward_burst: Show the payoff after the challenge.",
            angle_type="reward_burst",
            audience="game users",
            selling_points=["Reward loop"],
            risk_notes="Avoid guarantees.",
            rationale="Should resolve by angle type, not slot order.",
            score=0.84,
        ),
    )

    assert matched == plan[2]


@pytest.mark.asyncio
async def test_mock_provider_uses_strategy_plan_angle_types() -> None:
    provider = MockLLMProvider()
    campaign = Campaign(
        id="campaign-game",
        name="Game campaign",
        objective="purchase",
        product_name="Game Hub",
        audience_description="game users",
        metadata_json={},
    )
    signals = {
        "creative_strategy": {
            "schema_version": "creative_strategy.v2",
            "topic_angle_plan": [
                {
                    "slot": 1,
                    "angle_type": "challenge_failure",
                    "purpose": "Open on the failed attempt.",
                },
                {
                    "slot": 2,
                    "angle_type": "comeback_growth",
                    "purpose": "Show the improvement arc.",
                },
                {
                    "slot": 3,
                    "angle_type": "reward_burst",
                    "purpose": "Land on the reward reveal.",
                },
            ]
        }
    }

    topics = await provider.generate_topics(campaign=campaign, limit=3, signals=signals)

    assert [topic.angle_type for topic in topics] == [
        "challenge_failure",
        "comeback_growth",
        "reward_burst",
    ]
    assert topics[0].angle.startswith("challenge_failure: Open on the failed attempt.")
    assert topics[1].angle.startswith("comeback_growth: Show the improvement arc.")
    assert topics[2].angle.startswith("reward_burst: Land on the reward reveal.")


@pytest.mark.asyncio
async def test_topic_service_fetches_landing_snapshot_before_generation() -> None:
    service = TopicService()
    analyzed_urls: list[str] = []

    class FakeLandingPageService:
        async def get_latest_snapshot(self, session, campaign_id: str):  # noqa: ANN001
            return None

        async def analyze_campaign_landing_page(self, session, campaign_id: str, payload):  # noqa: ANN001
            analyzed_urls.append(str(payload.url))
            return SimpleNamespace(
                id="snapshot-1",
                campaign_id=campaign_id,
                work_order_id=None,
                url=str(payload.url),
                status="fetched",
                http_status=200,
                title="Play on PC and Android",
                description="Interactive games for quick breaks",
                text_content="Use free time to play interactive games on PC and Android.",
                extracted_data={"headings": ["PC and Android support", "Quick interactive play"]},
                fetched_at=datetime.now(UTC),
                metadata_json={"source": "topic_generation"},
            )

    service.landing_pages = FakeLandingPageService()  # type: ignore[assignment]
    campaign = Campaign(
        id="campaign-1",
        name="India game campaign",
        objective="purchase",
        product_name="India game",
        audience_description="India users with free time",
        metadata_json={
            "work_order": {
                "country": "IN",
                "media": "facebook",
                "landing_url": "https://www.example.com/game",
                "parsed_fields": {"event_name": "purchase"},
            },
        },
    )

    signals = await service._build_effective_signals(  # noqa: SLF001
        session=None,  # type: ignore[arg-type]
        campaign=campaign,
        request_signals={"integration": "publishing_jump_workflow"},
    )

    assert analyzed_urls == ["https://www.example.com/game"]
    assert signals["landing_page"]["status"] == "fetched"
    assert signals["landing_page"]["title"] == "Play on PC and Android"
    assert "PC and Android support" in signals["selling_points"]


@pytest.mark.asyncio
async def test_openai_topic_generation_sends_compact_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAILLMProvider(api_key="test-key", model="test-model")
    captured: dict[str, object] = {}

    async def fake_json_completion(system: str, user: str) -> dict:
        captured["system"] = system
        captured["payload"] = json.loads(user)
        return {
            "topics": [
                {
                    "title": "Family streaming hook",
                    "angle": "Lead with home entertainment.",
                    "audience": "male age 25-45",
                    "selling_points": ["HD entertainment"],
                    "risk_notes": "Avoid unsupported channel claims.",
                    "rationale": "Fits the campaign objective.",
                    "score": 0.85,
                }
            ]
        }

    monkeypatch.setattr(provider, "_json_completion", fake_json_completion)

    long_excerpt = "full landing page copy " * 300
    campaign = Campaign(
        id="campaign-1",
        name="India TV campaign",
        objective="purchase",
        product_name="India TV",
        audience_description="male age 25-45",
        metadata_json={
            "raw_content": "CAMPAIGN METADATA SHOULD NOT BE SENT",
            "landing_page": {"text_excerpt": long_excerpt},
        },
    )

    await provider.generate_topics(
        campaign=campaign,
        limit=1,
        signals={
            "work_order": {
                "raw_content": "SECRET RAW WORK ORDER SHOULD NOT BE SENT",
                "country": "IN",
                "landing_url": "https://www.example.com/tv",
                "parsed_fields": {
                    "event_name": "purchase",
                    "audience_description_raw": "male age 25-45",
                },
            },
            "landing_page": {
                "url": "https://www.example.com/tv",
                "text_excerpt": long_excerpt,
                "extracted_data": {"headings": ["Live TV", "HD shows"]},
            },
            "previous_topics": [
                {
                    "title": "Old topic",
                    "angle": "Old angle",
                    "risk_notes": "x" * 1000,
                    "ignored": "SHOULD NOT BE SENT",
                }
            ],
            "topic_revision_feedback": "Make it more family-oriented.",
            "creative_strategy": {
                "schema_version": "creative_strategy.v2",
                "vertical": "game",
                "topic_angle_plan": [
                    {
                        "slot": 1,
                        "angle_type": "challenge_failure",
                        "purpose": "Test whether failure and challenge hooks drive curiosity.",
                        "avoid_repeating": ["comeback_growth", "reward_burst"],
                    },
                    {
                        "slot": 2,
                        "angle_type": "comeback_growth",
                        "purpose": "Test weak-to-strong or wrong-to-right progression.",
                        "avoid_repeating": ["challenge_failure", "reward_burst"],
                    },
                    {
                        "slot": 3,
                        "angle_type": "reward_burst",
                        "purpose": "Test visual satisfaction, rewards, upgrades, and payoff.",
                        "avoid_repeating": ["challenge_failure", "comeback_growth"],
                    },
                ],
                "copy_guidance": {
                    "language": "English",
                    "tone": "clear, specific, and culturally neutral",
                },
                "image_guidance": {
                    "composition": "Use a gameplay or challenge-first visual with a clear payoff.",
                },
                "video_guidance": {
                    "opening": "Lead with a visible challenge or failed attempt.",
                },
                "compliance_guardrails": ["Do not invent local trending topics."],
                "ignored": "SHOULD NOT BE SENT",
            },
            "unrelated_large_blob": "SHOULD NOT BE SENT",
        },
    )

    payload = captured["payload"]
    assert isinstance(payload, dict)
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "metadata" not in payload["campaign"]  # type: ignore[index]
    assert "raw_content" not in serialized
    assert "SECRET RAW WORK ORDER" not in serialized
    assert "CAMPAIGN METADATA" not in serialized
    assert "SHOULD NOT BE SENT" not in serialized
    signals = payload["signals"]  # type: ignore[index]
    assert signals["work_order"]["landing_domain"] == "example.com"  # type: ignore[index]
    assert len(signals["landing_page"]["text_excerpt"]) <= 600  # type: ignore[index]
    assert signals["selling_points"][:2] == ["Live TV", "HD shows"]  # type: ignore[index]
    assert signals["creative_strategy"]["schema_version"] == "creative_strategy.v2"  # type: ignore[index]
    assert "topic_angle_plan" in signals["creative_strategy"]  # type: ignore[operator]
    assert "raw_content" not in json.dumps(signals["creative_strategy"], ensure_ascii=False)  # type: ignore[index]
    assert "ignored" not in signals["creative_strategy"]  # type: ignore[index]
    assert "creative_strategy" in captured["system"]  # type: ignore[operator]
    assert "mandatory" in captured["system"]  # type: ignore[operator]


@pytest.mark.asyncio
async def test_openai_copy_payload_includes_compact_v2_strategy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAILLMProvider(api_key="test-key", model="test-model")
    captured: dict[str, object] = {}

    async def fake_json_completion(system: str, user: str) -> dict:
        captured["system"] = system
        captured["payload"] = json.loads(user)
        return {
            "body": "A clear daily-use message.",
            "primary_text": "A clear daily-use message.",
            "headline": "Simple daily glow",
            "description": "Designed for busy routines.",
            "cta": "Shop Now",
        }

    monkeypatch.setattr(provider, "_json_completion", fake_json_completion)
    strategy = {
        "schema_version": "creative_strategy.v2",
        "vertical": "ecommerce",
        "market_context": {"country_code": "SG", "language": "English"},
        "audience_lens": {"age_range": "25-34", "gender": "Female"},
        "raw_content": "SHOULD NOT LEAK",
    }
    campaign = Campaign(
        id="campaign-1",
        name="Glow Serum",
        product_name="Glow Serum",
        metadata_json={
            "creative_strategy": strategy,
            "raw_content": "CAMPAIGN RAW SHOULD NOT LEAK",
            "work_order": {
                "raw_content": "WORK ORDER RAW SHOULD NOT LEAK",
                "country": "Singapore",
                "landing_url": "https://shop.example.sg/products/glow-serum",
            },
            "landing_page": {
                "title": "Glow Serum",
                "text_excerpt": "landing copy " * 300,
            },
        },
    )
    topic = ContentTopic(
        id="topic-1",
        campaign_id="campaign-1",
        title="Busy-day skincare",
        angle="scenario_resonance",
        source_data={"creative_strategy": strategy},
    )

    await provider.generate_copy(campaign=campaign, topic=topic, constraints={})

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["creative_strategy"]["schema_version"] == "creative_strategy.v2"
    payload_text = json.dumps(payload, ensure_ascii=False)
    assert "SHOULD NOT LEAK" not in payload_text
    assert "CAMPAIGN RAW SHOULD NOT LEAK" not in payload_text
    assert "WORK ORDER RAW SHOULD NOT LEAK" not in payload_text
    assert "raw_content" not in payload_text
    assert payload["campaign"]["metadata"]["landing_page"]["title"] == "Glow Serum"
    assert len(payload["campaign"]["metadata"]["landing_page"]["text_excerpt"]) <= 600
    assert "creative_strategy.v2" in captured["system"]


def test_topic_stream_prompt_mentions_creative_strategy() -> None:
    prompt = _topic_stream_system_prompt()

    assert "creative_strategy" in prompt
    assert "mandatory" in prompt
    assert "angle_type" in prompt
    assert "topic_angle_plan" in prompt
    assert '"angle_type":"..."' in prompt


def test_topic_stream_parser_yields_ndjson_topics_incrementally() -> None:
    parser = _TopicNDJSONStreamParser(limit=3)
    line_one = json.dumps(
        {
            "type": "topic",
            "index": 1,
            "topic": {
                "title": "Family TV hook",
                "angle": "Lead with home entertainment.",
                "audience": "families",
                "selling_points": ["HD entertainment"],
                "risk_notes": "Avoid unsupported claims.",
                "rationale": "First candidate can be shown immediately.",
                "score": 0.86,
            },
        },
        ensure_ascii=False,
    )
    line_two = json.dumps(
        {
            "type": "topic",
            "index": 2,
            "topic": {
                "title": "Simple setup hook",
                "angle": "Focus on easy setup.",
                "audience": "new users",
                "selling_points": ["Easy setup"],
                "risk_notes": "Keep claims review-safe.",
                "rationale": "Second candidate follows in a later chunk.",
                "score": 0.8,
            },
        },
        ensure_ascii=False,
    )

    first = parser.feed(line_one + "\n" + line_two[:24])
    second = parser.feed(line_two[24:] + "\n{\"type\":\"done\"}\n")

    assert [candidate.title for candidate in first] == ["Family TV hook"]
    assert [candidate.title for candidate in second] == ["Simple setup hook"]
    assert parser.finish() == []
