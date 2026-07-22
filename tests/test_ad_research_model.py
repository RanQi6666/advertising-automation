import json

import httpx
import pytest

from backend.app.core.config import get_settings
from backend.app.services.ad_research_model import AdResearchModel, RedisGlobalLimiter


class Lease:
    async def release(self):
        return None


class Limiter:
    async def acquire(self, *args, **kwargs):
        return Lease()


def test_research_model_limiter_falls_back_to_celery_broker(monkeypatch) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://redis:6379/0")
    get_settings.cache_clear()
    try:
        assert RedisGlobalLimiter().redis_url == "redis://redis:6379/0"
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_model_planner_uses_gateway_model_and_no_reasoning(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "gateway")
    monkeypatch.setenv("MODEL_GATEWAY_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("MODEL_GATEWAY_API_KEY", "test-key")
    get_settings.cache_clear()
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"output_text": '{"queries":["rummy bonus"]}'})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://gateway.example/v1/"
    )
    model = AdResearchModel(http_client=client, limiter=Limiter())
    queries = await model.plan_queries(
        country="IN", category="gambling", seed_keywords=["rummy"], round_number=1
    )
    assert queries == ["rummy bonus"]
    assert captured["model"] == "gpt-5.4-mini"
    assert captured["reasoning"] == {"effort": "none"}
    await client.aclose()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_model_planner_accepts_fenced_json(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "gateway")
    monkeypatch.setenv("MODEL_GATEWAY_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("MODEL_GATEWAY_API_KEY", "test-key")
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"output_text": "```json\n{\"queries\":[\"rummy bonus\"]}\n```"},
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://gateway.example/v1/"
    )
    model = AdResearchModel(http_client=client, limiter=Limiter())
    queries = await model.plan_queries(
        country="IN", category="gambling", seed_keywords=["rummy"], round_number=1
    )
    assert queries == ["rummy bonus"]
    await client.aclose()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_model_classification_sends_thumbnail_as_responses_input_image(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "gateway")
    monkeypatch.setenv("MODEL_GATEWAY_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("MODEL_GATEWAY_API_KEY", "test-key")
    get_settings.cache_clear()
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "category_match": True,
                        "category_confidence": 0.9,
                        "business_type": "gambling",
                        "creative_relevance_score": 80,
                        "public_performance_signal_score": 70,
                        "real_money_signal_score": 0.8,
                        "is_obviously_unrelated": False,
                        "text_evidence": [],
                        "visual_evidence": [],
                        "public_signal_evidence": [],
                        "public_risk_signals": [],
                        "recommendation": "keep",
                    }
                )
            },
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://gateway.example/v1/"
    )
    model = AdResearchModel(http_client=client, limiter=Limiter())
    from backend.app.schemas.ad_research import CollectorAd

    await model.classify(
        category="gambling",
        candidate=CollectorAd(
            ad_library_id="ad-1",
            video_url="https://cdn.example/ad-1.mp4",
            thumbnail_url="https://cdn.example/ad-1.jpg",
        ),
    )

    content = captured["input"][1]["content"]
    image = next(part for part in content if part["type"] == "input_image")
    assert image == {"type": "input_image", "image_url": "https://cdn.example/ad-1.jpg"}
    await client.aclose()
    get_settings.cache_clear()
