from __future__ import annotations

import json

import httpx
import pytest

from backend.app.core.config import get_settings
from backend.app.schemas.ad_research import CollectorAd
from backend.app.services.ad_research_media import PreparedAdMedia
from backend.app.services.ad_research_model import (
    AdResearchModel,
    RedisGlobalLimiter,
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
                        "core_gambling_points": 40,
                        "reward_ui_points": 8,
                        "gambling_style_points": 4,
                        "casino_context_points": 2,
                        "media_quality_points": 5,
                        "analysis_confidence": 0.8,
                        "visible_elements": ["slot reels", "coins"],
                        "visual_evidence": [{"frame_index": 0, "detail": "slot reels are visible"}],
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


@pytest.mark.asyncio
async def test_model_planner_uses_gateway_model_and_no_reasoning(monkeypatch) -> None:
    captured: dict[str, object] = {}
    model, client = gateway_model_that_captures_request(monkeypatch, captured)

    async def complete_json(**kwargs):
        return {"queries": ["rummy bonus"]}

    model._complete_json = complete_json
    queries = await model.plan_queries(
        country="IN", category="gambling", seed_keywords=["rummy"], round_number=1
    )

    assert queries == ["rummy bonus"]
    await client.aclose()


@pytest.mark.asyncio
async def test_visual_score_sends_only_media_metadata_and_three_input_images(
    monkeypatch, tmp_path
) -> None:
    captured: dict[str, object] = {}
    model, client = gateway_model_that_captures_request(monkeypatch, captured)
    media = prepared_media_with_three_frames(tmp_path)

    result = await model.score_visual(
        category="gambling",
        candidate=CollectorAd(
            ad_library_id="ad-1",
            text_variants=["must not be sent"],
            headline="must not be sent",
            cta_text="must not be sent",
            advertiser_name="must not be sent",
            landing_url="https://must-not-be-sent.example",
            days_running=3,
            duration_seconds=15,
        ),
        media=media,
    )

    user_content = captured["input"][1]["content"]
    text_blocks = [part["text"] for part in user_content if part["type"] == "input_text"]
    assert len([part for part in user_content if part["type"] == "input_image"]) == 3
    assert all("must not be sent" not in block for block in text_blocks)
    assert captured["reasoning"] == {"effort": "none"}
    assert result["core_gambling_points"] == 40.0
    await client.aclose()


def test_visual_score_validation_clamps_points_and_removes_invalid_evidence() -> None:
    result = _validated_visual_score(
        {
            "core_gambling_points": 99,
            "reward_ui_points": -4,
            "gambling_style_points": 99,
            "casino_context_points": 99,
            "media_quality_points": 99,
            "analysis_confidence": 9,
            "visible_elements": "not-a-list",
            "visual_evidence": [
                {"frame_index": 0, "detail": "valid"},
                {"frame_index": 5, "detail": "out of range"},
                {"frame_index": "x", "detail": "invalid"},
            ],
            "recommendation": "keep",
            "category_match": True,
            "is_obviously_unrelated": False,
            "category_confidence": 1,
        },
        frame_count=3,
    )

    assert result["core_gambling_points"] == 40.0
    assert result["reward_ui_points"] == 0.0
    assert result["gambling_style_points"] == 15.0
    assert result["casino_context_points"] == 10.0
    assert result["media_quality_points"] == 5.0
    assert result["analysis_confidence"] == 1.0
    assert result["visible_elements"] == []
    assert result["visual_evidence"] == [{"frame_index": 0, "detail": "valid"}]
    for deprecated_key in (
        "recommendation",
        "category_match",
        "is_obviously_unrelated",
        "category_confidence",
    ):
        assert deprecated_key not in result


@pytest.mark.asyncio
async def test_mock_visual_score_does_not_read_candidate_text(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    get_settings.cache_clear()
    model = AdResearchModel(limiter=Limiter())

    result = await model.score_visual(
        category="gambling",
        candidate=CollectorAd(
            ad_library_id="ad-1",
            text_variants=["this must never control mock score"],
            headline="same",
            cta_text="same",
        ),
        media=prepared_media_with_three_frames(tmp_path),
    )

    assert result["media_quality_points"] == 5.0
    assert result["visual_total"] == 5.0


@pytest.mark.asyncio
async def test_model_planner_receives_visual_feedback(monkeypatch) -> None:
    captured: dict[str, object] = {}
    model, client = gateway_model_that_captures_request(monkeypatch, captured)

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"output_text": '{"queries":["short promo"]}'})

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
    }

    queries = await model.plan_queries(
        country="IN",
        category="gambling",
        seed_keywords=[],
        round_number=2,
        gap_summary=gap_summary,
    )

    assert queries == ["short promo"]
    system_prompt = captured["input"][0]["content"]
    assert "visible elements and visual style" in system_prompt
    user_payload = json.loads(captured["input"][1]["content"])
    assert user_payload["gap_summary"]["high_score_visible_elements"] == ["slot reels", "coins"]
    await client.aclose()
