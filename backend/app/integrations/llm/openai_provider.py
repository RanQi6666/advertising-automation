import json
from collections.abc import AsyncIterator
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
    ImageBrief,
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
        data = await self._json_completion(
            system=_ad_performance_analysis_system_prompt(),
            user=_ad_performance_user_content(
                context,
                supports_video_input=self.supports_video_input,
                video_fps=self.video_input_fps,
            ),
        )
        return _ad_performance_analysis_from_data(data)

    async def stream_ad_performance_analysis(
        self, context: dict
    ) -> AsyncIterator[dict[str, Any]]:
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": _ad_performance_analysis_system_prompt()},
                {
                    "role": "user",
                    "content": _ad_performance_user_content(
                        context,
                        supports_video_input=self.supports_video_input,
                        video_fps=self.video_input_fps,
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
            "analysis": _ad_performance_analysis_from_data(data),
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
                "three groups distinct enough for an operator to choose between. "
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


def _ad_performance_analysis_from_data(data: dict[str, Any]) -> dict[str, Any]:
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


def _creative_payload_json(payload: dict[str, Any]) -> str:
    return json.dumps(sanitize_creative_safety_payload(payload), ensure_ascii=False)


def _ad_performance_analysis_system_prompt() -> str:
    return (
        "You are a senior performance marketing analyst for Meta/Facebook ads. "
        "Return valid JSON only. Analyze the ad independently first; comparisons "
        "with sibling ads are supplemental and must not replace the independent "
        "diagnosis. Do not overjudge creative quality when spend, impressions, or "
        "click volume is too small. Be practical for an operator: explain what is "
        "probably wrong, why, and what exact action to test next. Use Simplified "
        "Chinese. Respect data_completeness: if image/video URLs or keyframes are "
        "missing, say the visual diagnosis is limited instead of pretending you "
        "saw the asset. The root object must contain: summary, root_causes, "
        "recommended_actions, next_tests, creative_feedback, audience_feedback, "
        "landing_page_feedback, budget_delivery_feedback, risk_notes, "
        "optimization_work_order, and confidence_note. optimization_work_order must be "
        "the same diagnosis expressed as an operator work order, not a separate rule "
        "result. Its shape is: schema_version, operator_summary, priority, "
        "overall_action, next_step, modules_to_change, modules_to_keep, "
        "modules_to_watch, campaign, adset, creative, warnings. campaign/adset/creative "
        "are arrays of field advice objects with: field, label, current_value, action, "
        "priority, suggested_value, suggested_direction, generation_prompt, reason, "
        "source, can_apply_to_generation, missing. action must be one of keep, "
        "regenerate, rewrite, check, watch, reduce, increase, pause, create_draft, "
        "missing. priority must be low, medium, or high. source must be ai. For fields "
        "that should feed generation, set can_apply_to_generation true and provide a "
        "practical generation_prompt. If a required field is missing from input, set "
        "action to missing and missing to true. If an image or video is attached, "
        "inspect the actual visual content and include "
        "visual_analysis with summary, observed_elements, strengths, weaknesses, "
        "recommendations, risk_notes, source_image_url/source_video_url, and "
        "confidence_note. If no visual media is attached, set visual_analysis to null. "
        "Every list field must be an array of short strings."
    )


def _ad_performance_user_content(
    context: dict[str, Any],
    supports_video_input: bool = False,
    video_fps: float = 1.0,
) -> str | list[dict[str, Any]]:
    text = json.dumps(context, ensure_ascii=False)
    video_url = _ad_performance_video_url(context) if supports_video_input else None
    if video_url:
        return [
            {
                "type": "text",
                "text": (
                    text
                    + "\n\nThe attached video is the actual ad creative from creative.video_url. "
                    "Inspect the video directly for visual_analysis, especially the first "
                    "seconds, hook, product/context match, pacing, and drop-off risks. Do not "
                    "claim visual details that are not visible in the video."
                ),
            },
            {"type": "video_url", "video_url": {"url": video_url, "fps": video_fps}},
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
                "Inspect the image directly for visual_analysis. Do not claim visual details "
                "that are not visible in the image."
            ),
        },
        {"type": "image_url", "image_url": {"url": image_url}},
    ]


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
    if creative_type and "image" not in creative_type.lower():
        return None
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
        "video_guidance, market_game_style_pack, and compliance_guardrails. If "
        "market_game_style_pack is present, use it as mandatory internal game creative "
        "direction for topic angles, copy hooks, image briefs, storyboard scenes, and "
        "video prompts: adapt the visual world, abstract AAA-style genre archetypes, "
        "game interest hypothesis, and gameplay_process into an actual playable sequence "
        "with challenge, player action, retry or choice, progression feedback, unlock, "
        "and CTA. Do not name real games, copied characters, logos, UI, real religious "
        "figures, deity names, prayers, worship, sacrifices, scripture, sacred text, "
        "or religious claims. Use audience traits only as "
        "internal strategy; do not directly assert sensitive personal attributes in "
        "user-facing text. Honor legacy template fields too: template_id, "
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
        "game hub CTA beat. For gaja_brand, use a dark premium neon game lobby with "
        "visible challenge, failed-attempt, retry, and reward-payoff cues plus metallic "
        "GAJA logo or wordmark branding, no visible numeric suffix, and no visible "
        "brand-number text. Finish on a Start or Play Now CTA. Keep all claims about "
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
    return context


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
