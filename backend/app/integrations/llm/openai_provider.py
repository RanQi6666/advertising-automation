import base64
import json
import logging
from collections.abc import AsyncIterator
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from openai import AsyncOpenAI
from pydantic import ValidationError

from backend.app.core.errors import ProviderError
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.topic import ContentTopic
from backend.app.integrations.llm.compliance import with_meta_ad_compliance
from backend.app.integrations.llm.language import (
    build_target_language_context,
    language_requirements_prompt,
)
from backend.app.schemas.ad_performance import AdPerformanceOptimizationWorkOrder
from backend.app.schemas.ai import (
    CopyDraftCandidate,
    FrameAnalysis,
    FrameAnchoredDirectorPlan,
    FrameAnchoredStoryboard,
    ImageBrief,
    ReferenceVideoFrame,
    TopicCandidate,
    VideoStoryboardCandidate,
    VideoStoryboardScene,
)
from backend.app.services.creative_safety_prompts import (
    creative_safety_prompt_block,
    sanitize_creative_safety_payload,
    sanitize_creative_safety_text,
)
from backend.app.services.creative_strategy_builder import compact_creative_strategy

logger = logging.getLogger(__name__)


class OpenAILLMProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        supports_video_input: bool = False,
        video_input_fps: float = 1.0,
    ) -> None:
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.supports_video_input = supports_video_input
        self.video_input_fps = video_input_fps

    async def _json_completion(self, system: str, user: Any) -> dict[str, Any]:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        content = _strip_json_markdown(content)
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise ProviderError("LLM returned invalid JSON.") from exc

    async def _vision_json_completion(self, system: str, user: Any) -> dict[str, Any]:
        return await self._json_completion(system, user)

    async def extract_delivery_fields(self, raw_content: str) -> dict[str, Any]:
        return await self._json_completion(
            system=(
                "You extract only ad-delivery fields from Chinese work orders. Return valid "
                "JSON only. Do not invent precise age, gender, or country without evidence; "
                "use suggested defaults only where instructed. The root object must include "
                "schema_version, fields, and review. fields must include landing_url, "
                "event_name, country, age_min, age_max, gender, and audience_description_raw. "
                "Each field must include value, normalized_value, status, confidence, evidence, "
                "candidates, and reason. status must be one of extracted, suggested, missing, "
                "conflict. For missing age/gender, suggest unrestricted: age fields may have "
                "null value and gender value can be '不限'. For missing event_name, suggest "
                "'流量'. landing_url must be a real URL from the text; if multiple URLs appear, "
                "mark conflict with candidates. Use event normalized values traffic, purchase, "
                "add_to_cart, lead. Use country normalized_value as ISO-2 if clear."
            ),
            user=json.dumps({"raw_content": raw_content}, ensure_ascii=False),
        )

    async def analyze_ad_performance(self, context: dict) -> dict[str, Any]:
        operator_result = _uses_facebook_operator_result(context)
        data = await self._json_completion(
            system=(
                _ad_performance_analysis_system_prompt()
                if operator_result
                else _legacy_ad_performance_analysis_system_prompt()
            ),
            user=(
                _ad_performance_user_content(
                    context,
                    supports_video_input=self.supports_video_input,
                    video_fps=self.video_input_fps,
                )
                if operator_result
                else _legacy_ad_performance_user_content(
                    context,
                    supports_video_input=self.supports_video_input,
                    video_fps=self.video_input_fps,
                )
            ),
        )
        return (
            _ad_performance_analysis_from_data(data)
            if operator_result
            else _legacy_ad_performance_analysis_from_data(data)
        )

    async def stream_ad_performance_analysis(
        self, context: dict
    ) -> AsyncIterator[dict[str, Any]]:
        operator_result = _uses_facebook_operator_result(context)
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        _ad_performance_analysis_system_prompt()
                        if operator_result
                        else _legacy_ad_performance_analysis_system_prompt()
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        _ad_performance_user_content(
                            context,
                            supports_video_input=self.supports_video_input,
                            video_fps=self.video_input_fps,
                        )
                        if operator_result
                        else _legacy_ad_performance_user_content(
                            context,
                            supports_video_input=self.supports_video_input,
                            video_fps=self.video_input_fps,
                        )
                    ),
                },
            ],
            response_format={"type": "json_object"},
            stream=True,
        )
        full_text = ""
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content or ""
            if not delta:
                continue
            full_text += delta
            yield {"type": "delta", "text": delta}

        content = _strip_json_markdown(full_text)
        try:
            data = json.loads(content or "{}")
        except json.JSONDecodeError as exc:
            raise ProviderError("LLM returned invalid JSON.") from exc
        yield {
            "type": "done",
            "analysis": (
                _ad_performance_analysis_from_data(data)
                if operator_result
                else _legacy_ad_performance_analysis_from_data(data)
            ),
            "text": content,
        }

    async def generate_topics(
        self,
        campaign: Campaign,
        limit: int,
        signals: dict,
    ) -> list[TopicCandidate]:
        target_language = build_target_language_context(campaign=campaign, signals=signals)
        compact_signals = _compact_topic_signals(signals)
        data = await self._json_completion(
            system=with_meta_ad_compliance(
                "You are an advertising strategist. Return valid JSON only with key "
                "'topics'. Each topic must include title, angle, audience, "
                "selling_points, risk_notes, rationale, and score. topic.title is "
                "user-facing and must use the target audience language. angle, audience, "
                "selling_points, risk_notes, and rationale are operator-facing planning "
                "fields and may use Simplified Chinese. If signals.topic_revision_feedback "
                "is present, treat it as mandatory operator feedback for regenerating topics: "
                "adjust the new topics to satisfy it, avoid repeating rejected "
                "previous_topics, and explain the adjustment in rationale. If "
                "signals.previous_topics is present without feedback, still avoid repeating "
                "those existing topics.\n\n"
                + _creative_strategy_system_instruction()
                + "\n\n"
                + language_requirements_prompt()
            ),
            user=json.dumps(
                {
                    "campaign": {
                        "name": campaign.name,
                        "objective": campaign.objective,
                        "product_name": campaign.product_name,
                        "audience_description": campaign.audience_description,
                    },
                    "signals": compact_signals,
                    "target_language": target_language,
                    "limit": limit,
                },
                ensure_ascii=False,
            ),
        )
        return [_topic_candidate_from_data(item) for item in data.get("topics", [])[:limit]]

    async def stream_topics(
        self,
        campaign: Campaign,
        limit: int,
        signals: dict,
    ) -> AsyncIterator[TopicCandidate]:
        target_language = build_target_language_context(campaign=campaign, signals=signals)
        compact_signals = _compact_topic_signals(signals)
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": _topic_stream_system_prompt(),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "campaign": {
                                "name": campaign.name,
                                "objective": campaign.objective,
                                "product_name": campaign.product_name,
                                "audience_description": campaign.audience_description,
                            },
                            "signals": compact_signals,
                            "target_language": target_language,
                            "limit": limit,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            stream=True,
        )

        parser = _TopicNDJSONStreamParser(limit=limit)
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content or ""
            for candidate in parser.feed(delta):
                yield candidate
        for candidate in parser.finish():
            yield candidate

    async def generate_copy(
        self,
        campaign: Campaign,
        topic: ContentTopic,
        constraints: dict,
    ) -> CopyDraftCandidate:
        target_language = build_target_language_context(campaign=campaign)
        campaign_metadata = (
            campaign.metadata_json if isinstance(campaign.metadata_json, dict) else {}
        )
        topic_source_data = topic.source_data if isinstance(topic.source_data, dict) else {}
        creative_strategy = _compact_creative_strategy(
            campaign_metadata.get("creative_strategy")
            or topic_source_data.get("creative_strategy")
        )
        data = await self._json_completion(
            system=with_meta_ad_compliance(
                "You are a direct-response Facebook copywriter. Return valid JSON only "
                "with body, primary_text, headline, description, and cta. Use the "
                "campaign, work order, and landing page context. Write compliant ad copy "
                "that is suitable for Meta/Facebook placements. All generated copy fields "
                "are user-facing and must use the target audience language.\n\n"
                + _creative_strategy_system_instruction()
                + "\n\n"
                + language_requirements_prompt()
            ),
            user=json.dumps(
                {
                    "campaign": {
                        "name": campaign.name,
                        "objective": campaign.objective,
                        "product_name": campaign.product_name,
                        "audience_description": campaign.audience_description,
                        "metadata": _compact_metadata_with_strategy(campaign_metadata),
                    },
                    "topic": {
                        "title": topic.title,
                        "angle": topic.angle,
                        "selling_points": topic.selling_points,
                        "risk_notes": topic.risk_notes,
                    },
                    "creative_strategy": creative_strategy,
                    "constraints": constraints,
                    "target_language": target_language,
                },
                ensure_ascii=False,
            ),
        )
        return _copy_candidate_from_data(data)

    async def revise_copy(
        self,
        campaign: Campaign,
        topic: ContentTopic,
        draft: CopyDraft,
        feedback: str,
        constraints: dict,
    ) -> CopyDraftCandidate:
        target_language = build_target_language_context(campaign=campaign)
        data = await self._json_completion(
            system=with_meta_ad_compliance(
                "Revise Facebook ad copy from human feedback. Return valid JSON only "
                "with body, primary_text, headline, description, and cta. Preserve the "
                "operator's business intent while rewriting risky claims into compliant "
                "language. All generated copy fields are user-facing and must use the "
                "target audience language.\n\n"
                + language_requirements_prompt()
            ),
            user=json.dumps(
                {
                    "campaign_name": campaign.name,
                    "topic": topic.title,
                    "existing_draft": draft.body,
                    "feedback": feedback,
                    "constraints": constraints,
                    "target_language": target_language,
                },
                ensure_ascii=False,
            ),
        )
        return _copy_candidate_from_data(data)

    async def generate_image_briefs(
        self,
        draft: CopyDraft,
        count: int,
        size: str,
        feedback: str | None = None,
        source_asset: CreativeAsset | None = None,
        storyboard_context: dict | None = None,
    ) -> list[ImageBrief]:
        target_language = build_target_language_context(draft_metadata=draft.metadata_json)
        compact_draft_metadata = _compact_metadata_with_strategy(draft.metadata_json)
        data = await self._json_completion(
            system=with_meta_ad_compliance(
                "Turn ad copy into concise image briefs. Return valid JSON only with "
                "key 'briefs'. Each brief must include image_index, title, short_text, "
                "visual_direction, and size. Do not generate images. The visual_direction "
                "must be safe for a downstream image model and must avoid unlicensed IP, "
                "fake UI, and unsupported claims. Each brief must describe an independent "
                "creative asset, not a platform feed screenshot. visual_direction must not "
                "mention Facebook, Meta, Instagram, social platform logos, platform "
                "UI/chrome, Sponsored labels, like/comment/share buttons, browser chrome, "
                "phone screenshots, QR codes, watermarks, or copyright marks. Focus on "
                "the product, usage context, audience benefit, composition, lighting, "
                "and readable text placement. title and short_text are user-facing if "
                "rendered on the image, so they must use the target audience language. "
                "Do not request extra visible text beyond short_text unless exact copy is "
                "provided by the user. visual_direction may use English or Simplified "
                "Chinese, but any visible text it requests must use the target audience "
                "language. If revision_feedback and source_asset are provided, generate "
                "a revised version for that single image: preserve useful continuity from "
                "the source image brief, apply the operator feedback as mandatory, and "
                "avoid repeating the rejected visual problem. If storyboard_context is "
                "provided, treat it as the primary visual plan: align each image brief to "
                "the matching scene order, preserve the scene's visual intent, subtitle, "
                "and motion, and create still images that can serve as first/last frame "
                "references for the video. If storyboard_context.keyframe_plan.mode is "
                "video_keyframe_variants, create paired variants: each group has one "
                "first-frame hook image and one last-frame resolution image for the same "
                "12-second video idea. Make each pair visually coherent while keeping the "
                "three groups distinct enough for an operator to choose between. If "
                "storyboard_context.selected_topic is provided, treat selected_topic as "
                "the selected campaign direction for every image brief. Do not distribute "
                "image briefs across creative_strategy.topic_angle_plan; use "
                "topic_angle_plan only as background strategy, and keep all briefs aligned "
                "to selected_topic.angle_type, selected_topic.topic_angle, and the "
                "approved copy unless operator feedback explicitly asks for a different "
                "direction. "
                + _keyframe_brand_aaa_image_rules()
                + creative_safety_prompt_block()
                + "\n\n"
                + _creative_strategy_system_instruction()
                + "\n\n"
                + language_requirements_prompt()
            ),
            user=_creative_payload_json(
                {
                    "copy": draft.body,
                    "headline": draft.headline,
                    "draft_metadata": compact_draft_metadata,
                    "count": count,
                    "size": size,
                    "target_language": target_language,
                    "revision_feedback": feedback,
                    "source_asset": _image_source_asset_context(source_asset),
                    "storyboard_context": _compact_image_storyboard_context(
                        storyboard_context
                    ),
                }
            ),
        )
        return [_image_brief_from_data(item) for item in data.get("briefs", [])[:count]]

    async def generate_video_storyboard(
        self,
        campaign: Campaign,
        draft: CopyDraft | None,
        assets: list[CreativeAsset],
        duration_seconds: int,
        aspect_ratio: str,
        context: dict,
        instructions: str | None = None,
    ) -> VideoStoryboardCandidate:
        target_language = build_target_language_context(campaign=campaign, context=context)
        data = await self._json_completion(
            system=with_meta_ad_compliance(
                "You are a senior short-form video ad director. Return valid JSON only "
                "with duration_seconds, aspect_ratio, scenes, and rationale. Each scene "
                "must include scene_index, start_second, end_second, visual, subtitle, "
                "motion, voiceover, source_asset_ids, and notes. Selected images are "
                "optional for storyboard generation. Use selected images only when assets "
                "are provided; do not invent unavailable image ids. If assets is "
                "non-empty, every scene's source_asset_ids must include one or more exact "
                "ids from selected_asset_ids. Never leave source_asset_ids empty when "
                "assets are provided. If assets is empty, use an empty source_asset_ids "
                "list and generate the script from campaign, copy, work order, landing "
                "context, creative_strategy, and operator instructions. Keep subtitles "
                "short, readable, and suitable for mobile feed placements. Do not script "
                "unlicensed IP, fake UI, misleading controls, platform logos/UI, or "
                "unsupported claims. Use duration_seconds as the source of truth. Compress "
                "or expand the creative_strategy.video_guidance beats to fit that duration; "
                "do not assume a fixed 30-second or 12-second structure. "
                "subtitle and voiceover are user-facing and must use the target audience "
                "language. visual, motion, notes, and rationale may use Simplified Chinese "
                "for operator review, but any visible text requested in visual must use "
                "the target audience language. "
                + creative_safety_prompt_block()
                + "\n\n"
                + _keyframe_brand_aaa_video_storyboard_rules()
                + "\n\n"
                + _creative_strategy_system_instruction()
                + "\n\n"
                + language_requirements_prompt()
            ),
            user=_creative_payload_json(
                {
                    "campaign": {
                        "name": campaign.name,
                        "objective": campaign.objective,
                        "product_name": campaign.product_name,
                        "audience_description": campaign.audience_description,
                        "metadata": _compact_metadata_with_strategy(campaign.metadata_json),
                    },
                    "copy_draft": _draft_context(draft),
                    "assets": [_asset_context(asset) for asset in assets],
                    "selected_asset_ids": [asset.id for asset in assets],
                    "duration_seconds": duration_seconds,
                    "aspect_ratio": aspect_ratio,
                    "context": _compact_metadata_with_strategy(context),
                    "instructions": instructions,
                    "target_language": target_language,
                }
            ),
        )
        return _video_storyboard_from_data(
            data=data,
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
            allowed_asset_ids={asset.id for asset in assets},
        )

    async def analyze_video_frame_pair(
        self,
        first_frame_image_url: str,
        last_frame_image_url: str,
        duration_seconds: int,
        aspect_ratio: str,
        reference_frames: list[ReferenceVideoFrame] | None = None,
        reference_video_duration_seconds: float | None = None,
        reference_video_sample_interval_seconds: float | None = None,
    ) -> FrameAnalysis:
        data = await self._vision_json_completion(
            system=_frame_analysis_system_prompt(),
            user=_frame_pair_user_content(
                first_frame_image_url,
                last_frame_image_url,
                {
                    "duration_seconds": duration_seconds,
                    "aspect_ratio": aspect_ratio,
                    "reference_video": (
                        {
                            "duration_seconds": reference_video_duration_seconds,
                            "sample_interval_seconds": reference_video_sample_interval_seconds,
                        }
                        if reference_frames
                        else None
                    ),
                },
                reference_frames=reference_frames,
            ),
        )
        return _frame_analysis_from_data(
            data,
            reference_video_duration_seconds=reference_video_duration_seconds,
            reference_video_sample_interval_seconds=reference_video_sample_interval_seconds,
        )

    async def direct_frame_anchored_video_storyboard(
        self,
        first_frame_image_url: str,
        last_frame_image_url: str,
        frame_analysis: FrameAnalysis,
        duration_seconds: int,
        aspect_ratio: str,
    ) -> FrameAnchoredDirectorPlan:
        data = await self._vision_json_completion(
            system=_frame_anchored_director_system_prompt(),
            user=_frame_pair_user_content(
                first_frame_image_url,
                last_frame_image_url,
                {
                    "duration_seconds": duration_seconds,
                    "aspect_ratio": aspect_ratio,
                    "frame_analysis": frame_analysis.model_dump(mode="json"),
                },
            ),
        )
        return _frame_anchored_director_plan_from_data(data)

    async def generate_frame_anchored_video_storyboard(
        self,
        first_frame_image_url: str,
        last_frame_image_url: str,
        frame_analysis: FrameAnalysis,
        duration_seconds: int,
        aspect_ratio: str,
    ) -> FrameAnchoredStoryboard:
        data = await self._vision_json_completion(
            system=_frame_anchored_storyboard_system_prompt(),
            user=_frame_pair_user_content(
                first_frame_image_url,
                last_frame_image_url,
                {
                    "duration_seconds": duration_seconds,
                    "aspect_ratio": aspect_ratio,
                    "frame_analysis": frame_analysis.model_dump(mode="json"),
                },
            ),
        )
        return _frame_anchored_storyboard_from_data(
            data,
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
        )

    async def stream_video_storyboard_text(
        self,
        campaign: Campaign,
        draft: CopyDraft | None,
        assets: list[CreativeAsset],
        duration_seconds: int,
        aspect_ratio: str,
        context: dict,
        instructions: str | None = None,
    ) -> AsyncIterator[str]:
        target_language = build_target_language_context(campaign=campaign, context=context)
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": _video_storyboard_text_system_prompt(revision=False),
                },
                {
                    "role": "user",
                    "content": _creative_payload_json(
                        {
                            "campaign": {
                                "name": campaign.name,
                                "objective": campaign.objective,
                                "product_name": campaign.product_name,
                                "audience_description": campaign.audience_description,
                                "metadata": _compact_metadata_with_strategy(
                                    campaign.metadata_json
                                ),
                            },
                            "copy_draft": _draft_context(draft),
                            "assets": [_asset_context(asset) for asset in assets],
                            "selected_asset_ids": [asset.id for asset in assets],
                            "duration_seconds": duration_seconds,
                            "aspect_ratio": aspect_ratio,
                            "context": _compact_metadata_with_strategy(context),
                            "instructions": instructions,
                            "target_language": target_language,
                        }
                    ),
                },
            ],
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content or ""
            if delta:
                yield delta

    async def revise_video_storyboard(
        self,
        campaign: Campaign,
        draft: CopyDraft | None,
        assets: list[CreativeAsset],
        duration_seconds: int,
        aspect_ratio: str,
        context: dict,
        current_storyboard: list[dict],
        current_storyboard_text: str | None,
        feedback: str,
    ) -> VideoStoryboardCandidate:
        target_language = build_target_language_context(campaign=campaign, context=context)
        data = await self._json_completion(
            system=with_meta_ad_compliance(
                "You are a senior short-form video ad director revising an existing "
                "storyboard from operator feedback. Return valid JSON only with "
                "duration_seconds, aspect_ratio, scenes, and rationale. Each scene must "
                "include scene_index, start_second, end_second, visual, subtitle, motion, "
                "voiceover, source_asset_ids, and notes. Preserve the current storyboard's "
                "usable structure, timing, product intent, and approved copy unless the "
                "feedback explicitly asks to change them. Selected images are optional "
                "for storyboard revision. Apply the feedback as mandatory. Do not invent "
                "unavailable image ids. "
                "If assets is non-empty, every scene's source_asset_ids must include one "
                "or more exact ids from selected_asset_ids; preserve existing valid ids "
                "when useful and fill missing scene ids from selected_asset_ids. Never "
                "leave source_asset_ids empty when assets are provided. If assets is "
                "empty, use an empty source_asset_ids list and revise from the script, "
                "campaign, copy, work order, creative_strategy, and feedback. Keep "
                "subtitles short, readable, and suitable for mobile feed placements. Do "
                "not script unlicensed IP, fake UI, misleading controls, platform "
                "logos/UI, or unsupported claims. Use duration_seconds as the source of "
                "truth. Compress or expand the creative_strategy.video_guidance beats to "
                "fit that duration; do not assume a fixed 30-second or 12-second "
                "structure. subtitle and voiceover are user-facing "
                "and must use the target audience language. visual, motion, notes, and "
                "rationale may use Simplified Chinese for operator review, but any visible "
                "text requested in visual must use the target audience language. "
                + creative_safety_prompt_block()
                + "\n\n"
                + _keyframe_brand_aaa_video_storyboard_rules()
                + "\n\n"
                + _creative_strategy_system_instruction()
                + "\n\n"
                + language_requirements_prompt()
            ),
            user=_creative_payload_json(
                {
                    "campaign": {
                        "name": campaign.name,
                        "objective": campaign.objective,
                        "product_name": campaign.product_name,
                        "audience_description": campaign.audience_description,
                        "metadata": _compact_metadata_with_strategy(campaign.metadata_json),
                    },
                    "copy_draft": _draft_context(draft),
                    "assets": [_asset_context(asset) for asset in assets],
                    "selected_asset_ids": [asset.id for asset in assets],
                    "duration_seconds": duration_seconds,
                    "aspect_ratio": aspect_ratio,
                    "context": _compact_metadata_with_strategy(context),
                    "current_storyboard": _compact_storyboard_for_revision(current_storyboard),
                    "current_storyboard_text": _truncate(current_storyboard_text, 6000),
                    "revision_feedback": feedback,
                    "target_language": target_language,
                }
            ),
        )
        return _video_storyboard_from_data(
            data=data,
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
            allowed_asset_ids={asset.id for asset in assets},
        )

    async def stream_video_storyboard_revision_text(
        self,
        campaign: Campaign,
        draft: CopyDraft | None,
        assets: list[CreativeAsset],
        duration_seconds: int,
        aspect_ratio: str,
        context: dict,
        current_storyboard: list[dict],
        current_storyboard_text: str | None,
        feedback: str,
    ) -> AsyncIterator[str]:
        target_language = build_target_language_context(campaign=campaign, context=context)
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": _video_storyboard_text_system_prompt(revision=True),
                },
                {
                    "role": "user",
                    "content": _creative_payload_json(
                        {
                            "campaign": {
                                "name": campaign.name,
                                "objective": campaign.objective,
                                "product_name": campaign.product_name,
                                "audience_description": campaign.audience_description,
                                "metadata": _compact_metadata_with_strategy(
                                    campaign.metadata_json
                                ),
                            },
                            "copy_draft": _draft_context(draft),
                            "assets": [_asset_context(asset) for asset in assets],
                            "selected_asset_ids": [asset.id for asset in assets],
                            "duration_seconds": duration_seconds,
                            "aspect_ratio": aspect_ratio,
                            "context": _compact_metadata_with_strategy(context),
                            "current_storyboard": _compact_storyboard_for_revision(
                                current_storyboard
                            ),
                            "current_storyboard_text": _truncate(current_storyboard_text, 6000),
                            "revision_feedback": feedback,
                            "target_language": target_language,
                        }
                    ),
                },
            ],
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content or ""
            if delta:
                yield delta


