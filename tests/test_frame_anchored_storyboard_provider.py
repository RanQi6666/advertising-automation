import json

import httpx
import pytest

from backend.app.integrations.llm.responses_provider import GatewayResponsesLLMProvider
from backend.app.schemas.ai import (
    FrameAnalysis,
    FrameLanguageAnalysis,
    FrameTransitionBrief,
    FrameVisualFacts,
)

FIRST_FRAME_URL = "https://cdn.example.test/first.png"
LAST_FRAME_URL = "https://cdn.example.test/last.png"


@pytest.mark.asyncio
async def test_gateway_frame_analysis_sends_first_then_last_image() -> None:
    provider, captured = _gateway_provider_with_responses(
        {
            "first_frame": _visual_facts("opening state"),
            "last_frame": _visual_facts("ending state"),
            "transition_brief": {
                "shared_visual_facts": ["shared subject"],
                "continuity_requirements": ["preserve the supplied frames"],
                "visual_transition": "camera follows the subject",
                "narrative_arc": "opening to ending",
            },
            "language_analysis": {
                "first_frame_visible_languages": ["en"],
                "last_frame_visible_languages": ["en"],
                "recommended_output_language": "en",
                "reason": "Visible text is English.",
            },
        }
    )

    analysis = await provider.analyze_video_frame_pair(
        FIRST_FRAME_URL,
        LAST_FRAME_URL,
        12,
        "9:16",
    )

    assert analysis.language_analysis.recommended_output_language == "en"
    content = captured[0]["input"][1]["content"]
    assert content[0]["type"] == "input_text"
    assert "FIRST FRAME" in content[0]["text"]
    assert content[1] == {"type": "input_image", "image_url": FIRST_FRAME_URL}
    assert content[2]["type"] == "input_text"
    assert "LAST FRAME" in content[2]["text"]
    assert content[3] == {"type": "input_image", "image_url": LAST_FRAME_URL}
    system_prompt = captured[0]["input"][0]["content"]
    _assert_no_legacy_content(system_prompt)


@pytest.mark.asyncio
async def test_gateway_frame_analysis_normalizes_real_semi_flat_response_shape() -> None:
    provider, _captured = _gateway_provider_with_responses(
        {
            "version": "V2",
            "video_metadata": {"duration_seconds": 12, "aspect_ratio": "9:16"},
            "first_frame": {
                **_visual_facts("opening state"),
                "visible_text": [
                    {
                        "text": "GCD",
                        "location": "Front of red shirt",
                        "certainty": "partial/visible letters only",
                    }
                ],
            },
            "last_frame": _visual_facts("ending state"),
            "shared_visual_facts": ["Both frames relate to fashion."],
            "continuity_requirements": ["Maintain the fashion-focused visual theme."],
            "plausible_visual_transition": "The subject changes outfits while walking.",
            "narrative_arc": "Opening fashion look transforms into ending fashion look.",
            "visible_languages": [
                {
                    "language": "English",
                    "evidence": "Partial Latin letters on the red shirt.",
                }
            ],
            "recommended_output_language": "English",
            "recommended_output_language_reason": "The supplied frame shows English text.",
        }
    )

    analysis = await provider.analyze_video_frame_pair(
        FIRST_FRAME_URL,
        LAST_FRAME_URL,
        12,
        "9:16",
    )

    assert analysis.first_frame.visible_text == ["GCD"]
    assert analysis.transition_brief.visual_transition == (
        "The subject changes outfits while walking."
    )
    assert analysis.language_analysis.first_frame_visible_languages == ["English"]
    assert analysis.language_analysis.last_frame_visible_languages == ["English"]
    assert analysis.language_analysis.recommended_output_language == "English"
    assert analysis.language_analysis.reason == "The supplied frame shows English text."


