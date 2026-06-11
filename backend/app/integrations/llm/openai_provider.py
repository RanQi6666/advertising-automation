import json
from typing import Any

from openai import AsyncOpenAI

from backend.app.core.errors import ProviderError
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.topic import ContentTopic
from backend.app.schemas.ai import (
    CopyDraftCandidate,
    ImageBrief,
    TopicCandidate,
    VideoStoryboardCandidate,
    VideoStoryboardScene,
)


class OpenAILLMProvider:
    def __init__(self, api_key: str, model: str, base_url: str | None = None) -> None:
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    async def _json_completion(self, system: str, user: str) -> dict[str, Any]:
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

    async def generate_topics(
        self,
        campaign: Campaign,
        limit: int,
        signals: dict,
    ) -> list[TopicCandidate]:
        data = await self._json_completion(
            system=(
                "You are an advertising strategist. Return valid JSON only with key "
                "'topics'. Each topic must include title, angle, audience, "
                "selling_points, risk_notes, rationale, and score. Use Chinese for "
                "internal planning fields unless the campaign explicitly requires "
                "another language."
            ),
            user=json.dumps(
                {
                    "campaign": {
                        "name": campaign.name,
                        "objective": campaign.objective,
                        "product_name": campaign.product_name,
                        "audience_description": campaign.audience_description,
                        "metadata": campaign.metadata_json,
                    },
                    "signals": signals,
                    "limit": limit,
                },
                ensure_ascii=False,
            ),
        )
        return [_topic_candidate_from_data(item) for item in data.get("topics", [])[:limit]]

    async def generate_copy(
        self,
        campaign: Campaign,
        topic: ContentTopic,
        constraints: dict,
    ) -> CopyDraftCandidate:
        data = await self._json_completion(
            system=(
                "You are a direct-response Facebook copywriter. Return valid JSON only "
                "with body, primary_text, headline, description, and cta. Use the "
                "campaign, work order, and landing page context. Avoid unsupported "
                "claims and sensitive personal attributes."
            ),
            user=json.dumps(
                {
                    "campaign": {
                        "name": campaign.name,
                        "objective": campaign.objective,
                        "product_name": campaign.product_name,
                        "audience_description": campaign.audience_description,
                        "metadata": campaign.metadata_json,
                    },
                    "topic": {
                        "title": topic.title,
                        "angle": topic.angle,
                        "selling_points": topic.selling_points,
                        "risk_notes": topic.risk_notes,
                    },
                    "constraints": constraints,
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
        data = await self._json_completion(
            system=(
                "Revise Facebook ad copy from human feedback. Return valid JSON only "
                "with body, primary_text, headline, description, and cta."
            ),
            user=json.dumps(
                {
                    "campaign_name": campaign.name,
                    "topic": topic.title,
                    "existing_draft": draft.body,
                    "feedback": feedback,
                    "constraints": constraints,
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
    ) -> list[ImageBrief]:
        data = await self._json_completion(
            system=(
                "Turn ad copy into concise image briefs. Return valid JSON only with "
                "key 'briefs'. Each brief must include image_index, title, short_text, "
                "visual_direction, and size. Do not generate images."
            ),
            user=json.dumps(
                {
                    "copy": draft.body,
                    "headline": draft.headline,
                    "draft_metadata": draft.metadata_json,
                    "count": count,
                    "size": size,
                },
                ensure_ascii=False,
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
        data = await self._json_completion(
            system=(
                "You are a senior short-form video ad director. Return valid JSON only "
                "with duration_seconds, aspect_ratio, scenes, and rationale. Each scene "
                "must include scene_index, start_second, end_second, visual, subtitle, "
                "motion, voiceover, source_asset_ids, and notes. Use the selected images "
                "as source assets; do not invent unavailable image ids. Keep subtitles "
                "short, readable, and suitable for Facebook placements."
            ),
            user=json.dumps(
                {
                    "campaign": {
                        "name": campaign.name,
                        "objective": campaign.objective,
                        "product_name": campaign.product_name,
                        "audience_description": campaign.audience_description,
                        "metadata": campaign.metadata_json,
                    },
                    "copy_draft": _draft_context(draft),
                    "assets": [_asset_context(asset) for asset in assets],
                    "duration_seconds": duration_seconds,
                    "aspect_ratio": aspect_ratio,
                    "context": context,
                    "instructions": instructions,
                },
                ensure_ascii=False,
            ),
        )
        return _video_storyboard_from_data(
            data=data,
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
            allowed_asset_ids={asset.id for asset in assets},
        )


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


def _topic_candidate_from_data(data: dict[str, Any]) -> TopicCandidate:
    normalized = {
        "title": _coerce_text(data.get("title")),
        "angle": _coerce_text(data.get("angle")),
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
        "title": _coerce_text(data.get("title")),
        "short_text": _coerce_text(data.get("short_text")),
        "visual_direction": _coerce_text(data.get("visual_direction")),
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
        "visual": _coerce_text(data.get("visual")),
        "subtitle": _coerce_optional_text(data.get("subtitle")),
        "motion": _coerce_optional_text(data.get("motion")),
        "voiceover": _coerce_optional_text(data.get("voiceover")),
        "source_asset_ids": source_asset_ids,
        "notes": _coerce_optional_text(data.get("notes")),
    }
    return VideoStoryboardScene.model_validate(normalized)


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
        "metadata": draft.metadata_json,
    }


def _asset_context(asset: CreativeAsset) -> dict[str, Any]:
    return {
        "id": asset.id,
        "url": asset.url,
        "prompt": asset.prompt,
        "alt_text": asset.alt_text,
        "size": asset.size,
        "metadata": asset.metadata_json,
    }


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
