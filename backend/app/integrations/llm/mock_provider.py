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


class MockLLMProvider:
    """Deterministic provider for local development and tests."""

    async def generate_topics(
        self,
        campaign: Campaign,
        limit: int,
        signals: dict,
    ) -> list[TopicCandidate]:
        product = campaign.product_name or campaign.name
        audience = campaign.audience_description or "the target audience"
        work_order = signals.get("work_order") or campaign.metadata_json.get("work_order") or {}
        landing_page = (
            signals.get("landing_page") or campaign.metadata_json.get("landing_page") or {}
        )
        parsed_fields = work_order.get("parsed_fields", {})
        country = work_order.get("country") or parsed_fields.get("country")
        event_name = parsed_fields.get("event_name") or campaign.objective
        landing_title = landing_page.get("title")
        candidates: list[TopicCandidate] = []

        templates = [
            ("Pain point hook", "Lead with the daily pain the audience already understands."),
            ("Before and after", "Show the transformation customers can expect."),
            ("Proof and trust", "Use credibility, reviews, or measurable proof."),
            ("Limited offer", "Frame the message around urgency and a clear next step."),
            ("Educational angle", "Teach one useful idea before presenting the offer."),
        ]

        for index, (title, angle) in enumerate(templates[:limit], start=1):
            candidates.append(
                TopicCandidate(
                    title=f"{product}: {title}",
                    angle=_append_context(
                        angle,
                        country=country,
                        event_name=event_name,
                        landing_title=landing_title,
                    ),
                    audience=audience,
                    selling_points=[
                        f"Clear benefit for {product}",
                        "Simple Facebook-friendly message",
                        "Can be adapted into multiple creative images",
                    ],
                    risk_notes="Validate product claims and avoid unsupported guarantees.",
                    rationale=(
                        f"Mock topic {index} generated from campaign and work order signals."
                    ),
                    score=max(0.1, 0.95 - index * 0.06),
                )
            )
        return candidates

    async def generate_copy(
        self,
        campaign: Campaign,
        topic: ContentTopic,
        constraints: dict,
    ) -> CopyDraftCandidate:
        product = campaign.product_name or campaign.name
        work_order = campaign.metadata_json.get("work_order") or {}
        landing_page = campaign.metadata_json.get("landing_page") or {}
        parsed_fields = work_order.get("parsed_fields", {})
        country = work_order.get("country") or parsed_fields.get("country")
        media = work_order.get("media") or parsed_fields.get("media")
        landing_url = work_order.get("landing_url") or parsed_fields.get("landing_url")
        landing_title = landing_page.get("title")
        landing_excerpt = landing_page.get("text_excerpt")
        body = (
            f"{topic.title}\n\n"
            f"{topic.angle}\n\n"
            f"Market context: country={country or 'unspecified'}, "
            f"media={media or 'unspecified'}.\n\n"
            f"Landing page title: {landing_title or 'not fetched'}.\n\n"
            f"If your audience is looking for a better way to approach {product}, "
            "this campaign introduces the benefit clearly, supports it with proof, "
            "and ends with one simple call to action.\n\n"
            f"Landing page insight: {(landing_excerpt or 'not available')[:500]}\n\n"
            f"Landing page: {landing_url or 'not provided'}.\n\n"
            "Use this draft as the first human-review version before publication."
        )
        return CopyDraftCandidate(
            body=body,
            primary_text=body[:500],
            headline=f"Try {product} today",
            description="A clear, benefit-led Facebook ad draft.",
            cta=constraints.get("cta", "Learn More"),
        )

    async def revise_copy(
        self,
        campaign: Campaign,
        topic: ContentTopic,
        draft: CopyDraft,
        feedback: str,
        constraints: dict,
    ) -> CopyDraftCandidate:
        revised_body = (
            f"{draft.body}\n\nRevision note applied: {feedback}\n"
            "This version is tightened for review and publication."
        )
        return CopyDraftCandidate(
            body=revised_body,
            primary_text=revised_body[:500],
            headline=draft.headline,
            description=draft.description,
            cta=draft.cta or constraints.get("cta", "Learn More"),
        )

    async def generate_image_briefs(
        self,
        draft: CopyDraft,
        count: int,
        size: str,
    ) -> list[ImageBrief]:
        snippets = [
            "Main benefit",
            "Customer pain point",
            "Proof or reason to believe",
            "Simple offer",
            "Call to action",
        ]
        briefs: list[ImageBrief] = []
        landing_page = draft.metadata_json.get("landing_page") or {}
        landing_hint = landing_page.get("title") or landing_page.get("url")
        for index in range(count):
            briefs.append(
                ImageBrief(
                    image_index=index + 1,
                    title=snippets[index],
                    short_text=(draft.headline or snippets[index])[:80],
                    visual_direction=(
                        "Clean performance-ad layout with readable text, product focus, "
                        "and enough negative space for Facebook placements. "
                        f"Use landing page context: {landing_hint or 'not available'}."
                    ),
                    size=size,
                )
            )
        return briefs

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
        product = campaign.product_name or campaign.name
        asset_count = max(1, len(assets))
        scene_count = min(max(asset_count, 3), 5)
        segment = max(1, duration_seconds // scene_count)
        landing_page = context.get("landing_page") or {}
        landing_title = landing_page.get("title") or "landing page"
        scenes: list[VideoStoryboardScene] = []

        for index in range(scene_count):
            asset = assets[index % asset_count]
            start_second = index * segment
            end_second = duration_seconds if index == scene_count - 1 else (index + 1) * segment
            if index == 0:
                visual = f"Open with the strongest product benefit for {product}."
                subtitle = f"{product}: watch instantly"
            elif index == scene_count - 1:
                visual = "End on a clear call to action and keep the final frame readable."
                subtitle = "Download Now"
            else:
                visual = f"Show proof and variety using context from {landing_title[:80]}."
                subtitle = "Free live channels in HD"

            scenes.append(
                VideoStoryboardScene(
                    scene_index=index + 1,
                    start_second=start_second,
                    end_second=end_second,
                    visual=f"{visual} Use image asset {asset.id}.",
                    subtitle=subtitle,
                    motion="Slow zoom, quick text reveal, and clean vertical-safe framing.",
                    voiceover=(
                        draft.primary_text[:120]
                        if draft and draft.primary_text
                        else f"Discover {product} in a simple, fast experience."
                    ),
                    source_asset_ids=[asset.id],
                    notes=instructions or "Mock storyboard for reserved video generation.",
                )
            )

        return VideoStoryboardCandidate(
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
            scenes=scenes,
            rationale=(
                "Mock storyboard uses selected creative assets, copy context, and landing page "
                "signals to produce a reviewable video plan."
            ),
        )


def _append_context(
    angle: str,
    country: str | None,
    event_name: str | None,
    landing_title: str | None,
) -> str:
    context_parts = []
    if country:
        context_parts.append(f"targeting {country}")
    if event_name:
        context_parts.append(f"optimized for {event_name}")
    if landing_title:
        context_parts.append(f"based on landing page '{landing_title[:80]}'")
    if not context_parts:
        return angle
    return f"{angle} Context: {', '.join(context_parts)}."
