import json
import logging

import httpx
import pytest

from backend.app.core.errors import ProviderError
from backend.app.integrations.llm.responses_provider import GatewayResponsesLLMProvider
from backend.app.schemas.ai import (
    FrameAnalysis,
    FrameLanguageAnalysis,
    FrameTransitionBrief,
    FrameVisualFacts,
    ReferenceVideoFrame,
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
async def test_gateway_joint_analysis_sends_reference_frames_chronologically() -> None:
    provider, captured = _gateway_provider_with_responses(
        {
            "first_frame": _visual_facts("opening state"),
            "last_frame": _visual_facts("ending state"),
            "transition_brief": {
                "shared_visual_facts": ["shared subject"],
                "continuity_requirements": ["preserve target frame truth"],
                "visual_transition": "adapt the reference motion",
                "narrative_arc": "opening to ending",
            },
            "language_analysis": {
                "first_frame_visible_languages": [],
                "last_frame_visible_languages": [],
                "recommended_output_language": "en",
                "reason": "No target-frame text requires another language.",
            },
            "reference_video_analysis": _reference_video_analysis_data(),
        }
    )
    reference_frames = [
        ReferenceVideoFrame(timestamp_seconds=0, image_url="data:image/jpeg;base64,AAA"),
        ReferenceVideoFrame(timestamp_seconds=2, image_url="data:image/jpeg;base64,BBB"),
        ReferenceVideoFrame(timestamp_seconds=5.8, image_url="data:image/jpeg;base64,CCC"),
    ]

    analysis = await provider.analyze_video_frame_pair(
        FIRST_FRAME_URL,
        LAST_FRAME_URL,
        12,
        "9:16",
        reference_frames=reference_frames,
        reference_video_duration_seconds=5.8,
        reference_video_sample_interval_seconds=2,
    )

    assert analysis.reference_video_analysis is not None
    assert analysis.reference_video_analysis.adapted_constraints.subject_presence.strength == (
        "required"
    )
    content = captured[0]["input"][1]["content"]
    assert content[1] == {"type": "input_image", "image_url": FIRST_FRAME_URL}
    assert content[3] == {"type": "input_image", "image_url": LAST_FRAME_URL}
    assert "REFERENCE FRAME 0.00s" in content[4]["text"]
    assert content[5] == {"type": "input_image", "image_url": reference_frames[0].image_url}
    assert "REFERENCE FRAME 2.00s" in content[6]["text"]
    assert content[7] == {"type": "input_image", "image_url": reference_frames[1].image_url}
    assert "REFERENCE FRAME 5.80s" in content[8]["text"]
    assert content[9] == {"type": "input_image", "image_url": reference_frames[2].image_url}
    system_prompt = captured[0]["input"][0]["content"]
    assert "subject presence" in system_prompt.lower()
    assert "required" in system_prompt.lower()
    assert "camera" in system_prompt.lower()
    assert "preferred" in system_prompt.lower()
    assert "do not copy" in system_prompt.lower()
    for excluded in ("audio", "speech", "lyrics", "voiceover", "transcript"):
        assert excluded in system_prompt.lower()


@pytest.mark.asyncio
async def test_gateway_frame_analysis_normalizes_observed_reference_text_shape() -> None:
    provider, _captured = _gateway_provider_with_responses(
        {
            "first_frame": _visual_facts("opening state"),
            "last_frame": _visual_facts("ending state"),
            "reference_video_analysis": {
                "duration_seconds": 10.0,
                "sample_interval_seconds": 2.0,
                "segments": [
                    {
                        "start_second": 0.0,
                        "end_second": 2.0,
                        "subject_presence": (
                            "The person remains visible in a medium full-body shot."
                        ),
                        "camera": "A gentle forward push follows the person.",
                        "transition": "Continuous movement carries into the next beat.",
                        "effects": "Soft light trails accent the motion.",
                        "confidence": "high",
                    }
                ],
                "adapted_constraints": {
                    "subject_presence": "Keep the target person present for most of the video.",
                    "camera_pattern": (
                        "Prefer a gentle forward push where the target frames allow it."
                    ),
                    "transition_pattern": "Prefer continuous movement between beats.",
                    "effects_pattern": (
                        "Prefer restrained light-trail effects without copying reference content."
                    ),
                },
            },
        }
    )

    analysis = await provider.analyze_video_frame_pair(
        FIRST_FRAME_URL,
        LAST_FRAME_URL,
        10,
        "9:16",
        reference_frames=[
            ReferenceVideoFrame(timestamp_seconds=0, image_url="data:image/jpeg;base64,AAA")
        ],
        reference_video_duration_seconds=10,
        reference_video_sample_interval_seconds=2,
    )

    reference = analysis.reference_video_analysis
    assert reference is not None
    assert reference.segments[0].subject_presence.state == (
        "The person remains visible in a medium full-body shot."
    )
    assert reference.segments[0].camera.movement == "A gentle forward push follows the person."
    assert reference.segments[0].transition.description == (
        "Continuous movement carries into the next beat."
    )
    assert reference.segments[0].effects == ["Soft light trails accent the motion."]
    assert reference.adapted_constraints.subject_presence.strength == "required"
    assert reference.adapted_constraints.camera_pattern.strength == "preferred"
    assert reference.adapted_constraints.transition_pattern.strength == "preferred"
    assert reference.adapted_constraints.effects_pattern.strength == "preferred"


@pytest.mark.asyncio
async def test_gateway_frame_analysis_accepts_chronological_segments_alias() -> None:
    provider, _captured = _gateway_provider_with_responses(
        {
            "first_frame": _visual_facts("opening state"),
            "last_frame": _visual_facts("ending state"),
            "reference_video_analysis": {
                "duration_seconds": 10.0,
                "sample_interval_seconds": 2.0,
                "chronological_segments": _reference_video_analysis_data()["segments"],
                "adapted_constraints": _reference_video_analysis_data()["adapted_constraints"],
            },
        }
    )

    analysis = await provider.analyze_video_frame_pair(
        FIRST_FRAME_URL,
        LAST_FRAME_URL,
        10,
        "9:16",
        reference_frames=[
            ReferenceVideoFrame(timestamp_seconds=0, image_url="data:image/jpeg;base64,AAA")
        ],
        reference_video_duration_seconds=10,
        reference_video_sample_interval_seconds=2,
    )

    reference = analysis.reference_video_analysis
    assert reference is not None
    assert len(reference.segments) == 1
    assert reference.segments[0].start_second == 0
    assert reference.segments[0].end_second == 2


@pytest.mark.asyncio
async def test_gateway_frame_analysis_rejects_reference_analysis_without_segments() -> None:
    provider, _captured = _gateway_provider_with_responses(
        {
            "first_frame": _visual_facts("opening state"),
            "last_frame": _visual_facts("ending state"),
            "reference_video_analysis": {
                "duration_seconds": 10.0,
                "sample_interval_seconds": 2.0,
                "segments": [],
                "adapted_constraints": _reference_video_analysis_data()["adapted_constraints"],
            },
        }
    )

    with pytest.raises(ProviderError, match="invalid frame analysis JSON"):
        await provider.analyze_video_frame_pair(
            FIRST_FRAME_URL,
            LAST_FRAME_URL,
            10,
            "9:16",
            reference_frames=[
                ReferenceVideoFrame(timestamp_seconds=0, image_url="data:image/jpeg;base64,AAA")
            ],
            reference_video_duration_seconds=10,
            reference_video_sample_interval_seconds=2,
        )


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
async def test_gateway_frame_analysis_logs_sanitized_reference_validation_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider, _captured = _gateway_provider_with_responses(
        {
            "first_frame": _visual_facts("opening state"),
            "last_frame": _visual_facts("ending state"),
            "reference_video_analysis": {
                "duration_seconds": 5.8,
                "sample_interval_seconds": 2,
                "segments": [],
                "unexpected_image": "data:image/jpeg;base64,DO_NOT_LOG_THIS",
            },
        }
    )

    with caplog.at_level(logging.WARNING), pytest.raises(
        ProviderError, match="invalid frame analysis JSON"
    ):
        await provider.analyze_video_frame_pair(
            FIRST_FRAME_URL,
            LAST_FRAME_URL,
            12,
            "9:16",
            reference_frames=[
                ReferenceVideoFrame(
                    timestamp_seconds=0,
                    image_url="data:image/jpeg;base64,ALSO_DO_NOT_LOG_THIS",
                )
            ],
            reference_video_duration_seconds=5.8,
            reference_video_sample_interval_seconds=2,
        )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "Frame analysis JSON validation failed" in messages
    assert "reference_video_analysis.adapted_constraints" in messages
    assert "response_shape" in messages
    assert "data:image" not in messages
    assert "DO_NOT_LOG_THIS" not in messages


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


@pytest.mark.asyncio
async def test_gateway_storyboard_prioritizes_target_truth_and_reference_constraints() -> None:
    provider, captured = _gateway_provider_with_responses(
        {
            "duration_seconds": 12,
            "aspect_ratio": "9:16",
            "scenes": [
                {
                    "scene_index": 1,
                    "start_second": 0,
                    "end_second": 8,
                    "frame_anchor": "first_frame",
                    "visual": "Keep the supplied target subject visible through the main action.",
                    "motion": "Adapt a gradual push-in.",
                    "transition_goal": "Begin from the supplied first frame.",
                    "subtitle": None,
                    "voiceover": None,
                    "sound_effects": ["light effect accent"],
                    "notes": None,
                },
                {
                    "scene_index": 2,
                    "start_second": 8,
                    "end_second": 12,
                    "frame_anchor": "last_frame",
                    "visual": "Reach the exact supplied ending state.",
                    "motion": "Settle into the final composition.",
                    "transition_goal": "End at the supplied last frame.",
                    "subtitle": None,
                    "voiceover": None,
                    "sound_effects": ["music resolve"],
                    "notes": None,
                },
            ],
            "sound_design": {"music": "instrumental", "ambience": "room tone"},
            "rationale": "Target truth takes priority.",
        }
    )
    analysis = FrameAnalysis.model_validate(
        {**_analysis().model_dump(), "reference_video_analysis": _reference_video_analysis_data()}
    )

    await provider.generate_frame_anchored_video_storyboard(
        FIRST_FRAME_URL,
        LAST_FRAME_URL,
        analysis,
        12,
        "9:16",
    )

    system_prompt = captured[0]["input"][0]["content"].lower()
    assert "target frame truth" in system_prompt
    assert "subject presence" in system_prompt
    assert "required" in system_prompt
    assert "camera" in system_prompt
    assert "transitions" in system_prompt
    assert "effects" in system_prompt
    assert "preferred" in system_prompt
    assert "advertising objective" in system_prompt


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


def _reference_video_analysis_data() -> dict[str, object]:
    return {
        "duration_seconds": 5.8,
        "sample_interval_seconds": 2,
        "segments": [
            {
                "start_second": 0,
                "end_second": 2,
                "subject_presence": {
                    "state": "continuous",
                    "visibility": "mostly_full_body",
                    "screen_position": "center",
                    "movement": "moves_forward",
                },
                "camera": {"movement": "slow_push_in", "intensity": "medium"},
                "transition": {
                    "type": "continuous_motion",
                    "description": "Movement continues into the next interval.",
                },
                "effects": ["gold particles"],
                "confidence": "high",
            }
        ],
        "adapted_constraints": {
            "subject_presence": {
                "strength": "required",
                "instruction": "Keep the target subject present for most of the video.",
            },
            "camera_pattern": {
                "strength": "preferred",
                "instruction": "Use a gradual push-in where compatible.",
            },
            "transition_pattern": {
                "strength": "preferred",
                "instruction": "Prefer continuous movement transitions.",
            },
            "effects_pattern": {
                "strength": "preferred",
                "instruction": "Adapt particle accents without copying content.",
            },
        },
    }


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
