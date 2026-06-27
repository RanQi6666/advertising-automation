import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from backend.app.core.config import get_settings
from backend.app.db.models.campaign import Campaign
from backend.app.integrations.llm.openai_provider import (
    OpenAILLMProvider,
    _topic_stream_system_prompt,
    _TopicNDJSONStreamParser,
)
from backend.app.schemas.ai import TopicCandidate
from backend.app.services.topic_service import TopicService


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
        audience_description="India users age 18-65",
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
                },
            },
            "landing_page": {
                "url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
                "status": "fetched",
                "title": "GAJA777",
                "description": "Casual game hub",
            },
        },
    )

    signals = await service._build_effective_signals(  # noqa: SLF001
        session=None,  # type: ignore[arg-type]
        campaign=campaign,
        request_signals={"integration": "publishing_jump_workflow"},
    )

    assert signals["creative_strategy"]["template_id"] == "mini_game_pool"
    assert signals["creative_strategy"]["duration_seconds"] == 12
    assert signals["creative_strategy"]["brand"]["display_name"] == "GAJA"
    assert "metallic GAJA" in str(signals["creative_strategy"])
    assert "no visible brand-number text" in str(signals["creative_strategy"])
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
                "template_id": "mini_game_pool",
                "template_name": "Mini-game pool to GAJA game hub ad",
                "duration_seconds": 12,
                "aspect_ratio": "9:16",
                "brand": {
                    "display_name": "GAJA777",
                    "landing_domain": "gaja777.game",
                },
                "first_frame": {"visual_must_include": ["light GAJA777 corner logo"]},
                "last_frame": {"cta_must_include": ["Register", "Play Now"]},
                "motion_direction": "Expand into GAJA777 game hub.",
                "compliance_guardrails": ["Meta-safe casual-game visuals"],
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
    assert signals["creative_strategy"]["template_id"] == "mini_game_pool"  # type: ignore[index]
    assert "ignored" not in signals["creative_strategy"]  # type: ignore[index]
    assert "creative_strategy" in captured["system"]  # type: ignore[operator]
    assert "mandatory" in captured["system"]  # type: ignore[operator]


def test_topic_stream_prompt_mentions_creative_strategy() -> None:
    prompt = _topic_stream_system_prompt()

    assert "creative_strategy" in prompt
    assert "mandatory" in prompt


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