@pytest.mark.asyncio
async def test_gateway_storyboard_sends_analysis_and_frame_rules() -> None:
    provider, captured = _gateway_provider_with_responses(
        {
            "duration_seconds": 12,
            "aspect_ratio": "9:16",
            "scenes": [
                {
                    "scene_index": 1,
                    "start_second": 0,
                    "end_second": 3,
                    "frame_anchor": "first_frame",
                    "visual": "Use the exact supplied opening state.",
                    "motion": "Slow push in.",
                    "transition_goal": "Begin from first frame.",
                    "subtitle": None,
                    "voiceover": None,
                    "sound_effects": ["soft room tone"],
                    "notes": None,
                },
                {
                    "scene_index": 2,
                    "start_second": 3,
                    "end_second": 9,
                    "frame_anchor": "transition",
                    "visual": "Bridge with the observed subject motion.",
                    "motion": "Follow movement.",
                    "transition_goal": "Connect the two supplied states.",
                    "subtitle": None,
                    "voiceover": "Optional narration.",
                    "sound_effects": ["movement swish"],
                    "notes": None,
                },
                {
                    "scene_index": 3,
                    "start_second": 9,
                    "end_second": 12,
                    "frame_anchor": "last_frame",
                    "visual": "Arrive at the exact supplied ending state.",
                    "motion": "Settle into the final composition.",
                    "transition_goal": "End at last frame.",
                    "subtitle": None,
                    "voiceover": None,
                    "sound_effects": ["music resolve"],
                    "notes": None,
                },
            ],
            "sound_design": {"music": "gentle build", "ambience": "room tone"},
            "rationale": "Bridge the supplied frames.",
        }
    )
    analysis = _analysis()

    storyboard = await provider.generate_frame_anchored_video_storyboard(
        FIRST_FRAME_URL,
        LAST_FRAME_URL,
        analysis,
        12,
        "9:16",
    )

    assert storyboard.scenes[0].frame_anchor == "first_frame"
    assert storyboard.scenes[-1].frame_anchor == "last_frame"
    content = captured[0]["input"][1]["content"]
    assert content[1] == {"type": "input_image", "image_url": FIRST_FRAME_URL}
    assert content[3] == {"type": "input_image", "image_url": LAST_FRAME_URL}
    assert '"frame_analysis"' in content[0]["text"]
    assert "first frame facts" in content[0]["text"]
    system_prompt = captured[0]["input"][0]["content"]
    _assert_no_legacy_content(system_prompt)
    assert "first_frame" in system_prompt
    assert "last_frame" in system_prompt
    assert "Do not invent" in system_prompt
    assert "duration_seconds" in system_prompt
    assert "aspect_ratio" in system_prompt
    assert "subtitle" in system_prompt
    assert "voiceover" in system_prompt
    assert "sound_effects" in system_prompt
    assert "Do not translate or rewrite text visible in either supplied image" in system_prompt


def _gateway_provider_with_responses(
    response_data: dict[str, object],
) -> tuple[GatewayResponsesLLMProvider, list[dict[str, object]]]:
    captured: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"output_text": json.dumps(response_data)})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://model.example.test/v1",
    )
    provider = GatewayResponsesLLMProvider(
        api_key="gateway-key",
        base_url="https://model.example.test/v1",
        model="gpt-5.5",
        http_client=client,
    )
    return provider, captured


def _visual_facts(state: str) -> dict[str, object]:
    return {
        "visible_subjects": ["subject"],
        "visible_text": ["Visible text"],
        "environment": "environment",
        "composition": "composition",
        "camera_perspective": "eye level",
        "visual_style": "natural",
        "color_and_lighting": "soft daylight",
        "opening_state": state,
        "ending_state": state,
    }


def _analysis() -> FrameAnalysis:
    return FrameAnalysis(
        first_frame=FrameVisualFacts(**_visual_facts("first frame facts")),
        last_frame=FrameVisualFacts(**_visual_facts("last frame facts")),
        transition_brief=FrameTransitionBrief(
            shared_visual_facts=["shared subject"],
            continuity_requirements=["retain visible image text"],
            visual_transition="camera follows subject motion",
            narrative_arc="first frame facts to last frame facts",
        ),
        language_analysis=FrameLanguageAnalysis(
            first_frame_visible_languages=["en"],
            last_frame_visible_languages=["en"],
            recommended_output_language="en",
            reason="Visible text is English.",
        ),
    )


def _assert_no_legacy_content(system_prompt: str) -> None:
    lowered = system_prompt.lower()
    for forbidden in (
        "meta/facebook",
        "creative_strategy",
        "product_name",
        "boss",
        "vip",
        "vfx",
        "3a",
    ):
        assert forbidden not in lowered
