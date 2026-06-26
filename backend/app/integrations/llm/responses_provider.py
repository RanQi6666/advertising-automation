import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from backend.app.core.errors import ProviderError
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.integrations.llm.language import build_target_language_context
from backend.app.integrations.llm.openai_provider import (
    OpenAILLMProvider,
    _asset_context,
    _compact_storyboard_for_revision,
    _draft_context,
    _strip_json_markdown,
    _truncate,
    _video_storyboard_text_system_prompt,
)
from backend.app.schemas.ai import TopicCandidate


class GatewayResponsesLLMProvider(OpenAILLMProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/") + "/"
        self.supports_video_input = False
        self.video_input_fps = 1.0
        self._http_client = http_client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=60.0,
        )

    async def _json_completion(self, system: str, user: Any) -> dict[str, Any]:
        content = await self._text_completion(system, user)
        content = _strip_json_markdown(content)
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise ProviderError("LLM returned invalid JSON.") from exc

    async def _text_completion(self, system: str, user: Any) -> str:
        response = await self._http_client.post(
            "responses",
            json={
                "model": self.model,
                "input": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": _responses_user_content(user)},
                ],
            },
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:1000]
            raise ProviderError(
                f"Gateway responses API returned HTTP {exc.response.status_code}: {body}"
            ) from exc
        return _extract_response_text(response.json())

    async def stream_ad_performance_analysis(
        self,
        context: dict,
    ) -> AsyncIterator[dict[str, Any]]:
        analysis = await self.analyze_ad_performance(context)
        text = json.dumps(analysis, ensure_ascii=False)
        yield {"type": "delta", "text": text}
        yield {"type": "done", "analysis": analysis, "text": text}

    async def stream_topics(
        self,
        campaign: Campaign,
        limit: int,
        signals: dict,
    ) -> AsyncIterator[TopicCandidate]:
        for candidate in await self.generate_topics(campaign, limit, signals):
            yield candidate

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
        text = await self._text_completion(
            system=_video_storyboard_text_system_prompt(revision=False),
            user=json.dumps(
                {
                    "campaign": _campaign_context(campaign),
                    "copy_draft": _draft_context(draft),
                    "assets": [_asset_context(asset) for asset in assets],
                    "selected_asset_ids": [asset.id for asset in assets],
                    "duration_seconds": duration_seconds,
                    "aspect_ratio": aspect_ratio,
                    "context": context,
                    "instructions": instructions,
                    "target_language": target_language,
                },
                ensure_ascii=False,
            ),
        )
        if text:
            yield text

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
        text = await self._text_completion(
            system=_video_storyboard_text_system_prompt(revision=True),
            user=json.dumps(
                {
                    "campaign": _campaign_context(campaign),
                    "copy_draft": _draft_context(draft),
                    "assets": [_asset_context(asset) for asset in assets],
                    "selected_asset_ids": [asset.id for asset in assets],
                    "duration_seconds": duration_seconds,
                    "aspect_ratio": aspect_ratio,
                    "context": context,
                    "current_storyboard": _compact_storyboard_for_revision(
                        current_storyboard
                    ),
                    "current_storyboard_text": _truncate(current_storyboard_text, 6000),
                    "revision_feedback": feedback,
                    "target_language": target_language,
                },
                ensure_ascii=False,
            ),
        )
        if text:
            yield text


def _responses_user_content(user: Any) -> str | list[dict[str, Any]]:
    if not isinstance(user, list):
        return user if isinstance(user, str) else json.dumps(user, ensure_ascii=False)

    content: list[dict[str, Any]] = []
    for item in user:
        if not isinstance(item, dict):
            content.append({"type": "input_text", "text": str(item)})
            continue
        if item.get("type") == "text":
            content.append({"type": "input_text", "text": str(item.get("text", ""))})
            continue
        if item.get("type") == "image_url":
            image_url = item.get("image_url")
            if isinstance(image_url, dict) and image_url.get("url"):
                content.append(
                    {"type": "input_image", "image_url": str(image_url["url"])}
                )
            continue
        content.append(
            {"type": "input_text", "text": json.dumps(item, ensure_ascii=False)}
        )
    return content


def _extract_response_text(data: dict[str, Any]) -> str:
    direct_text = data.get("output_text")
    if isinstance(direct_text, str) and direct_text.strip():
        return direct_text

    chunks: list[str] = []
    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text:
                    chunks.append(text)
    if chunks:
        return "".join(chunks)
    raise ProviderError("Gateway responses API returned no text output.")


def _campaign_context(campaign: Campaign) -> dict[str, Any]:
    return {
        "name": campaign.name,
        "objective": campaign.objective,
        "product_name": campaign.product_name,
        "audience_description": campaign.audience_description,
        "metadata": campaign.metadata_json,
    }