def _uses_facebook_operator_result(context: dict[str, Any]) -> bool:
    contract = context.get("result_contract")
    if not isinstance(contract, dict):
        return False
    return contract.get("schema_version") == "facebook_ad_analysis_v1" and isinstance(
        contract.get("operator_sections"), list
    )


def _ad_performance_analysis_from_data(data: dict[str, Any]) -> dict[str, Any]:
    overall_decision = _dict_from_value(data.get("overall_decision"))
    copywriting = _dict_from_value(data.get("copywriting_analysis"))
    media = _dict_from_value(data.get("media_analysis"))
    market = _dict_from_value(data.get("market_intelligence"))
    normalized = {
        "summary": _short_required_text(data.get("summary"), "分析建议已生成。"),
        "overall_decision": {
            "action": _enum_value(
                overall_decision.get("action"),
                {"scale", "optimize", "monitor", "pause"},
                "monitor",
            ),
            "priority": _operator_priority(overall_decision.get("priority")),
            "main_problem": _short_required_text(
                overall_decision.get("main_problem"),
                "需要结合规则指标确认主要问题。",
            ),
        },
        "targeting_analysis": _operator_targeting_items(
            data.get("targeting_analysis")
        ),
        "adjustment_plans": _operator_adjustment_plans(data.get("adjustment_plans")),
        "copywriting_analysis": {
            "summary": _short_required_text(
                copywriting.get("summary"),
                "当前没有明确的文案补充结论。",
            ),
            "problems": _limited_text_list(copywriting.get("problems"), 3, 600),
            "suggestions": _limited_text_list(copywriting.get("suggestions"), 3, 600),
            "recommended_primary_text": _truncate(
                _coerce_optional_text(copywriting.get("recommended_primary_text")), 1200
            ),
            "recommended_headline": _truncate(
                _coerce_optional_text(copywriting.get("recommended_headline")), 1200
            ),
            "recommended_description": _truncate(
                _coerce_optional_text(copywriting.get("recommended_description")), 1200
            ),
        },
        "media_analysis": {
            "media_type": _enum_value(
                media.get("media_type"), {"image", "video", "carousel"}, "image"
            ),
            "summary": _short_required_text(
                media.get("summary"),
                "当前没有可靠的画面补充结论。",
            ),
            "improvements": _operator_media_improvements(media.get("improvements")),
        },
        "market_intelligence": {
            "status": _enum_value(
                market.get("status"),
                {"completed", "partial", "unavailable"},
                "unavailable",
            ),
            "summary": _short_required_text(
                market.get("summary"),
                "本次没有足够可靠的公开相似广告参考。",
            ),
            "references": _operator_market_references(market.get("references")),
            "limitation": _required_text(
                market.get("limitation"),
                "公开来源只能用于参考创意模式，不能验证真实投放成效。",
                600,
            ),
        },
        "data_gaps": _limited_text_list(data.get("data_gaps"), 3, 600),
    }
    return normalized


