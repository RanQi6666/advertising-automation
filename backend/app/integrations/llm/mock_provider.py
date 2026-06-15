import re
from typing import Any

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

    async def extract_delivery_fields(self, raw_content: str) -> dict:
        url_candidates = _find_urls(raw_content)
        country_candidates = _find_country_candidates(raw_content)
        event_value, event_status, event_reason = _find_event(raw_content)
        age_min, age_max = _find_age_range(raw_content)
        gender_value = _find_gender(raw_content)
        audience_raw = _find_audience_raw(raw_content, age_min, age_max, gender_value)

        return {
            "schema_version": "ad_delivery_extract_v1",
            "fields": {
                "landing_url": _field(
                    value=url_candidates[0] if len(url_candidates) == 1 else None,
                    status=(
                        "extracted"
                        if len(url_candidates) == 1
                        else "conflict"
                        if len(url_candidates) > 1
                        else "missing"
                    ),
                    confidence=0.96 if len(url_candidates) == 1 else 0.6 if url_candidates else 0,
                    evidence=url_candidates[:3],
                    candidates=url_candidates,
                    reason=(
                        "识别到唯一链接。"
                        if len(url_candidates) == 1
                        else "识别到多个链接，请人工选择。"
                        if len(url_candidates) > 1
                        else "未识别到落地页链接。"
                    ),
                ),
                "event_name": _field(
                    value=event_value,
                    normalized_value=_normalize_event(event_value),
                    status=event_status,
                    confidence=0.88 if event_status == "extracted" else 0.55,
                    evidence=[event_reason] if event_reason else [],
                    candidates=[event_value] if event_value else ["流量"],
                    reason=event_reason or "未识别到投放事件，默认建议流量。",
                ),
                "country": _field(
                    value=country_candidates[0] if len(country_candidates) == 1 else None,
                    normalized_value=(
                        _normalize_country(country_candidates[0])
                        if len(country_candidates) == 1
                        else None
                    ),
                    status=(
                        "extracted"
                        if len(country_candidates) == 1
                        else "conflict"
                        if len(country_candidates) > 1
                        else "missing"
                    ),
                    confidence=0.9 if len(country_candidates) == 1 else 0.55,
                    evidence=country_candidates[:3],
                    candidates=country_candidates,
                    reason=(
                        "识别到唯一投放国家。"
                        if len(country_candidates) == 1
                        else "识别到多个可能国家，请人工确认。"
                        if len(country_candidates) > 1
                        else "未识别到投放国家。"
                    ),
                ),
                "age_min": _field(
                    value=age_min,
                    normalized_value=age_min,
                    status="extracted" if age_min else "suggested",
                    confidence=0.9 if age_min else 0.45,
                    evidence=[f"{age_min}-{age_max}"] if age_min and age_max else [],
                    candidates=[age_min] if age_min else ["不限"],
                    reason="识别到年龄范围。" if age_min else "未写年龄，建议不限。",
                ),
                "age_max": _field(
                    value=age_max,
                    normalized_value=age_max,
                    status="extracted" if age_max else "suggested",
                    confidence=0.9 if age_max else 0.45,
                    evidence=[f"{age_min}-{age_max}"] if age_min and age_max else [],
                    candidates=[age_max] if age_max else ["不限"],
                    reason="识别到年龄范围。" if age_max else "未写年龄，建议不限。",
                ),
                "gender": _field(
                    value=gender_value or "不限",
                    normalized_value=_normalize_gender(gender_value),
                    status="extracted" if gender_value else "suggested",
                    confidence=0.9 if gender_value else 0.45,
                    evidence=[gender_value] if gender_value else [],
                    candidates=[gender_value] if gender_value else ["不限"],
                    reason="识别到性别定向。" if gender_value else "未写性别，建议不限。",
                ),
                "audience_description_raw": _field(
                    value=audience_raw,
                    normalized_value=audience_raw,
                    status="extracted" if audience_raw else "suggested",
                    confidence=0.85 if audience_raw else 0.45,
                    evidence=[audience_raw] if audience_raw else [],
                    candidates=[audience_raw] if audience_raw else [],
                    reason="保留人群原文供人工确认。" if audience_raw else "未识别到人群原文。",
                ),
            },
            "review": {
                "must_confirm": ["landing_url", "event_name", "country"],
                "missing_fields": [
                    key
                    for key, value in {
                        "landing_url": bool(url_candidates),
                        "country": bool(country_candidates),
                    }.items()
                    if not value
                ],
                "conflict_fields": [
                    key
                    for key, values in {
                        "landing_url": url_candidates,
                        "country": country_candidates,
                    }.items()
                    if len(values) > 1
                ],
                "suggested_fields": [
                    key
                    for key, is_suggested in {
                        "event_name": event_status == "suggested",
                        "age_min": age_min is None,
                        "age_max": age_max is None,
                        "gender": gender_value is None,
                    }.items()
                    if is_suggested
                ],
            },
        }

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


