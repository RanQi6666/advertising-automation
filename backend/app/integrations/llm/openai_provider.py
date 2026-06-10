import json
from typing import Any

from openai import AsyncOpenAI

from backend.app.core.errors import ProviderError
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.topic import ContentTopic
from backend.app.schemas.ai import CopyDraftCandidate, ImageBrief, TopicCandidate


class OpenAILLMProvider:
    def __init__(self, api_key: str, model: str) -> None:
        self.client = AsyncOpenAI(api_key=api_key)
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
                "You are an advertising strategist. Return JSON with key 'topics'. "
                "Each topic must include title, angle, audience, selling_points, "
                "risk_notes, rationale, and score."
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
        return [TopicCandidate.model_validate(item) for item in data.get("topics", [])[:limit]]

    async def generate_copy(
        self,
        campaign: Campaign,
        topic: ContentTopic,
        constraints: dict,
    ) -> CopyDraftCandidate:
        data = await self._json_completion(
            system=(
                "You are a direct-response Facebook copywriter. Return JSON with body, "
                "primary_text, headline, description, and cta."
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
        return CopyDraftCandidate.model_validate(data)

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
                "Revise Facebook ad copy from human feedback. Return JSON with body, "
                "primary_text, headline, description, and cta."
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
        return CopyDraftCandidate.model_validate(data)

    async def generate_image_briefs(
        self,
        draft: CopyDraft,
        count: int,
        size: str,
    ) -> list[ImageBrief]:
        data = await self._json_completion(
            system=(
                "Turn ad copy into concise image briefs. Return JSON with key 'briefs'. "
                "Each brief must include image_index, title, short_text, visual_direction, size."
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
        return [ImageBrief.model_validate(item) for item in data.get("briefs", [])[:count]]