def _legacy_ad_performance_analysis_from_data(data: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "summary": _coerce_text(data.get("summary"))[:1200],
        "root_causes": _limited_text_list(data.get("root_causes"), 6, 360),
        "recommended_actions": _limited_text_list(data.get("recommended_actions"), 8, 420),
        "next_tests": _limited_text_list(data.get("next_tests"), 6, 420),
        "creative_feedback": _limited_text_list(data.get("creative_feedback"), 5, 360),
        "audience_feedback": _limited_text_list(data.get("audience_feedback"), 5, 360),
        "landing_page_feedback": _limited_text_list(data.get("landing_page_feedback"), 5, 360),
        "budget_delivery_feedback": _limited_text_list(
            data.get("budget_delivery_feedback"), 5, 360
        ),
        "risk_notes": _limited_text_list(data.get("risk_notes"), 5, 360),
        "visual_analysis": _ad_performance_visual_analysis_from_data(
            data.get("visual_analysis")
        ),
        "optimization_work_order": _ad_performance_optimization_work_order_from_data(
            data.get("optimization_work_order")
        ),
        "confidence_note": _truncate(_coerce_optional_text(data.get("confidence_note")), 500),
    }
    if not normalized["summary"]:
        normalized["summary"] = "大模型已完成分析，但没有返回明确摘要，请优先查看下方原因和建议。"
    return normalized