def _field(
    value: Any | None,
    status: str,
    confidence: float,
    evidence: list[Any],
    candidates: list[Any],
    reason: str,
    normalized_value: Any | None = None,
) -> dict[str, Any]:
    return {
        "value": value,
        "normalized_value": normalized_value if normalized_value is not None else value,
        "status": status,
        "confidence": max(0, min(1, confidence)),
        "evidence": [str(item) for item in evidence if item],
        "candidates": [item for item in candidates if item],
        "reason": reason,
    }


def _find_urls(raw_content: str) -> list[str]:
    urls = re.findall(r"https?://[^\s，,。；;）)】\]]+", raw_content, flags=re.I)
    return list(dict.fromkeys(url.strip() for url in urls))


COUNTRY_ALIASES = {
    "印度": "印度",
    "india": "印度",
    "in": "印度",
    "美国": "美国",
    "usa": "美国",
    "us": "美国",
    "united states": "美国",
    "菲律宾": "菲律宾",
    "philippines": "菲律宾",
    "印尼": "印度尼西亚",
    "印度尼西亚": "印度尼西亚",
    "indonesia": "印度尼西亚",
    "泰国": "泰国",
    "thailand": "泰国",
    "越南": "越南",
    "vietnam": "越南",
    "马来西亚": "马来西亚",
    "malaysia": "马来西亚",
    "新加坡": "新加坡",
    "singapore": "新加坡",
    "巴西": "巴西",
    "brazil": "巴西",
    "墨西哥": "墨西哥",
    "mexico": "墨西哥",
}


COUNTRY_CODES = {
    "印度": "IN",
    "美国": "US",
    "菲律宾": "PH",
    "印度尼西亚": "ID",
    "泰国": "TH",
    "越南": "VN",
    "马来西亚": "MY",
    "新加坡": "SG",
    "巴西": "BR",
    "墨西哥": "MX",
}


def _find_country_candidates(raw_content: str) -> list[str]:
    lowered = raw_content.lower()
    found: list[str] = []
    for alias, country in COUNTRY_ALIASES.items():
        pattern = rf"(?<![a-z]){re.escape(alias.lower())}(?![a-z])"
        if re.search(pattern, lowered):
            found.append(country)
    return list(dict.fromkeys(found))


def _normalize_country(country: str | None) -> str | None:
    if not country:
        return None
    return COUNTRY_CODES.get(country, country)


def _find_event(raw_content: str) -> tuple[str, str, str]:
    normalized = re.sub(r"\s+", "", raw_content.lower())
    if any(keyword in normalized for keyword in ["购物", "购买", "下单", "purchase", "shop"]):
        return "购物", "extracted", "识别到购买/购物意图。"
    if any(keyword in normalized for keyword in ["加购", "addtocart", "cart"]):
        return "加购", "extracted", "识别到加购意图。"
    if any(keyword in normalized for keyword in ["注册", "线索", "lead", "signup"]):
        return "线索", "extracted", "识别到注册/线索意图。"
    if any(keyword in normalized for keyword in ["流量", "点击", "traffic", "click"]):
        return "流量", "extracted", "识别到流量/点击目标。"
    return "流量", "suggested", "未明确写投放事件，默认建议流量。"


def _normalize_event(event_name: str | None) -> str | None:
    if event_name == "购物":
        return "purchase"
    if event_name == "加购":
        return "add_to_cart"
    if event_name == "线索":
        return "lead"
    if event_name == "流量":
        return "traffic"
    return event_name


def _find_age_range(raw_content: str) -> tuple[int | None, int | None]:
    pattern = re.compile(r"(?:年龄|age)?[^\d]{0,8}(\d{2})\s*(?:-|~|至|到|—|–)\s*(\d{2})", re.I)
    match = pattern.search(raw_content)
    if not match:
        return None, None
    age_min = int(match.group(1))
    age_max = int(match.group(2))
    if 13 <= age_min <= age_max <= 65:
        return age_min, age_max
    return None, None


def _find_gender(raw_content: str) -> str | None:
    lowered = raw_content.lower()
    has_female = "女" in lowered or re.search(r"\b(female|women|woman)\b", lowered) is not None
    has_male = "男" in lowered or re.search(r"\b(male|men|man)\b", lowered) is not None
    if has_male and not has_female:
        return "男"
    if has_female and not has_male:
        return "女"
    if any(keyword in lowered for keyword in ["不限", "all gender", "all genders"]):
        return "不限"
    return None


def _normalize_gender(gender: str | None) -> str:
    if gender == "男":
        return "male"
    if gender == "女":
        return "female"
    return "all"


def _find_audience_raw(
    raw_content: str,
    age_min: int | None,
    age_max: int | None,
    gender: str | None,
) -> str | None:
    for line in raw_content.replace("\r\n", "\n").split("\n"):
        if any(keyword in line for keyword in ["投放人群", "目标人群", "受众", "人群"]):
            return line.strip()
    parts = []
    if gender:
        parts.append(gender)
    if age_min and age_max:
        parts.append(f"年龄{age_min}-{age_max}")
    return "，".join(parts) if parts else None