def _operator_targeting_items(value: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue
        result.append(
            {
                "dimension": _enum_value(
                    raw.get("dimension"),
                    {"country", "audience", "age", "gender", "device", "placement"},
                    "audience",
                ),
                "current": _required_text(raw.get("current"), "未提供", 600),
                "decision": _enum_value(
                    raw.get("decision"), {"adjust", "test", "monitor"}, "test"
                ),
                "problem": _required_text(raw.get("problem"), "需要进一步验证", 600),
                "suggestion": _required_text(
                    raw.get("suggestion"), "采用小预算对照测试", 600
                ),
                "reason": _required_text(
                    raw.get("reason"), "缺少该维度的成效拆分数据", 600
                ),
            }
        )
        if len(result) >= 3:
            break
    return result


def _operator_adjustment_plans(value: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue
        result.append(
            {
                "priority": _operator_priority(raw.get("priority")),
                "category": _enum_value(
                    raw.get("category"),
                    {
                        "landing_page",
                        "tracking",
                        "copywriting",
                        "media",
                        "targeting",
                        "budget",
                        "campaign_setup",
                    },
                    "campaign_setup",
                ),
                "title": _short_required_text(raw.get("title"), "核对投放设置"),
                "action": _required_text(raw.get("action"), "核对当前投放设置。", 600),
                "reason": _required_text(raw.get("reason"), "需要进一步验证。", 600),
                "expected_effect": _required_text(
                    raw.get("expected_effect"), "提高后续判断可靠性。", 600
                ),
                "what_to_watch": _required_text(
                    raw.get("what_to_watch"), "观察核心业务结果和单次结果成本。", 600
                ),
            }
        )
        if len(result) >= 5:
            break
    return result


def _operator_media_improvements(value: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue
        result.append(
            {
                "location": _required_text(raw.get("location"), "素材画面", 600),
                "problem": _required_text(raw.get("problem"), "画面表达可进一步优化。", 600),
                "action": _required_text(raw.get("action"), "使用对照版本验证调整。", 600),
            }
        )
        if len(result) >= 3:
            break
    return result


def _operator_market_references(value: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue
        source_url = _coerce_optional_text(raw.get("source_url"))
        if not source_url:
            continue
        result.append(
            {
                "advertiser_name": _required_text(
                    raw.get("advertiser_name"), "公开来源广告主", 600
                ),
                "source_url": _truncate(source_url, 2000) or source_url,
                "observed_pattern": _required_text(
                    raw.get("observed_pattern"), "公开页面包含可参考的创意表达。", 600
                ),
                "applicable_idea": _required_text(
                    raw.get("applicable_idea"), "仅参考创意结构并通过对照测试验证。", 600
                ),
            }
        )
        if len(result) >= 3:
            break
    return result


def _dict_from_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _enum_value(value: Any, allowed: set[str], default: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in allowed else default


def _operator_priority(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "critical":
        return "high"
    return normalized if normalized in {"high", "medium", "low"} else "medium"


def _short_required_text(value: Any, default: str) -> str:
    return _required_text(value, default, 100)


def _required_text(value: Any, default: str, max_chars: int) -> str:
    return _truncate(_coerce_optional_text(value), max_chars) or default


def _creative_payload_json(payload: dict[str, Any]) -> str:
    return json.dumps(sanitize_creative_safety_payload(payload), ensure_ascii=False)


def _keyframe_brand_aaa_image_rules() -> str:
    return (
        "\n\n"
        "Keyframe brand and 3A image rules: "
        "for every work-order type, use first-frame and last-frame images to carry "
        "the main visible text, brand lockup, logo, VIP when required, and CTA; "
        "the middle video segment should be planned as visual spectacle rather than "
        "text display. Large cinematic brand typography is allowed when the full "
        "wordmark remains readable and complete. The safe text zone begins at least "
        "10% below the top edge; keep the brand/VIP/CTA lockup above 45% image height "
        "or centered in the upper-middle safe area, with clear margins on all sides. "
        "first_frame image rule: show the visible brand logo or cleaned brand name "
        "without numeric suffix plus a visible VIP mark directly under the cleaned "
        "brand name when the brief requests VIP. For game or gambling keyframe briefs, "
        "feature a country-market strong visual character when requested: "
        "epic hero, king, warrior, bird-god-style boss, giant serpent boss, or stone "
        "guardian boss. For ecommerce briefs, keep the product or use scenario as the "
        "main subject instead. Use complete full-frame composition: the product, "
        "hero/boss when relevant, logo, VIP mark, CTA, and any visible text must stay "
        "fully inside the safe area, must not touch image edges, and must not be "
        "cropped. No letter, logo stroke, VIP badge, CTA, decorative frame, or main "
        "subject may cross or touch any image edge. "
        "last_frame image rule: return to a clear branded CTA end frame with the visible "
        "brand logo or cleaned brand name, Start, Play Now, or Explore, and a clean "
        "reward-resolution layout. Keep the brand/VIP/CTA lockup centered or upper-middle "
        "with clear margins; never place it on the bottom edge. Do not render "
        "brand-number text, cash amounts, withdrawal/recharge/balance UI, or guaranteed "
        "winning claims."
    )


def _keyframe_brand_aaa_video_storyboard_rules() -> str:
    return (
        "Keyframe brand and 3A storyboard timing rules:\n"
        "0-3s opening rule: write the first scene as a premium 3A hook continuing the "
        "first frame; place the main text, visible brand logo or cleaned brand name, "
        "VIP when required, and short hook here with an epic hero, "
        "king, warrior, bird-god-style boss, giant serpent boss, or stone guardian boss.\n"
        "3-9s middle VFX rule: VFX must be the main visual action, not a small garnish. "
        "Select 2-3 VFX library items whenever creative_strategy.middle_vfx_policy or "
        "creative_strategy.vfx_library is present. Use safe 3A spectacle beats: "
        "portal burst, golden divine "
        "light descent, screen-shake particle shockwave, boss-defeat energy break, "
        "jackpot-style feedback without money claims, Score/Points/Stars/Power rolling "
        "number effects, and slow-motion reward burst. Keep 3-9s mostly text-free: "
        "no captions, no large text, no numbers except safe Score/Points/Stars/Power "
        "effects, and no UI panels; at most use one tiny brand mark. Do not reduce "
        "3-9s to ordinary "
        "path-choice gameplay, walking, simple ground lights, or UI-like buttons. For "
        "gambling-like work orders, keep the effects premium and abstract: avoid cash "
        "amounts, real-money wording, withdrawal/recharge/balance UI, cards, dice, slot "
        "machines, casino tables, and guaranteed-win language.\n"
        "9-12s ending rule: resolve into the last frame with visible brand logo or "
        "cleaned brand name, VIP when required, Start, Play Now, or Explore CTA, "
        "and a stable branded end "
        "frame. Keep brand, VIP, and CTA inside the safe area."
    )


def _ad_performance_analysis_system_prompt() -> str:
    return (
        "You are a senior performance marketing analyst for Meta/Facebook ads. "
        "Return valid JSON only. Be concise and practical for an operator: identify "
        "the problem, the evidence boundary, and the exact action to test next. "
        "Core objective: combine caller-submitted verified Facebook delivery metrics "
        "with system-collected public similar-ad creative/proxy signals, then provide "
        "executable optimization advice. Treat submitted Meta/Facebook delivery metrics "
        "as verified performance facts. Public research cannot verify actual performance. "
        "Never claim public sources verify CTR, CPC, CPA, purchases, revenue, or ROAS. "
        "Do not claim country, age, gender, device, or placement performance without "
        "caller-submitted breakdown data. In that case, recommend a controlled test rather "
        "than asserting a winning segment. If media processing failed, do not invent visual "
        "observations. Use Simplified Chinese for operator-facing analysis, except recommended "
        "ad copy must preserve the source ad language. The root object must contain exactly: "
        "summary, overall_decision, targeting_analysis, adjustment_plans, "
        "copywriting_analysis, media_analysis, market_intelligence, data_gaps. summary must "
        "be at most 100 characters. overall_decision contains action, priority, and "
        "main_problem, but deterministic rules remain authoritative for the final action, "
        "priority, and bottleneck. targeting_analysis must contain only meaningful issues "
        "and at most three items. Each item contains dimension, current, decision, problem, "
        "suggestion, and reason. adjustment_plans must contain one to five concrete operator "
        "actions. Each plan contains priority, category, title, action, reason, "
        "expected_effect, and what_to_watch. copywriting_analysis contains summary, up to "
        "three problems, up to three suggestions, recommended_primary_text, "
        "recommended_headline, and recommended_description. media_analysis contains "
        "media_type, summary, and up to three improvements with location, problem, and "
        "action. market_intelligence contains status, summary, up to three references, and "
        "limitation. data_gaps contains at most three short items. Do not output old fields "
        "such as executive_summary, recommended_actions, optimization_work_order, or "
        "visual_analysis."
    )


def _legacy_ad_performance_analysis_system_prompt() -> str:
    return (
        "You are a senior performance marketing analyst for Meta/Facebook ads. "
        "Return valid JSON only. Analyze the ad independently first; comparisons with "
        "sibling ads are supplemental. Use Simplified Chinese and return the existing "
        "internal structure with summary, root_causes, recommended_actions, next_tests, "
        "creative_feedback, audience_feedback, landing_page_feedback, "
        "budget_delivery_feedback, risk_notes, optimization_work_order, confidence_note, "
        "and optional visual_analysis. optimization_work_order must use the existing "
        "ad_performance_optimization_work_order_v1 structure. If no visual media is "
        "attached, set visual_analysis to null."
    )


def _ad_performance_user_content(
    context: dict[str, Any],
    supports_video_input: bool = False,
    video_fps: float = 1.0,
) -> str | list[dict[str, Any]]:
    text = json.dumps(_ad_performance_public_context(context), ensure_ascii=False)
    video_url = _ad_performance_video_url(context) if supports_video_input else None
    if video_url:
        return [
            {
                "type": "text",
                "text": (
                    text
                    + "\n\nThe attached video is the actual ad creative from creative.video_url. "
                    "Inspect the video directly for media_analysis, especially the first "
                    "seconds, hook, product/context match, pacing, and drop-off risks. Do not "
                    "claim visual details that are not visible in the video."
                ),
            },
            {"type": "video_url", "video_url": {"url": video_url, "fps": video_fps}},
        ]
    local_images = _ad_performance_local_visual_data_urls(context)
    if local_images:
        creative = context.get("creative") if isinstance(context.get("creative"), dict) else {}
        creative_type = str(creative.get("creative_type") or "creative").lower()
        visual_kind = (
            " carousel cards"
            if creative_type == "carousel"
            else " keyframes"
            if len(local_images) > 1
            else ""
        )
        return [
            {
                "type": "text",
                "text": (
                    text
                    + f"\n\nThe attached image{'s are' if len(local_images) > 1 else ' is'} "
                    f"the internally processed {creative_type} creative visual"
                    f"{visual_kind}. Inspect only the "
                    "attached visuals for media_analysis and do not claim details that are "
                    "not visible. For video keyframes, compare the frames in chronological "
                    "order for hook, product/context match, pacing, and likely drop-off risks. "
                    "For carousel cards, some cards may be unavailable; discuss only the "
                    "attached cards and do not infer the contents of missing cards."
                ),
            },
            *[
                {"type": "image_url", "image_url": {"url": data_url}}
                for data_url in local_images
            ],
        ]
    image_url = _ad_performance_image_url(context)
    if not image_url:
        return text
    return [
        {
            "type": "text",
            "text": (
                text
                + "\n\nThe attached image is the actual ad creative from creative.image_url. "
                "Inspect the image directly for media_analysis. Do not claim visual details "
                "that are not visible in the image."
            ),
        },
        {"type": "image_url", "image_url": {"url": image_url}},
    ]


def _legacy_ad_performance_user_content(
    context: dict[str, Any],
    supports_video_input: bool = False,
    video_fps: float = 1.0,
) -> str | list[dict[str, Any]]:
    content = _ad_performance_user_content(
        context,
        supports_video_input=supports_video_input,
        video_fps=video_fps,
    )
    if isinstance(content, str):
        return content
    legacy = deepcopy(content)
    if legacy and isinstance(legacy[0], dict) and isinstance(legacy[0].get("text"), str):
        legacy[0]["text"] = legacy[0]["text"].replace("media_analysis", "visual_analysis")
    return legacy


def _ad_performance_public_context(context: dict[str, Any]) -> dict[str, Any]:
    clean = deepcopy(context)
    media_summary = clean.get("media_summary")
    if isinstance(media_summary, dict):
        media_summary.pop("local_artifacts", None)
    return clean


def _ad_performance_local_visual_data_urls(context: dict[str, Any]) -> list[str]:
    media_summary = context.get("media_summary")
    if not isinstance(media_summary, dict):
        return []
    local_artifacts = media_summary.get("local_artifacts")
    if not isinstance(local_artifacts, dict):
        return []
    creative = context.get("creative") if isinstance(context.get("creative"), dict) else {}
    creative_type = str(creative.get("creative_type") or "").lower()
    candidates: list[Any]
    if "video" in creative_type:
        paths = local_artifacts.get("keyframe_paths")
        candidates = paths if isinstance(paths, list) else []
    elif creative_type == "carousel":
        paths = local_artifacts.get("carousel_thumbnail_paths")
        candidates = paths if isinstance(paths, list) else []
    else:
        candidates = [local_artifacts.get("thumbnail_path")]

    data_urls: list[str] = []
    for candidate in candidates[:5]:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        try:
            path = Path(candidate)
            payload = path.read_bytes()
        except OSError:
            continue
        if not payload:
            continue
        mime_type = {
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }.get(path.suffix.lower(), "image/jpeg")
        encoded = base64.b64encode(payload).decode("ascii")
        data_urls.append(f"data:{mime_type};base64,{encoded}")
    return data_urls


def _ad_performance_video_url(context: dict[str, Any]) -> str | None:
    creative = context.get("creative") if isinstance(context.get("creative"), dict) else {}
    creative_type = _coerce_optional_text(
        creative.get("creative_type") or creative.get("asset_type")
    )
    has_video_type = bool(creative_type and "video" in creative_type.lower())
    video_url = _first_text(
        creative.get("video_url"),
        creative.get("videoUrl"),
        creative.get("asset_video_url"),
        creative.get("assetVideoUrl"),
        creative.get("source_video_url"),
    )
    if not video_url:
        return None
    return video_url if has_video_type or not creative_type else None


def _ad_performance_image_url(context: dict[str, Any]) -> str | None:
    creative = context.get("creative") if isinstance(context.get("creative"), dict) else {}
    creative_type = _coerce_optional_text(
        creative.get("creative_type") or creative.get("asset_type")
    )
    if creative_type and creative_type.lower() not in {"image", "carousel"}:
        return None
    image_urls = creative.get("image_urls")
    if creative_type and creative_type.lower() == "carousel" and isinstance(image_urls, list):
        return _first_text(*image_urls)
    return _first_text(
        creative.get("image_url"),
        creative.get("imageUrl"),
        creative.get("asset_image_url"),
        creative.get("assetImageUrl"),
        creative.get("thumbnail_url"),
        creative.get("thumbnailUrl"),
        creative.get("cover_url"),
        creative.get("coverUrl"),
        creative.get("preview_url"),
        creative.get("previewUrl"),
    )


def _ad_performance_visual_analysis_from_data(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    normalized = {
        "summary": _truncate(_coerce_optional_text(value.get("summary")), 700),
        "observed_elements": _limited_text_list(value.get("observed_elements"), 6, 240),
        "strengths": _limited_text_list(value.get("strengths"), 5, 260),
        "weaknesses": _limited_text_list(value.get("weaknesses"), 5, 260),
        "recommendations": _limited_text_list(value.get("recommendations"), 6, 320),
        "risk_notes": _limited_text_list(value.get("risk_notes"), 4, 260),
        "source_image_url": _truncate(_coerce_optional_text(value.get("source_image_url")), 1000),
        "source_video_url": _truncate(_coerce_optional_text(value.get("source_video_url")), 1000),
        "confidence_note": _truncate(_coerce_optional_text(value.get("confidence_note")), 360),
    }
    if not any(
        normalized[key]
        for key in (
            "summary",
            "observed_elements",
            "strengths",
            "weaknesses",
            "recommendations",
            "risk_notes",
        )
    ):
        return None
    return normalized


def _ad_performance_optimization_work_order_from_data(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    try:
        work_order = AdPerformanceOptimizationWorkOrder.model_validate(value).model_dump(
            mode="json"
        )
    except ValidationError:
        return None
    if not (
        work_order.get("operator_summary")
        or work_order.get("campaign")
        or work_order.get("adset")
        or work_order.get("creative")
    ):
        return None
    return work_order


def _limited_text_list(value: Any, limit: int, max_chars: int) -> list[str]:
    return [
        item
        for item in (
            _truncate(_coerce_optional_text(raw), max_chars)
            for raw in _coerce_text_list(value)
        )
        if item
    ][:limit]


def _strip_json_markdown(content: str) -> str:
    stripped = content.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```"):
        if lines[-1].strip().startswith("```"):
            lines = lines[1:-1]
        else:
            lines = lines[1:]
    return "\n".join(lines).strip()


def _video_storyboard_text_system_prompt(revision: bool) -> str:
    task = (
        "Revise the existing editable video storyboard script from operator feedback. "
        "Return the complete revised script, not a diff. Preserve useful timing, "
        "product intent, and approved copy unless feedback asks to change them. "
        "Apply revision_feedback as mandatory. "
        if revision
        else "Write one complete editable plain-text video storyboard script for a short ad. "
    )
    return with_meta_ad_compliance(
        task
        + "Do not return JSON, markdown tables, code fences, or analysis. Stream only the "
        "script text that an operator can edit directly in a textarea. Selected images "
        "are optional for script generation. Use concise scene blocks with time ranges, "
        "visual direction, subtitle, motion, voiceover, and an "
        "exact `Source image id notes:` line. Asset reference rule: If assets is non-empty, "
        "every scene block must include `Source image id notes:` with one or more exact "
        "ids from `selected_asset_ids`. Never write `No source image provided` when assets "
        "is non-empty. If assets is empty, write `Source image id notes: No reference image "
        "required.` Use selected images only when assets are provided and do not invent "
        "unavailable image ids. When assets is empty, generate the script from campaign, "
        "copy, work order, landing context, creative_strategy, and operator instructions. "
        "Use duration_seconds as the source of truth. Compress or expand the "
        "creative_strategy.video_guidance beats to fit that duration; do not assume a fixed "
        "30-second or 12-second structure. Keep subtitles short, readable, and suitable for "
        "mobile feed placements. Do not script unlicensed IP, fake UI, misleading controls, "
        "platform logos/UI, QR codes, watermarks, or unsupported claims. subtitle and "
        "voiceover are user-facing and must use the target audience language. Operator-facing "
        "labels, visual direction, motion notes, and review notes may use Simplified Chinese, "
        "but any visible text requested in the video must use the target audience language. "
        + creative_safety_prompt_block()
        + "\n\n"
        + _keyframe_brand_aaa_video_storyboard_rules()
        + "\n\n"
        + _creative_strategy_system_instruction()
        + "\n\n"
        + language_requirements_prompt()
    )


def _creative_strategy_system_instruction() -> str:
    return (
        "If draft_metadata.creative_strategy, campaign.metadata.creative_strategy, or "
        "context.creative_strategy is provided, treat creative_strategy as mandatory "
        "ad-direction context. If creative_strategy.schema_version is "
        "creative_strategy.v2, treat it as the mandatory internal creative brief. For "
        "topic generation, create one topic per topic_angle_plan slot when possible; "
        "do not repeat angle_type across the three topics. Return angle_type when the "
        "schema allows it. For copy, image, storyboard, and video, follow "
        "market_context, audience_lens, copy_guidance, image_guidance, "
        "video_guidance, market_game_style_pack, brand_profile, brand_policy_pack, "
        "style_pack_id, style_pack, country_overlay, boss_guidance, "
        "reference_signal_pack, and compliance_guardrails. Treat brand_profile and "
        "brand_policy_pack as stronger than style or reference guidance: use only the "
        "current work-order visible brand, do not invent brand names, and keep brand "
        "visibility in the allowed text windows. Treat style_pack_id and style_pack as "
        "the selected creative direction, country_overlay as local preference and "
        "cultural safety guidance, boss_guidance as the mandatory boss role definition, "
        "and reference_signal_pack as inspiration for the current generation only; it "
        "must not override brand, compliance, or the selected style pack. If "
        "text_brand_timing_policy is present, keep visible text, brand lockups, "
        "logo, VIP, and CTA concentrated in 0-3s and 9-12s; the 3-9s middle "
        "segment should have no captions, no large text, no numbers, and no UI "
        "panels except an optional tiny brand mark. If middle_vfx_policy is "
        "present, the 3-9s middle segment must select 2-3 VFX library items as "
        "the main action; do not replace the VFX library with generic cinematic "
        "wording. If creative_package is gambling_vfx_spectacle_package, "
        "Boss or mysterious energy source is a VFX driver, not a combat, leveling, "
        "equipment, or gameplay progression character. For that package, follow "
        "boss_matrix, scene_pool, reveal_mechanism_pool, reveal_diversity_rule, "
        "vfx_library, cta_pool, and gambling_safety_rules: show boss or mysterious "
        "energy-source arrival, VFX library burst, world distortion, mystery reveal "
        "climax, then cleaned brand plus VIP and CTA. Do not default every gambling "
        "creative to a physical door, gate, portal, or vault; at most one variant may "
        "use a physical door/gate/portal/vault composition, while the other variants "
        "should use sky rupture, storm eye, energy throne, crystal core, golden light "
        "column, ancient seal awakening, or abstract power vortex. If "
        "market_game_style_pack is present, use it as mandatory internal game creative "
        "direction for topic angles, copy hooks, image briefs, storyboard scenes, and "
        "video prompts: adapt the visual world, abstract AAA-style genre archetypes, "
        "game interest hypothesis, and gameplay_process into an actual playable sequence "
        "with challenge, player action, retry or choice, progression feedback, unlock, "
        "and CTA. Do not name real games, copied characters, logos, UI, real religious "
        "figures, deity names, prayers, worship, sacrifices, scripture, sacred text, "
        "or religious claims. Use audience traits only as "
        "internal strategy; do not directly assert sensitive personal attributes in "
        "user-facing text. The opening frame must show the project or product name "
        "when one is available; remove digit characters from visible brand text and "
        "do not show numeric suffixes. Use the cleaned visible brand text only for "
        "on-screen branding, while keeping internal campaign metadata unchanged. "
        "Honor legacy template fields too: template_id, "
        "duration_seconds, first_frame, last_frame, motion_direction, "
        "compliance_guardrails, negative_style_cues, video_recipe, "
        "landing_visual_reference, country_style_pack, visual_concepts, "
        "text_layout_rules, and first_three_seconds. If country_style_pack is present, "
        "treat it as mandatory country-specific art direction without adding unsafe "
        "symbols, regulated props, or real-world sensitive claims. If visual_concepts "
        "is present, use those concepts as distinct variants; for "
        "video_keyframe_variants, map keyframe group 1/2/3 to visual_concepts 1/2/3, "
        "using each group's first-frame and last-frame descriptions consistently. If "
        "text_layout_rules is present, keep visible text inside the safe area, auto-fit "
        "text, respect max line counts, and allow no overflow outside the image or "
        "video frame. If first_three_seconds is present, make the opening hook follow "
        "that 0-3s sequence. If landing visual reference is "
        "present, treat it as mandatory art direction: follow its palette, surface_style, "
        "gameplay_moment_archetypes, composition_cues, and video_recipe while avoiding "
        "negative_style_cues. For a 12-second first/last-frame workflow, make the "
        "first-frame hook and last-frame resolution explicit. For mini_game_pool, lead "
        "with a gameplay-led mini-game challenge and finish on a low-text metallic GAJA "
        "game hub CTA beat. For legacy gaja_brand, ignore the stored legacy visual "
        "template as mandatory art direction; use the current brief, selected assets, "
        "or creative_strategy.v2 instead, while keeping visible brand text cleaned of "
        "digit suffixes with no visible brand-number text. Finish on a Start or "
        "Play Now CTA. Keep all claims about "
        "navigation, variety, simple start, and app experience. Avoid "
        "outcome promises, value-return implications, fake platform UI, or fake "
        "browser/app screenshots."
    )


def _topic_stream_system_prompt() -> str:
    return with_meta_ad_compliance(
        "You are an advertising strategist. Generate selectable Facebook advertising "
        "topics as NDJSON only. Do not output markdown, code fences, arrays, or a root "
        "JSON object. Emit exactly one complete JSON object per line. Each topic line "
        "must use this shape: "
        "{\"type\":\"topic\",\"index\":1,\"topic\":{\"title\":\"...\",\"angle\":\"...\","
        "\"angle_type\":\"...\",\"audience\":\"...\",\"selling_points\":[\"...\"],"
        "\"risk_notes\":\"...\",\"rationale\":\"...\",\"score\":0.85}}. "
        "Emit each topic as soon as it is complete. After the requested number of topics, "
        "emit one final line: {\"type\":\"done\"}. topic.title is user-facing and must use "
        "the target audience language. angle, audience, selling_points, risk_notes, and "
        "rationale are operator-facing planning fields and may use Simplified Chinese. "
        "If creative_strategy.topic_angle_plan is present, set topic.angle_type to one of "
        "the plan's angle_type values. "
        "If signals.previous_topics is present, avoid repeating those existing topics. "
        "If signals.topic_revision_feedback is present, treat it as mandatory operator "
        "feedback: adjust the new topics to satisfy it, avoid repeating previous_topics, "
        "and explain the adjustment in rationale.\n\n"
        + _creative_strategy_system_instruction()
        + "\n\n"
        + language_requirements_prompt()
    )


class _TopicNDJSONStreamParser:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.buffer = ""
        self.full_text = ""
        self.yielded = 0

    def feed(self, chunk: str) -> list[TopicCandidate]:
        if not chunk:
            return []
        self.full_text += chunk
        self.buffer += chunk
        candidates: list[TopicCandidate] = []
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            candidates.extend(self._parse_line(line))
        return candidates

    def finish(self) -> list[TopicCandidate]:
        candidates = self._parse_line(self.buffer)
        self.buffer = ""
        if self.yielded == 0:
            candidates.extend(self._parse_full_text_fallback())
        return candidates

    def _parse_line(self, line: str) -> list[TopicCandidate]:
        stripped = line.strip()
        if not stripped or stripped.startswith("```"):
            return []
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, dict):
            return []
        if data.get("type") == "done":
            return []
        topic_data = data.get("topic") if isinstance(data.get("topic"), dict) else data
        if not isinstance(topic_data, dict):
            return []
        if self.yielded >= self.limit:
            return []
        self.yielded += 1
        return [_topic_candidate_from_data(topic_data)]

    def _parse_full_text_fallback(self) -> list[TopicCandidate]:
        content = _strip_json_markdown(self.full_text)
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return []
        if isinstance(data, dict):
            raw_topics = data.get("topics")
        elif isinstance(data, list):
            raw_topics = data
        else:
            raw_topics = None
        if not isinstance(raw_topics, list):
            return []
        candidates: list[TopicCandidate] = []
        for item in raw_topics[: self.limit]:
            if isinstance(item, dict):
                candidates.append(_topic_candidate_from_data(item))
        self.yielded = len(candidates)
        return candidates


def _compact_topic_signals(signals: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in ("integration", "topic_generation_mode", "topic_revision_feedback"):
        value = _coerce_optional_text(signals.get(key))
        if value:
            compact[key] = _truncate(value, 1000 if key == "topic_revision_feedback" else 120)

    previous_topics = signals.get("previous_topics")
    if isinstance(previous_topics, list):
        compact_topics = [
            topic
            for item in previous_topics[:6]
            if isinstance(item, dict)
            if (topic := _compact_previous_topic(item))
        ]
        if compact_topics:
            compact["previous_topics"] = compact_topics

    work_order = _compact_topic_work_order(signals.get("work_order"))
    if work_order:
        compact["work_order"] = work_order

    landing_page = _compact_topic_landing_page(signals.get("landing_page"), work_order)
    if landing_page:
        compact["landing_page"] = landing_page

    selling_points = _dedupe_limited_text(
        [
            *_coerce_text_list(signals.get("selling_points")),
            *_coerce_text_list(landing_page.get("headings") if landing_page else None),
            landing_page.get("title") if landing_page else None,
            landing_page.get("description") if landing_page else None,
        ],
        limit=5,
        max_chars=180,
    )
    if selling_points:
        compact["selling_points"] = selling_points

    creative_strategy = _compact_creative_strategy(signals.get("creative_strategy"))
    if creative_strategy:
        compact["creative_strategy"] = creative_strategy

    return compact


def _compact_topic_work_order(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    parsed_fields = (
        value.get("parsed_fields") if isinstance(value.get("parsed_fields"), dict) else {}
    )
    landing_url = _first_text(value.get("landing_url"), parsed_fields.get("landing_url"))
    context = {
        "country": _first_text(value.get("country"), parsed_fields.get("country")),
        "media": _first_text(value.get("media"), parsed_fields.get("media")),
        "event_name": _first_text(
            value.get("event_name"),
            parsed_fields.get("event_name"),
            parsed_fields.get("objective"),
        ),
        "age_min": _first_text(value.get("age_min"), parsed_fields.get("age_min")),
        "age_max": _first_text(value.get("age_max"), parsed_fields.get("age_max")),
        "gender": _first_text(value.get("gender"), parsed_fields.get("gender")),
        "audience": _truncate(
            _first_text(
                value.get("audience"),
                value.get("audience_description"),
                parsed_fields.get("audience_description_raw"),
                parsed_fields.get("audience_description"),
            ),
            500,
        ),
        "landing_url": landing_url,
        "landing_domain": _domain_from_url(landing_url),
        "report_timezone": _first_text(value.get("report_timezone")),
    }
    return {key: item for key, item in context.items() if item not in (None, "", [])}


def _compact_topic_landing_page(value: Any, work_order: dict[str, Any]) -> dict[str, Any]:
    context = value if isinstance(value, dict) else {}
    extracted_data = (
        context.get("extracted_data") if isinstance(context.get("extracted_data"), dict) else {}
    )
    url = _first_text(context.get("url"), work_order.get("landing_url"))
    headings = _dedupe_limited_text(
        _coerce_text_list(context.get("headings"))
        + _coerce_text_list(extracted_data.get("headings")),
        limit=5,
        max_chars=160,
    )
    landing_page = {
        "url": url,
        "domain": _first_text(context.get("domain"), _domain_from_url(url)),
        "status": _first_text(context.get("status")),
        "http_status": context.get("http_status"),
        "title": _truncate(_coerce_optional_text(context.get("title")), 160),
        "description": _truncate(_coerce_optional_text(context.get("description")), 320),
        "text_excerpt": _truncate(_coerce_optional_text(context.get("text_excerpt")), 600),
        "headings": headings,
    }
    return {key: item for key, item in landing_page.items() if item not in (None, "", [])}


def _compact_previous_topic(value: dict[str, Any]) -> dict[str, str]:
    topic = {
        "title": _truncate(_coerce_optional_text(value.get("title")), 160),
        "angle": _truncate(_coerce_optional_text(value.get("angle")), 240),
        "status": _truncate(_coerce_optional_text(value.get("status")), 80),
        "risk_notes": _truncate(_coerce_optional_text(value.get("risk_notes")), 240),
    }
    return {key: item for key, item in topic.items() if item}


def _dedupe_limited_text(values: list[Any], limit: int, max_chars: int) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _truncate(_coerce_optional_text(value), max_chars)
        if not text:
            continue
        normalized = text.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        items.append(text)
        if len(items) >= limit:
            break
    return items


def _first_text(*values: Any) -> str | None:
    for value in values:
        text = _coerce_optional_text(value)
        if text:
            return text
    return None


def _truncate(value: str | None, max_chars: int) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3].rstrip() + "..."


def _domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if not parsed.netloc:
        return None
    return parsed.netloc.removeprefix("www.")


def _topic_candidate_from_data(data: dict[str, Any]) -> TopicCandidate:
    normalized = {
        "title": _coerce_text(data.get("title")),
        "angle": _coerce_text(data.get("angle")),
        "angle_type": _coerce_optional_text(data.get("angle_type")),
        "audience": _coerce_optional_text(data.get("audience")),
        "selling_points": _coerce_text_list(data.get("selling_points")),
        "risk_notes": _coerce_optional_text(data.get("risk_notes")),
        "rationale": _coerce_optional_text(data.get("rationale")),
        "score": _coerce_score(data.get("score")),
    }
    return TopicCandidate.model_validate(normalized)


def _copy_candidate_from_data(data: dict[str, Any]) -> CopyDraftCandidate:
    normalized = {
        "body": _coerce_text(data.get("body")),
        "primary_text": _coerce_optional_text(data.get("primary_text")),
        "headline": _coerce_optional_text(data.get("headline")),
        "description": _coerce_optional_text(data.get("description")),
        "cta": _coerce_optional_text(data.get("cta")),
    }
    return CopyDraftCandidate.model_validate(normalized)


def _image_brief_from_data(data: dict[str, Any]) -> ImageBrief:
    normalized = {
        "image_index": data.get("image_index"),
        "title": sanitize_creative_safety_text(_coerce_text(data.get("title"))),
        "short_text": sanitize_creative_safety_text(_coerce_text(data.get("short_text"))),
        "visual_direction": sanitize_creative_safety_text(
            _coerce_text(data.get("visual_direction"))
        ),
        "size": _coerce_text(data.get("size") or "1:1"),
    }
    return ImageBrief.model_validate(normalized)


def _video_storyboard_from_data(
    data: dict[str, Any],
    duration_seconds: int,
    aspect_ratio: str,
    allowed_asset_ids: set[str],
) -> VideoStoryboardCandidate:
    raw_scenes = data.get("scenes") or data.get("storyboard") or []
    if not isinstance(raw_scenes, list):
        raw_scenes = []

    scenes = [
        _video_scene_from_data(item, index, allowed_asset_ids)
        for index, item in enumerate(raw_scenes, start=1)
        if isinstance(item, dict)
    ]

    normalized = {
        "duration_seconds": _coerce_int(data.get("duration_seconds"), duration_seconds),
        "aspect_ratio": _coerce_text(data.get("aspect_ratio") or aspect_ratio),
        "scenes": scenes,
        "rationale": _coerce_optional_text(data.get("rationale")),
    }
    return VideoStoryboardCandidate.model_validate(normalized)


def _frame_analysis_system_prompt() -> str:
    return (
        "You jointly analyze two target endpoint images and optional chronological reference "
        "video frames. Return JSON only, with root keys first_frame, last_frame, "
        "transition_brief, language_analysis, and optional reference_video_analysis. "
        "Use this exact base shape: {\"first_frame\": {\"visible_subjects\": [\"string\"], "
        "\"visible_text\": [\"string\"], \"environment\": \"string\", "
        "\"composition\": \"string\", \"camera_perspective\": \"string\", "
        "\"visual_style\": \"string\", \"color_and_lighting\": \"string\", "
        "\"opening_state\": \"string\"}, \"last_frame\": {\"visible_subjects\": "
        "[\"string\"], \"visible_text\": [\"string\"], \"environment\": \"string\", "
        "\"composition\": \"string\", \"camera_perspective\": \"string\", "
        "\"visual_style\": \"string\", \"color_and_lighting\": \"string\", "
        "\"ending_state\": \"string\"}, \"transition_brief\": "
        "{\"shared_visual_facts\": [\"string\"], \"continuity_requirements\": "
        "[\"string\"], \"visual_transition\": \"string\", \"narrative_arc\": "
        "\"string\"}, \"language_analysis\": {\"first_frame_visible_languages\": "
        "[\"string\"], \"last_frame_visible_languages\": [\"string\"], "
        "\"recommended_output_language\": \"string\", \"reason\": \"string\"}}. "
        "Every visible_text item must be a plain string, never an object. Record factual "
        "visual observations for each exact image. Do not write a storyboard or create new "
        "visible copy, logos, labels, or end cards. The target first and last frames define "
        "the required base layers at their anchors. Reference content can be an additional "
        "middle layer or final overlay only when the reference analysis explicitly maps it. "
        "Reference frames are the preferred source for the observed behavior graph: dynamically "
        "extract entities, action and causal beats, visible evidence, dependencies, camera, "
        "transitions, effects, and the lifecycle of any character, appearance, prop, product, "
        "brand, text, reward, UI, setting, or other visual element. Do not use any fixed "
        "action, object, product, or reward template. "
        "When reference frames exist, return reference_video_analysis with duration_seconds, "
        "sample_interval_seconds, chronological segments, visual_identity_mappings, "
        "behavior_graph, and adapted_constraints. Each segment must include start_second, "
        "end_second, subject_presence, camera, transition, effects, and confidence. "
        "behavior_graph must contain entities and beats. Each beat must include beat_id, "
        "reference_start_second, reference_end_second, description, visible_evidence, "
        "behavior_type (action, state, or overlay), importance (core, supporting, or decorative), "
        "minimum_readable_duration_seconds, depends_on, must_remain_visible_until_final, and "
        "locked_text. Use numeric seconds only. reference_end_second must be strictly greater "
        "than reference_start_second. Every beat_id must be non-empty and unique. depends_on "
        "may only contain a known beat_id from the same behavior_graph. Use reference seconds "
        "only to describe observed order and duration; they are not target-generation seconds. "
        "Set must_remain_visible_until_final=true only for a non-diegetic visual overlay such as "
        "readable reward text, logo, label, or UI layer that remains visible at the reference end. "
        "Never mark a character, prop, setting, camera move, action, transformation, hit, or "
        "effect as a final overlay merely because it appears near the end. "
        "For an overlay containing readable text, set locked_text to the exact observed string; "
        "otherwise set locked_text to null. "
        "visual_identity_mappings items must include reference_element, element_type, strategy, "
        "target_first_frame_equivalent, target_last_frame_equivalent, and instruction. "
        "element_type must be one of character, appearance, prop, product, brand, text, reward, "
        "setting, or other. strategy must be exactly preserve, replace_with_target, "
        "morph_to_target, endpoint_only, or preserve_through_last_anchor. Use "
        "preserve_through_last_anchor when a reference element remains visible near the "
        "reference ending and should continue into the generated final frame even if no "
        "target-frame equivalent exists. Its instruction must state that the target last frame "
        "is the base layer and the reference element is a readable final overlay; it may be "
        "repositioned or scaled to avoid obscuring a target subject, product, or brand but must "
        "not be deleted. Use replace_with_target or morph_to_target when a target frame visibly "
        "supplies the same semantic role with different content. adapted_constraints must include "
        "subject_presence, camera_pattern, transition_pattern, and effects_pattern, all with "
        "strength preferred. Do not analyze audio, speech, lyrics, voiceover, narration, "
        "subtitles, or transcripts."
    )


def _frame_anchored_director_system_prompt() -> str:
    return (
        "You are the director of a high-end game cinematic and commercial film. Create a private "
        "evidence-backed director plan for one frame-anchored generated video. Return valid JSON "
        "only with narrative_objective, attention_path, tension_curve, climax_beats, "
        "overlay_lifecycle_plan, anchor_adaptation_plan, and anti_flattening_constraints. "
        "Use the exact primitive field shapes: narrative_objective must be a plain string; "
        "attention_path must be a list of plain strings, not objects; tension_curve must be a "
        "list using only setup, trigger, escalation, climax, and resolution; every climax_beats "
        "item must use stage=climax, source_evidence as a list of plain strings, importance as "
        "core, supporting, or decorative, and depends_on as a list of beat ids. Each "
        "overlay_lifecycle_plan item must use reference_element, strategy, timing_instruction, "
        "and final_frame_requirement. anchor_adaptation_plan must be a list of plain strings, "
        "and anti_flattening_constraints must be a list of plain strings. Do not substitute "
        "nested objects for any of those fields. "
        "First distinguish observed evidence from director inference: source_evidence for every "
        "climax beat must describe visible facts from the supplied target frames or the supplied "
        "reference-video analysis; all camera, timing, performance, and effects proposals are "
        "director inference grounded in that evidence. Do not present an unsupported inference as "
        "an observed fact. The supplied first frame is the exact opening visual anchor and the "
        "supplied last frame is the exact final visual anchor, including its visible composition, "
        "text, brand, product, reward, character appearance, and setting. Reference-video analysis "
        "can transfer observed action, action-result causality, entry or staging, camera language, "
        "timing, effects timing, and overlay lifecycle. Do not copy a reference-specific identity, "
        "brand, logo, product, character, text, reward, or setting unless its equivalent is "
        "visibly "
        "provided by a target frame or the existing visual identity mapping explicitly permits it. "
        "Plan the experience as one generated clip: camera changes, reframing, push-ins, reveals, "
        "or viewpoint changes must happen in-shot. Do not require post-production editing, multi-"
        "segment generation, stitching, or external compositing. Build a tension_curve in "
        "ascending "
        "setup, trigger, escalation, climax, resolution order. Include one or more evidence-backed "
        "climax_beats. Each climax beat must have a stable beat_id, a 0-to-1 start_ratio and "
        "end_ratio, source_evidence, attention_objective, camera_instruction, action_requirement, "
        "effect_requirement, importance, and dependency ids when needed. Core beats must separate "
        "cause, action, impact, and visible result rather than flattening them into a generic "
        "continuous move. For each observed overlay or UI element, decide dynamically whether to "
        "inherit it, replace it with the target equivalent, persist it to the final frame, or omit "
        "it; state the final-frame requirement without inventing a fixed overlay rule. Provide "
        "non-empty anti_flattening_constraints that preserve causal readability, the effect peak, "
        "and the ending anchor."
    )


def _frame_anchored_storyboard_system_prompt() -> str:
    return (
        "You create a frame-anchored video storyboard from two supplied endpoint images "
        "and their visual analysis. Return valid JSON only with duration_seconds, "
        "aspect_ratio, scenes, sound_design, and rationale. Each scene must include "
        "scene_index, start_second, end_second, frame_anchor, visual, motion, "
        "transition_goal, subtitle, voiceover, sound_effects, notes, cinematic_beat, "
        "camera_instruction, tension_stage, action_result_requirement, effect_timing, "
        "overlay_instruction, and anti_flattening_requirement. overlay_instruction must be "
        "null or an object with reference_element, strategy, timing_instruction, and "
        "final_frame_requirement; strategy must be exactly inherit, replace_with_target, "
        "persist_to_final, or omit. Do not return overlay_instruction as a plain string. "
        "The first scene "
        "must use frame_anchor first_frame and begin from the actual supplied first-frame "
        "base layer. Every middle scene must use frame_anchor transition. The final scene "
        "must use frame_anchor last_frame and arrive at the actual supplied last-frame base "
        "layer. Do not alter or translate text that is visibly supplied by either target image. "
        "Use the supplied duration_seconds and aspect_ratio exactly. Scene start_second and "
        "end_second values are target-generation seconds and may use decimals when needed "
        "for a readable adapted beat; do not round them merely to copy reference timing. "
        "When reference_video_analysis exists, use its behavior_graph and its target "
        "timeline_adaptation_plan together: reference seconds explain observed behavior only; "
        "the target plan defines the target scene windows. Do not mechanically copy reference "
        "seconds or linearly scale them. Preserve the dependency order and allocate readable "
        "time to core causal beats; compress repeated or decorative material first and extend "
        "readability or the final state when the target duration is longer. "
        "For each core behavior beat, describe the actual available actors, objects, action, "
        "interaction, causal change, and visible result from the analysis. When frame_analysis "
        "contains a director_plan, every core climax beat must be assigned to a scene whose "
        "cinematic_beat is the exact beat_id from that plan, verbatim; use neither a stage name "
        "nor a new freeform label. Do not replace a "
        "specific observed causal action with a generic effect, passive pose, or static result. "
        "Reference characters, appearance, props, products, brands, text, rewards, UI, settings, "
        "camera, transitions, and effects may be preserved when their visual_identity_mappings "
        "permit it. Follow every mapping: preserve keeps the reference element; "
        "replace_with_target keeps its presentation while substituting the exact visible "
        "target-frame equivalent; morph_to_target transforms it before the ending; endpoint_only "
        "reserves it for the applicable endpoint; preserve_through_last_anchor introduces the "
        "reference element at its target window and keeps it readable through the final frame. "
        "For preserve_through_last_anchor, the target last frame is the final base layer and the "
        "reference element is a required final overlay. It may be scaled or repositioned to "
        "avoid obscuring a target subject, product, or brand, but it must not be omitted merely "
        "because the target last frame has no equivalent. State this requirement explicitly in "
        "the final scene visual or notes. Include optional per-scene sound_effects plus overall "
        "music and ambience directions in sound_design. Do not invent visual identities absent "
        "from both supplied target frames and reference analysis. Apply this priority order: "
        "(1) target-frame base-layer truth and continuity, (2) required mapped reference "
        "lifecycle and causal behavior, including final overlays, (3) the advertising objective "
        "where it does not contradict supplied images, and (4) preferred reference camera, "
        "transitions, and effects."
    )


def _frame_pair_user_content(
    first_frame_image_url: str,
    last_frame_image_url: str,
    payload: dict[str, Any],
    reference_frames: list[ReferenceVideoFrame] | None = None,
) -> list[dict[str, Any]]:
    content = [
        {
            "type": "text",
            "text": "TARGET FIRST FRAME: analyze this exact opening frame.\n"
            + json.dumps(payload, ensure_ascii=False),
        },
        {"type": "image_url", "image_url": {"url": first_frame_image_url}},
        {"type": "text", "text": "TARGET LAST FRAME: analyze this exact ending frame."},
        {"type": "image_url", "image_url": {"url": last_frame_image_url}},
    ]
    for frame in reference_frames or []:
        content.extend(
            [
                {
                    "type": "text",
                    "text": (
                        f"REFERENCE FRAME {frame.timestamp_seconds:.2f}s: extract only "
                        "subject presence, appearance or entry, action, interaction, camera, "
                        "transition, and visible effects."
                    ),
                },
                {"type": "image_url", "image_url": {"url": frame.image_url}},
            ]
        )
    return content


def _frame_analysis_from_data(
    data: dict[str, Any],
    *,
    reference_video_duration_seconds: float | None = None,
    reference_video_sample_interval_seconds: float | None = None,
) -> FrameAnalysis:
    try:
        first_frame = data.get("first_frame")
        last_frame = data.get("last_frame")
        if not isinstance(first_frame, dict) or not isinstance(last_frame, dict):
            raise ValueError("frame analysis must include first_frame and last_frame objects")

        transition_source = data.get("transition_brief")
        if not isinstance(transition_source, dict):
            transition_source = {}
        language_source = data.get("language_analysis")
        if not isinstance(language_source, dict):
            language_source = {}

        visible_languages = _frame_language_list(
            language_source.get("visible_languages") or data.get("visible_languages")
        )
        normalized = {
            "first_frame": _frame_visual_facts_from_data(first_frame),
            "last_frame": _frame_visual_facts_from_data(last_frame),
            "transition_brief": {
                "shared_visual_facts": _frame_string_list(
                    transition_source.get("shared_visual_facts")
                    or data.get("shared_visual_facts")
                ),
                "continuity_requirements": _frame_string_list(
                    transition_source.get("continuity_requirements")
                    or data.get("continuity_requirements")
                ),
                "visual_transition": _coerce_text(
                    transition_source.get("visual_transition")
                    or data.get("plausible_visual_transition")
                ),
                "narrative_arc": _coerce_text(
                    transition_source.get("narrative_arc") or data.get("narrative_arc")
                ),
            },
            "language_analysis": {
                "first_frame_visible_languages": _frame_language_list(
                    language_source.get("first_frame_visible_languages")
                )
                or visible_languages,
                "last_frame_visible_languages": _frame_language_list(
                    language_source.get("last_frame_visible_languages")
                )
                or visible_languages,
                "recommended_output_language": _coerce_text(
                    language_source.get("recommended_output_language")
                    or data.get("recommended_output_language")
                ),
                "reason": _coerce_text(
                    language_source.get("reason")
                    or data.get("recommended_output_language_reason")
                ),
            },
        }
        if isinstance(data.get("reference_video_analysis"), dict):
            normalized["reference_video_analysis"] = _reference_video_analysis_from_data(
                data["reference_video_analysis"],
                reference_duration_seconds=reference_video_duration_seconds,
                reference_sample_interval_seconds=reference_video_sample_interval_seconds,
            )
        return FrameAnalysis.model_validate(normalized)
    except (TypeError, ValidationError, ValueError) as exc:
        logger.warning(
            "Frame analysis JSON validation failed: errors=%s response_shape=%s",
            _frame_analysis_validation_errors(exc),
            _frame_analysis_response_shape(data),
        )
        raise ProviderError("LLM returned invalid frame analysis JSON.") from exc


def _frame_analysis_validation_errors(exc: Exception) -> list[dict[str, str]]:
    if not isinstance(exc, ValidationError):
        return [{"field": "root", "type": type(exc).__name__}]
    return [
        {
            "field": ".".join(str(part) for part in error.get("loc", ("root",))),
            "type": str(error.get("type", "validation_error")),
        }
        for error in exc.errors()
    ]


def _frame_analysis_response_shape(value: Any, depth: int = 0) -> Any:
    if depth >= 3:
        return type(value).__name__
    if isinstance(value, dict):
        return {
            str(key): _frame_analysis_response_shape(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _frame_analysis_response_shape(item, depth + 1)
            for item in value[:3]
        ]
    return type(value).__name__


def _frame_visual_facts_from_data(data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(data)
    normalized["visible_text"] = _frame_string_list(data.get("visible_text"))
    return normalized


def _frame_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    values: list[str] = []
    for item in items:
        raw_text = item.get("text") if isinstance(item, dict) else item
        text = _coerce_optional_text(raw_text)
        if text:
            values.append(text)
    return values


def _frame_language_list(value: Any) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    values: list[str] = []
    for item in items:
        raw_language = item.get("language") if isinstance(item, dict) else item
        language = _coerce_optional_text(raw_language)
        if language:
            values.append(language)
    return values


def _reference_video_analysis_from_data(
    data: dict[str, Any],
    *,
    reference_duration_seconds: float | None = None,
    reference_sample_interval_seconds: float | None = None,
) -> dict[str, Any]:
    normalized = dict(data)
    reliable_duration = _positive_finite_float(reference_duration_seconds)
    reported_duration = _positive_finite_float(data.get("duration_seconds"))
    duration_seconds = reliable_duration or reported_duration
    if duration_seconds is not None:
        normalized["duration_seconds"] = duration_seconds

    reliable_sample_interval = _positive_finite_float(reference_sample_interval_seconds)
    reported_sample_interval = _positive_finite_float(data.get("sample_interval_seconds"))
    if reliable_sample_interval is not None:
        normalized["sample_interval_seconds"] = reliable_sample_interval
    elif reported_sample_interval is not None:
        normalized["sample_interval_seconds"] = reported_sample_interval

    segments = data.get("segments")
    if segments is None:
        segments = data.get("chronological_segments")
    if isinstance(segments, list):
        normalized["segments"] = [
            _reference_video_segment_from_data(segment)
            for segment in segments
            if isinstance(segment, dict)
        ]

    mappings = data.get("visual_identity_mappings")
    if isinstance(mappings, list):
        normalized["visual_identity_mappings"] = [
            _reference_visual_identity_mapping_from_data(mapping)
            for mapping in mappings
            if isinstance(mapping, dict)
        ]

    behavior_graph = data.get("behavior_graph")
    if isinstance(behavior_graph, dict):
        normalized["behavior_graph"] = _normalize_reference_behavior_graph(
            behavior_graph,
            duration_seconds=duration_seconds,
            segments=normalized.get("segments"),
        )

    constraints = data.get("adapted_constraints")
    if isinstance(constraints, dict):
        normalized["adapted_constraints"] = {
            "subject_presence": _reference_constraint_from_data(
                constraints.get("subject_presence"),
                strength="preferred",
            ),
            "camera_pattern": _reference_constraint_from_data(
                constraints.get("camera_pattern"),
                strength="preferred",
            ),
            "transition_pattern": _reference_constraint_from_data(
                constraints.get("transition_pattern"),
                strength="preferred",
            ),
            "effects_pattern": _reference_constraint_from_data(
                constraints.get("effects_pattern"),
                strength="preferred",
            ),
        }
    return normalized


def _normalize_reference_behavior_graph(
    behavior_graph: dict[str, Any],
    *,
    duration_seconds: float | None,
    segments: Any,
) -> dict[str, Any]:
    raw_beats = [beat for beat in behavior_graph.get("beats", []) if isinstance(beat, dict)]
    normalized_beats = [_reference_behavior_beat_from_data(beat) for beat in raw_beats]
    if not normalized_beats:
        return {
            "entities": _frame_string_list(behavior_graph.get("entities")),
            "beats": [],
        }

    duration = duration_seconds or _behavior_graph_fallback_duration(normalized_beats)
    anchors = _reference_timeline_anchors(segments, duration)
    fallback_window = min(0.5, max(0.1, duration / max(len(normalized_beats) + 1, 2)))
    prepared: list[dict[str, Any]] = []
    previous_end = 0.0

    for source_index, beat in enumerate(normalized_beats):
        start = _finite_float_or_none(beat.get("reference_start_second"))
        end = _finite_float_or_none(beat.get("reference_end_second"))
        window = _normalize_reference_behavior_window(
            start=start,
            end=end,
            duration=duration,
            anchors=anchors,
            previous_end=previous_end,
            fallback_window=fallback_window,
        )
        beat["reference_start_second"], beat["reference_end_second"] = window
        if beat["must_remain_visible_until_final"]:
            beat["reference_end_second"] = duration
        beat["description"] = (
            _coerce_optional_text(beat.get("description"))
            or f"Observed reference behavior {source_index + 1}."
        )
        readable_duration = _positive_finite_float(
            beat.get("minimum_readable_duration_seconds")
        )
        beat["minimum_readable_duration_seconds"] = readable_duration or 0.5
        prepared.append(
            {
                "source_index": source_index,
                "raw_id": _coerce_optional_text(raw_beats[source_index].get("beat_id")),
                "raw_depends_on": _frame_string_list(raw_beats[source_index].get("depends_on")),
                "beat": beat,
            }
        )
        previous_end = max(previous_end, beat["reference_end_second"])

    prepared.sort(
        key=lambda item: (
            item["beat"]["reference_start_second"],
            item["beat"]["reference_end_second"],
            item["source_index"],
        )
    )
    raw_to_canonical: dict[str, str] = {}
    for index, item in enumerate(prepared, start=1):
        canonical_id = f"beat_{index:03d}"
        item["canonical_id"] = canonical_id
        raw_id = item["raw_id"]
        if raw_id and raw_id not in raw_to_canonical:
            raw_to_canonical[raw_id] = canonical_id

    canonical_beats: list[dict[str, Any]] = []
    for index, item in enumerate(prepared):
        beat = item["beat"]
        canonical_id = item["canonical_id"]
        allowed_prior_ids = {previous["beat_id"] for previous in canonical_beats}
        declared_dependencies = item["raw_depends_on"]
        dependencies: list[str] = []
        for raw_dependency in declared_dependencies:
            canonical_dependency = raw_to_canonical.get(raw_dependency)
            if (
                canonical_dependency
                and canonical_dependency != canonical_id
                and canonical_dependency in allowed_prior_ids
                and canonical_dependency not in dependencies
            ):
                dependencies.append(canonical_dependency)
        if index == 0:
            dependencies = []
        elif declared_dependencies and not dependencies:
            dependencies = [canonical_beats[-1]["beat_id"]]
        beat["beat_id"] = canonical_id
        beat["depends_on"] = dependencies
        canonical_beats.append(beat)

    return {
        "entities": _frame_string_list(behavior_graph.get("entities")),
        "beats": canonical_beats,
    }


def _normalize_reference_behavior_window(
    *,
    start: float | None,
    end: float | None,
    duration: float,
    anchors: list[float],
    previous_end: float,
    fallback_window: float,
) -> tuple[float, float]:
    clamped_start = _clamp_reference_second(start, duration)
    clamped_end = _clamp_reference_second(end, duration)
    if clamped_start is not None and clamped_end is not None and clamped_end > clamped_start:
        return clamped_start, clamped_end

    preferred_start = max(previous_end, clamped_start or 0.0)
    if preferred_start >= duration:
        return max(0.0, duration - fallback_window), duration
    anchored_end = next((anchor for anchor in anchors if anchor > preferred_start), None)
    repaired_end = anchored_end or min(duration, preferred_start + fallback_window)
    if repaired_end <= preferred_start:
        repaired_end = min(duration, preferred_start + fallback_window)
    if repaired_end <= preferred_start:
        return max(0.0, duration - fallback_window), duration
    return preferred_start, repaired_end


def _reference_timeline_anchors(segments: Any, duration: float) -> list[float]:
    anchors = {0.0, duration}
    if isinstance(segments, list):
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            for key in ("start_second", "end_second"):
                second = _clamp_reference_second(_finite_float_or_none(segment.get(key)), duration)
                if second is not None:
                    anchors.add(second)
    return sorted(anchors)


def _behavior_graph_fallback_duration(beats: list[dict[str, Any]]) -> float:
    observed_seconds = [
        second
        for beat in beats
        for second in (
            _finite_float_or_none(beat.get("reference_start_second")),
            _finite_float_or_none(beat.get("reference_end_second")),
        )
        if second is not None and second > 0
    ]
    return max(1.0, min(30.0, max(observed_seconds, default=1.0)))


def _clamp_reference_second(second: float | None, duration: float) -> float | None:
    if second is None:
        return None
    return max(0.0, min(second, duration))


def _finite_float_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _positive_finite_float(value: Any) -> float | None:
    number = _finite_float_or_none(value)
    return number if number is not None and number > 0 else None

def _reference_visual_identity_mapping_from_data(data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(data)
    normalized["reference_element"] = _coerce_text(data.get("reference_element"))
    element_type = _coerce_text(data.get("element_type")).lower()
    normalized["element_type"] = (
        element_type
        if element_type
        in {
            "character",
            "appearance",
            "prop",
            "product",
            "brand",
            "text",
            "reward",
            "setting",
            "other",
        }
        else "other"
    )
    strategy = _coerce_text(data.get("strategy")).lower()
    normalized["strategy"] = (
        strategy
        if strategy
        in {
            "preserve",
            "replace_with_target",
            "morph_to_target",
            "endpoint_only",
            "preserve_through_last_anchor",
        }
        else "preserve"
    )
    normalized["target_first_frame_equivalent"] = _coerce_optional_text(
        data.get("target_first_frame_equivalent")
    )
    normalized["target_last_frame_equivalent"] = _coerce_optional_text(
        data.get("target_last_frame_equivalent")
    )
    normalized["instruction"] = _coerce_text(data.get("instruction"))
    return normalized


def _reference_behavior_beat_from_data(data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(data)
    normalized["beat_id"] = _coerce_text(data.get("beat_id"))
    normalized["reference_start_second"] = _coerce_float(data.get("reference_start_second"))
    normalized["reference_end_second"] = _coerce_float(data.get("reference_end_second"))
    normalized["description"] = _coerce_text(data.get("description"))
    normalized["visible_evidence"] = _frame_string_list(data.get("visible_evidence"))
    behavior_type = _coerce_text(data.get("behavior_type")).lower()
    normalized["behavior_type"] = (
        behavior_type if behavior_type in {"action", "state", "overlay"} else "action"
    )
    importance = _coerce_text(data.get("importance")).lower()
    normalized["importance"] = (
        importance if importance in {"core", "supporting", "decorative"} else "supporting"
    )
    normalized["minimum_readable_duration_seconds"] = _coerce_float(
        data.get("minimum_readable_duration_seconds"),
        default=0.5,
    )
    normalized["depends_on"] = _frame_string_list(data.get("depends_on"))
    normalized["must_remain_visible_until_final"] = (
        normalized["behavior_type"] == "overlay"
        and bool(data.get("must_remain_visible_until_final"))
    )
    locked_text = _coerce_optional_text(data.get("locked_text"))
    normalized["locked_text"] = (
        locked_text if normalized["behavior_type"] == "overlay" else None
    )
    return normalized


def _reference_video_segment_from_data(data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(data)
    normalized["subject_presence"] = _reference_pattern_from_data(
        data.get("subject_presence"),
        text_field="state",
    )
    normalized["camera"] = _reference_pattern_from_data(
        data.get("camera"),
        text_field="movement",
    )
    normalized["transition"] = _reference_pattern_from_data(
        data.get("transition"),
        text_field="description",
    )
    normalized["effects"] = _frame_string_list(data.get("effects"))
    normalized["confidence"] = _coerce_text(data.get("confidence"))
    return normalized


def _reference_pattern_from_data(value: Any, *, text_field: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {text_field: _coerce_text(value)}


def _reference_constraint_from_data(value: Any, *, strength: str) -> dict[str, str]:
    instruction = value.get("instruction") if isinstance(value, dict) else value
    return {
        "strength": strength,
        "instruction": _coerce_text(instruction),
    }


_DIRECTOR_STAGE_ORDER = {
    "setup": 0,
    "trigger": 1,
    "escalation": 2,
    "climax": 3,
    "resolution": 4,
}


def _director_items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else ([] if value is None else [value])


def _director_text(value: Any) -> str:
    """Keep semantic content when the model uses an object for a text field."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "; ".join(text for item in value if (text := _director_text(item)))
    if isinstance(value, dict):
        return "; ".join(
            f"{key}: {text}"
            for key, item in value.items()
            if (text := _director_text(item))
        )
    return _coerce_text(value).strip()


def _director_first_text(data: dict[str, Any], *keys: str) -> str:
    return next((text for key in keys if (text := _director_text(data.get(key)))), "")


def _director_text_list(value: Any) -> list[str]:
    texts: list[str] = []
    for item in _director_items(value):
        if isinstance(item, dict):
            texts.extend(
                f"{key}: {text}"
                for key, raw in item.items()
                if (text := _director_text(raw))
            )
        elif text := _director_text(item):
            texts.append(text)
    return texts


def _director_ratio(value: Any, fallback: float) -> float:
    ratio = _finite_float_or_none(value)
    if ratio is None:
        return fallback
    if 1 < ratio <= 100:
        ratio /= 100
    return max(0.0, min(ratio, 1.0))


def _director_stage(value: Any) -> str | None:
    text = _director_text(value).lower().replace("_", " ").replace("-", " ")
    for stage in _DIRECTOR_STAGE_ORDER:
        if stage in text:
            return stage
    aliases = (
        ("climax", ("peak", "payoff", "impact")),
        ("resolution", ("resolve", "ending", "conclusion")),
        ("escalation", ("escalat", "rising", "build", "intens", "develop")),
        ("trigger", ("catalyst", "inciting", "activate", "hook")),
        ("setup", ("opening", "establish", "intro", "begin")),
    )
    return next((stage for stage, words in aliases if any(word in text for word in words)), None)


def _normalize_director_attention_path(value: Any) -> list[str]:
    path: list[str] = []
    for item in _director_items(value):
        if isinstance(item, dict):
            focus = _director_first_text(item, "focus", "attention", "subject", "objective")
            method = _director_first_text(
                item, "method", "approach", "instruction", "camera"
            )
            ratio = _finite_float_or_none(item.get("time_ratio"))
            prefix = f"At {round(ratio * 100)}%: " if ratio is not None else ""
            detail = ". ".join(part for part in (focus, method) if part)
            text = prefix + (detail or _director_text(item))
        else:
            text = _director_text(item)
        if text and text not in path:
            path.append(text)
    return path


def _normalize_director_tension_curve(value: Any) -> list[str]:
    stages = {
        stage
        for item in _director_items(value)
        if (stage := _director_stage(item.get("phase") if isinstance(item, dict) else item))
    }
    if not stages:
        return ["setup", "climax", "resolution"]
    stages.add("climax")
    return sorted(stages, key=_DIRECTOR_STAGE_ORDER.__getitem__)


def _normalize_director_importance(value: Any) -> str:
    text = _director_text(value).lower()
    if any(word in text for word in ("decorative", "optional", "minor", "low")):
        return "decorative"
    if any(word in text for word in ("core", "primary", "main", "key", "high")):
        return "core"
    return "supporting"


def _normalize_director_climax_beats(value: Any) -> list[dict[str, Any]]:
    raw_beats = [item for item in _director_items(value) if isinstance(item, dict)]
    normalized: list[dict[str, Any]] = []
    id_map: dict[str, str] = {}
    raw_dependencies: list[list[str]] = []
    for index, raw in enumerate(raw_beats, start=1):
        raw_id = _director_first_text(raw, "beat_id", "id", "name", "title")
        raw_id = raw_id or f"climax_{index}"
        beat_id = raw_id
        suffix = 2
        while beat_id in {item["beat_id"] for item in normalized}:
            beat_id = f"{raw_id}_{suffix}"
            suffix += 1
        id_map.setdefault(raw_id, beat_id)
        start = _director_ratio(
            raw.get("start_ratio", raw.get("start")),
            min(0.85, 0.45 + 0.12 * (index - 1)),
        )
        end = _director_ratio(
            raw.get("end_ratio", raw.get("end")),
            min(1.0, start + 0.18),
        )
        if end <= start:
            start = min(start, 0.95)
            end = min(1.0, max(start + 0.05, end))
        evidence = _director_text_list(
            raw.get("source_evidence", raw.get("visible_evidence", raw.get("evidence")))
        )
        if not evidence:
            evidence = _director_text_list(
                {
                    key: raw[key]
                    for key in ("observed_action", "observed_result", "description")
                    if raw.get(key) is not None
                }
            )
        normalized.append(
            {
                "beat_id": beat_id,
                "stage": "climax",
                "source_evidence": evidence,
                "start_ratio": start,
                "end_ratio": end,
                "attention_objective": _director_first_text(
                    raw, "attention_objective", "objective", "attention"
                ),
                "camera_instruction": _director_first_text(
                    raw, "camera_instruction", "camera", "viewpoint"
                ),
                "action_requirement": _director_first_text(
                    raw, "action_requirement", "action", "performance"
                ),
                "effect_requirement": _director_first_text(
                    raw, "effect_requirement", "effects", "vfx"
                ),
                "importance": _normalize_director_importance(raw.get("importance")),
            }
        )
        raw_dependencies.append(
            _director_text_list(raw.get("depends_on", raw.get("dependencies")))
        )
    for beat, dependencies in zip(normalized, raw_dependencies, strict=True):
        beat["depends_on"] = [
            mapped
            for dependency in dependencies
            if (mapped := id_map.get(dependency)) and mapped != beat["beat_id"]
        ]
    return normalized


def _normalize_director_overlay_strategy(value: Any) -> str:
    text = _director_text(value).lower()
    if any(word in text for word in ("persist", "remain", "through", "final", "ending")):
        return "persist_to_final"
    if any(word in text for word in ("replace", "target", "swap", "substitut")):
        return "replace_with_target"
    if any(word in text for word in ("omit", "remove", "drop", "exclude")):
        return "omit"
    return "inherit"


def _normalize_director_overlay_lifecycle_plan(value: Any) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for raw in _director_items(value):
        if not isinstance(raw, dict):
            continue
        element = _director_first_text(
            raw, "reference_element", "element", "overlay", "name", "label"
        )
        if not element:
            continue
        lifecycle = _director_first_text(
            raw, "lifecycle", "timing_instruction", "observed_in"
        )
        constraints = _director_text(raw.get("constraints"))
        final_requirement = _director_first_text(
            raw,
            "final_frame_requirement",
            "observed_final_requirement",
            "final_requirement",
        )
        normalized.append(
            {
                "reference_element": element,
                "strategy": _normalize_director_overlay_strategy(
                    _director_first_text(raw, "strategy", "decision", "lifecycle")
                ),
                "timing_instruction": (
                    ". ".join(part for part in (lifecycle, constraints) if part)
                    or "Follow the observed overlay lifecycle."
                ),
                "final_frame_requirement": (
                    final_requirement
                    or "Respect the supplied last-frame base layer when resolving this overlay."
                ),
            }
        )
    return normalized


def _flatten_director_anchor_plan(value: Any, prefix: str = "") -> list[str]:
    if isinstance(value, list):
        return [line for item in value for line in _flatten_director_anchor_plan(item, prefix)]
    if not isinstance(value, dict):
        text = _director_text(value)
        return [f"{prefix}: {text}" if prefix and text else text] if text else []
    direct = _director_first_text(value, "instruction", "plan", "strategy", "requirement")
    if direct:
        return [f"{prefix}: {direct}" if prefix else direct]
    return [
        line
        for key, item in value.items()
        for line in _flatten_director_anchor_plan(
            item,
            f"{prefix}.{key}" if prefix else str(key),
        )
    ]


def _normalize_director_constraints(value: Any) -> list[str]:
    normalized: list[str] = []
    for item in _director_items(value):
        if isinstance(item, dict):
            constraint = _director_first_text(item, "constraint", "instruction", "requirement")
            application = _director_first_text(item, "application", "how", "implementation")
            text = (
                f"{constraint} Application: {application}"
                if constraint and application
                else constraint or application
            )
        else:
            text = _director_text(item)
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _frame_anchored_director_plan_from_data(
    data: dict[str, Any],
) -> FrameAnchoredDirectorPlan:
    normalized = dict(data)
    normalized["narrative_objective"] = _director_text(data.get("narrative_objective"))
    normalized["attention_path"] = _normalize_director_attention_path(
        data.get("attention_path")
    )
    normalized["tension_curve"] = _normalize_director_tension_curve(
        data.get("tension_curve")
    )
    normalized["climax_beats"] = _normalize_director_climax_beats(
        data.get("climax_beats")
    )
    normalized["overlay_lifecycle_plan"] = _normalize_director_overlay_lifecycle_plan(
        data.get("overlay_lifecycle_plan")
    )
    normalized["anchor_adaptation_plan"] = _flatten_director_anchor_plan(
        data.get("anchor_adaptation_plan")
    )
    normalized["anti_flattening_constraints"] = _normalize_director_constraints(
        data.get("anti_flattening_constraints")
    )
    try:
        return FrameAnchoredDirectorPlan.model_validate(normalized)
    except ValidationError as exc:
        logger.warning(
            "Frame-anchored director-plan JSON validation failed: errors=%s response_shape=%s",
            _frame_analysis_validation_errors(exc),
            _frame_analysis_response_shape(data),
        )
        raise ProviderError("LLM returned invalid frame-anchored director-plan JSON.") from exc

def _frame_anchored_storyboard_from_data(
    data: dict[str, Any],
    duration_seconds: int,
    aspect_ratio: str,
) -> FrameAnchoredStoryboard:
    normalized = dict(data)
    normalized["duration_seconds"] = duration_seconds
    normalized["aspect_ratio"] = aspect_ratio
    scenes = data.get("scenes")
    if isinstance(scenes, list):
        normalized["scenes"] = [
            {
                **scene,
                "sound_effects": _frame_string_list(scene.get("sound_effects")),
            }
            if isinstance(scene, dict) and "sound_effects" in scene
            else scene
            for scene in scenes
        ]
    try:
        return FrameAnchoredStoryboard.model_validate(normalized)
    except ValidationError as exc:
        logger.warning(
            "Frame-anchored storyboard JSON validation failed: errors=%s response_shape=%s",
            _frame_analysis_validation_errors(exc),
            _frame_analysis_response_shape(data),
        )
        raise ProviderError("LLM returned invalid frame-anchored storyboard JSON.") from exc


def _video_scene_from_data(
    data: dict[str, Any],
    fallback_index: int,
    allowed_asset_ids: set[str],
) -> VideoStoryboardScene:
    source_asset_ids = [
        asset_id
        for asset_id in _coerce_text_list(data.get("source_asset_ids"))
        if asset_id in allowed_asset_ids
    ]
    normalized = {
        "scene_index": _coerce_int(data.get("scene_index"), fallback_index),
        "start_second": _coerce_optional_int(data.get("start_second")),
        "end_second": _coerce_optional_int(data.get("end_second")),
        "visual": sanitize_creative_safety_text(_coerce_text(data.get("visual"))),
        "subtitle": _safe_optional_creative_text(data.get("subtitle")),
        "motion": _safe_optional_creative_text(data.get("motion")),
        "voiceover": _safe_optional_creative_text(data.get("voiceover")),
        "source_asset_ids": source_asset_ids,
        "notes": _safe_optional_creative_text(data.get("notes")),
    }
    return VideoStoryboardScene.model_validate(normalized)


def _safe_optional_creative_text(value: Any) -> str | None:
    text = _coerce_optional_text(value)
    if text is None:
        return None
    return sanitize_creative_safety_text(text)


def _compact_storyboard_for_revision(storyboard: list[dict]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for scene in storyboard[:12]:
        if not isinstance(scene, dict):
            continue
        compact.append(
            {
                "scene_index": scene.get("scene_index"),
                "start_second": scene.get("start_second"),
                "end_second": scene.get("end_second"),
                "visual": _truncate(_coerce_optional_text(scene.get("visual")), 900),
                "subtitle": _truncate(_coerce_optional_text(scene.get("subtitle")), 240),
                "motion": _truncate(_coerce_optional_text(scene.get("motion")), 360),
                "voiceover": _truncate(_coerce_optional_text(scene.get("voiceover")), 500),
                "source_asset_ids": _coerce_text_list(scene.get("source_asset_ids"))[:4],
                "notes": _truncate(_coerce_optional_text(scene.get("notes")), 360),
            }
        )
    return [
        {key: value for key, value in scene.items() if value not in (None, "", [])}
        for scene in compact
    ]


def _draft_context(draft: CopyDraft | None) -> dict[str, Any] | None:
    if not draft:
        return None
    return {
        "id": draft.id,
        "body": draft.body,
        "primary_text": draft.primary_text,
        "headline": draft.headline,
        "description": draft.description,
        "cta": draft.cta,
        "metadata": _compact_metadata_with_strategy(draft.metadata_json),
    }


def _asset_context(asset: CreativeAsset) -> dict[str, Any]:
    return {
        "id": asset.id,
        "url": asset.url,
        "prompt": asset.prompt,
        "alt_text": asset.alt_text,
        "size": asset.size,
        "metadata": _compact_metadata_with_strategy(asset.metadata_json),
    }


def _image_source_asset_context(asset: CreativeAsset | None) -> dict[str, Any] | None:
    if asset is None:
        return None
    metadata = asset.metadata_json if isinstance(asset.metadata_json, dict) else {}
    return {
        "id": asset.id,
        "image_index": metadata.get("image_index"),
        "version": asset.version,
        "prompt": _truncate(asset.prompt, 1200),
        "alt_text": asset.alt_text,
        "size": asset.size,
        "status": asset.status,
    }


def _compact_metadata_with_strategy(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    metadata: dict[str, Any] = {}
    creative_strategy = _compact_creative_strategy(value.get("creative_strategy"))
    if creative_strategy:
        metadata["creative_strategy"] = creative_strategy

    landing_page = _compact_landing_page_metadata(value.get("landing_page"))
    if landing_page:
        metadata["landing_page"] = landing_page

    work_order = _compact_work_order_metadata(value.get("work_order"))
    if work_order:
        metadata["work_order"] = work_order

    for key in (
        "external_request_id",
        "external_order_id",
        "campaign_id",
        "topic_id",
        "draft_id",
        "image_index",
        "provider",
        "source",
        "size",
        "status",
        "version",
        "url",
        "storage_key",
        "landing_url",
        "country",
        "event_name",
        "audience",
        "audience_description",
    ):
        value_item = value.get(key)
        compact_value = _compact_metadata_scalar(value_item)
        if compact_value not in (None, "", []):
            metadata[key] = compact_value
    return metadata


def _compact_landing_page_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact: dict[str, Any] = {}
    for key, limit in (
        ("url", 500),
        ("domain", 120),
        ("title", 180),
        ("description", 300),
        ("text_excerpt", 600),
        ("status", 80),
    ):
        compact_value = _truncate(_coerce_optional_text(value.get(key)), limit)
        if compact_value:
            compact[key] = compact_value
    headings = value.get("headings")
    if isinstance(headings, list):
        compact_headings = [
            item
            for item in (
                _truncate(_coerce_text(heading), 120) for heading in headings[:8]
            )
            if item
        ]
        if compact_headings:
            compact["headings"] = compact_headings
    return compact


def _compact_work_order_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact: dict[str, Any] = {}
    for key in (
        "country",
        "media",
        "landing_url",
        "event_name",
        "report_timezone",
        "audience_description",
        "audience_description_raw",
    ):
        compact_value = _compact_metadata_scalar(value.get(key))
        if compact_value not in (None, "", []):
            compact[key] = compact_value
    parsed_fields = value.get("parsed_fields")
    if isinstance(parsed_fields, dict):
        compact_fields = {
            key: compact_value
            for key in (
                "country",
                "landing_url",
                "event_name",
                "product_name",
                "audience_description_raw",
                "gender",
                "age_min",
                "age_max",
            )
            if (compact_value := _compact_metadata_scalar(parsed_fields.get(key)))
            not in (None, "", [])
        }
        if compact_fields:
            compact["parsed_fields"] = compact_fields
    return compact


def _compact_metadata_scalar(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _truncate(value, 500)
    if isinstance(value, list):
        return [
            item
            for item in (_compact_metadata_scalar(entry) for entry in value[:8])
            if item not in (None, "", [])
        ]
    return None


def _compact_image_storyboard_context(value: dict | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    raw_scenes = value.get("storyboard")
    scenes = raw_scenes if isinstance(raw_scenes, list) else []
    compact_scenes: list[dict[str, Any]] = []
    for index, scene in enumerate(scenes[:8], start=1):
        if not isinstance(scene, dict):
            continue
        compact_scenes.append(
            {
                "scene_index": _coerce_int(scene.get("scene_index"), index),
                "visual": _truncate(_coerce_optional_text(scene.get("visual")), 700),
                "subtitle": _truncate(_coerce_optional_text(scene.get("subtitle")), 180),
                "motion": _truncate(_coerce_optional_text(scene.get("motion")), 240),
                "voiceover": _truncate(_coerce_optional_text(scene.get("voiceover")), 300),
            }
        )

    storyboard_text = _truncate(_coerce_optional_text(value.get("storyboard_text")), 4000)
    keyframe_plan = value.get("keyframe_plan")
    compact_keyframe_plan = keyframe_plan if isinstance(keyframe_plan, dict) else None
    creative_strategy = _compact_creative_strategy(value.get("creative_strategy"))
    if (
        not compact_scenes
        and not storyboard_text
        and not compact_keyframe_plan
        and not creative_strategy
    ):
        return None
    context = {
        "scenes": [
            {key: item for key, item in scene.items() if item not in (None, "", [])}
            for scene in compact_scenes
        ],
        "storyboard_text": storyboard_text,
    }
    if compact_keyframe_plan:
        context["keyframe_plan"] = {
            key: compact_keyframe_plan.get(key)
            for key in (
                "mode",
                "variant_count",
                "frames_per_variant",
                "video_duration_seconds",
                "total_images",
            )
            if compact_keyframe_plan.get(key) not in (None, "", [])
        }
    if creative_strategy:
        context["creative_strategy"] = creative_strategy
    selected_topic = _compact_selected_topic_context(value.get("selected_topic"))
    if selected_topic:
        context["selected_topic"] = selected_topic
    return context


def _compact_selected_topic_context(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    selected_topic: dict[str, Any] = {
        "id": _truncate(_coerce_optional_text(value.get("id")), 80),
        "title": _truncate(_coerce_optional_text(value.get("title")), 240),
        "angle": _truncate(_coerce_optional_text(value.get("angle")), 500),
        "angle_type": _truncate(_coerce_optional_text(value.get("angle_type")), 120),
        "audience": _truncate(_coerce_optional_text(value.get("audience")), 240),
        "risk_notes": _truncate(_coerce_optional_text(value.get("risk_notes")), 300),
    }
    selling_points = value.get("selling_points")
    if isinstance(selling_points, list):
        selected_topic["selling_points"] = [
            item
            for item in (
                _truncate(_coerce_optional_text(point), 160)
                for point in selling_points[:6]
            )
            if item
        ]
    topic_angle = value.get("topic_angle")
    if isinstance(topic_angle, dict):
        selected_topic["topic_angle"] = {
            key: item
            for key, item in {
                "slot": topic_angle.get("slot"),
                "angle_type": _truncate(
                    _coerce_optional_text(topic_angle.get("angle_type")),
                    120,
                ),
                "purpose": _truncate(
                    _coerce_optional_text(topic_angle.get("purpose")),
                    500,
                ),
                "avoid_repeating": _compact_string_list(
                    topic_angle.get("avoid_repeating"),
                    limit=6,
                    item_limit=120,
                ),
            }.items()
            if item not in (None, "", [])
        }
    return {key: item for key, item in selected_topic.items() if item not in (None, "", [])} or None


def _compact_string_list(value: Any, *, limit: int, item_limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value[:limit]:
        text = _truncate(_coerce_optional_text(item), item_limit)
        if text:
            items.append(text)
    return items


def _compact_creative_strategy(value: Any) -> dict[str, Any] | None:
    return compact_creative_strategy(value)


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "；".join(_coerce_text(item) for item in value if item is not None)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _coerce_optional_text(value: Any) -> str | None:
    text = _coerce_text(value).strip()
    return text or None


def _coerce_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_coerce_text(item) for item in value if _coerce_text(item).strip()]
    return [_coerce_text(value)]


def _coerce_score(value: Any) -> float | None:
    if value is None:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score > 1 and score <= 10:
        score = score / 10
    return max(0.0, min(1.0, score))


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _coerce_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
