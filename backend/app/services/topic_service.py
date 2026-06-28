from collections.abc import AsyncIterator
from urllib.parse import urlparse

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.enums import TopicStatus
from backend.app.db.models.topic import ContentTopic
from backend.app.db.models.work_order import WorkOrder
from backend.app.integrations.llm import get_llm_provider
from backend.app.integrations.llm.language import build_target_language_context
from backend.app.schemas.ai import TopicCandidate
from backend.app.schemas.landing_page import LandingPageAnalyzeRequest
from backend.app.schemas.topic import TopicGenerateRequest, TopicRead
from backend.app.services.creative_strategy_builder import (
    build_creative_strategy,
    compact_creative_strategy,
)
from backend.app.services.landing_page_service import (
    LandingPageService,
    snapshot_to_context,
)
from backend.app.services.model_selection import effective_text_model, settings_for_text_model
from backend.app.services.utils import get_required

TOPIC_LANDING_EXCERPT_CHARS = 600
TOPIC_TEXT_FIELD_CHARS = 500
TOPIC_SELLING_POINT_LIMIT = 5


class TopicService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.landing_pages = LandingPageService()

    async def generate_topics(
        self,
        session: AsyncSession,
        payload: TopicGenerateRequest,
    ) -> list[ContentTopic]:
        campaign = await get_required(session, Campaign, payload.campaign_id)
        effective_signals = await self._build_effective_signals(
            session=session,
            campaign=campaign,  # type: ignore[arg-type]
            request_signals=payload.signals,
        )
        if _should_replace_existing_topics(effective_signals):
            await self._reject_existing_topics(session, payload.campaign_id)
        llm_settings = settings_for_text_model(self.settings, payload.model_id)
        llm = get_llm_provider(llm_settings)
        model_name = effective_text_model(llm_settings)
        candidates = await llm.generate_topics(
            campaign=campaign,  # type: ignore[arg-type]
            limit=payload.limit,
            signals=effective_signals,
        )
        topics: list[ContentTopic] = []
        angle_plan = _topic_angle_plan(effective_signals)
        for index, candidate in enumerate(candidates):
            topic = self._topic_from_candidate(
                campaign_id=payload.campaign_id,
                candidate=candidate,
                signals=effective_signals,
                streamed=False,
                provider=llm_settings.llm_provider,
                model=model_name,
                angle_plan_item=_angle_plan_item(angle_plan, index),
            )
            session.add(topic)
            topics.append(topic)

        await session.commit()
        for topic in topics:
            await session.refresh(topic)
        return topics

    async def stream_topics(
        self,
        session: AsyncSession,
        payload: TopicGenerateRequest,
    ) -> AsyncIterator[dict]:
        campaign = await get_required(session, Campaign, payload.campaign_id)
        effective_signals = await self._build_effective_signals(
            session=session,
            campaign=campaign,  # type: ignore[arg-type]
            request_signals=payload.signals,
        )
        if _should_replace_existing_topics(effective_signals):
            await self._reject_existing_topics(session, payload.campaign_id)
            await session.commit()

        limit = payload.limit
        llm_settings = settings_for_text_model(self.settings, payload.model_id)
        llm = get_llm_provider(llm_settings)
        model_name = effective_text_model(llm_settings)
        yield {"type": "start", "limit": limit}
        for index in range(1, limit + 1):
            yield {"type": "slot", "index": index}

        generated_count = 0
        angle_plan = _topic_angle_plan(effective_signals)
        try:
            async for candidate in llm.stream_topics(
                campaign=campaign,  # type: ignore[arg-type]
                limit=limit,
                signals=effective_signals,
            ):
                if generated_count >= limit:
                    break
                generated_count += 1
                topic = self._topic_from_candidate(
                    campaign_id=payload.campaign_id,
                    candidate=candidate,
                    signals=effective_signals,
                    streamed=True,
                    provider=llm_settings.llm_provider,
                    model=model_name,
                    angle_plan_item=_angle_plan_item(angle_plan, generated_count - 1),
                )
                session.add(topic)
                await session.commit()
                await session.refresh(topic)
                yield {
                    "type": "topic",
                    "index": generated_count,
                    "topic": TopicRead.model_validate(topic).model_dump(mode="json"),
                }
        except Exception as exc:
            for index in range(generated_count + 1, limit + 1):
                yield {
                    "type": "error",
                    "index": index,
                    "message": f"选题生成中断：{exc}",
                }
            yield {"type": "done", "generated": generated_count}
            return

        for index in range(generated_count + 1, limit + 1):
            yield {
                "type": "error",
                "index": index,
                "message": "模型未返回此候选，请重新生成。",
            }
        yield {"type": "done", "generated": generated_count}

    def _topic_from_candidate(
        self,
        campaign_id: str,
        candidate: TopicCandidate,
        signals: dict,
        streamed: bool,
        provider: str,
        model: str,
        angle_plan_item: dict | None = None,
    ) -> ContentTopic:
        source_data = {
            "signals": signals,
            "provider": provider,
            "model": model,
            "streamed": streamed,
            **(
                {"creative_strategy": signals["creative_strategy"]}
                if isinstance(signals.get("creative_strategy"), dict)
                else {}
            ),
            **({"topic_angle": angle_plan_item} if angle_plan_item else {}),
            **({"angle_type": candidate.angle_type} if candidate.angle_type else {}),
        }
        return ContentTopic(
            campaign_id=campaign_id,
            title=candidate.title,
            angle=candidate.angle,
            audience=candidate.audience,
            selling_points=candidate.selling_points,
            risk_notes=candidate.risk_notes,
            rationale=candidate.rationale,
            score=candidate.score,
            source_data=source_data,
        )

    async def list_topics(
        self,
        session: AsyncSession,
        campaign_id: str,
        limit: int,
        offset: int,
    ) -> list[ContentTopic]:
        result = await session.execute(
            select(ContentTopic)
            .where(ContentTopic.campaign_id == campaign_id)
            .order_by(ContentTopic.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def select_topic(self, session: AsyncSession, topic_id: str) -> ContentTopic:
        topic = await get_required(session, ContentTopic, topic_id)
        await session.execute(
            update(ContentTopic)
            .where(
                ContentTopic.campaign_id == topic.campaign_id,
                ContentTopic.id != topic.id,
                ContentTopic.status == TopicStatus.SELECTED.value,
            )
            .values(status=TopicStatus.PROPOSED.value)
        )
        topic.status = TopicStatus.SELECTED.value
        await session.commit()
        await session.refresh(topic)
        return topic  # type: ignore[return-value]

    async def _build_effective_signals(
        self,
        session: AsyncSession,
        campaign: Campaign,
        request_signals: dict,
    ) -> dict:
        signals = _topic_request_signals(request_signals)
        work_order_context = _dict_value(campaign.metadata_json.get("work_order"))

        if not work_order_context and campaign.work_order_id:
            work_order = await get_required(session, WorkOrder, campaign.work_order_id)
            work_order_context = {
                "parsed_fields": work_order.parsed_fields,
                "country": work_order.country,
                "media": work_order.media,
                "landing_url": work_order.landing_url,
                "report_timezone": work_order.report_timezone,
            }

        if work_order_context:
            signals["work_order"] = _topic_work_order_context(work_order_context)

        landing_page_context = await self._landing_page_context_for_topics(
            session=session,
            campaign=campaign,
            work_order_context=work_order_context,
        )

        landing_page = _topic_landing_page_context(landing_page_context, work_order_context)
        if landing_page:
            signals["landing_page"] = landing_page

        selling_points = _topic_selling_points(request_signals, landing_page)
        if selling_points:
            signals["selling_points"] = selling_points

        creative_strategy = _topic_creative_strategy(
            campaign=campaign,
            work_order_context=work_order_context,
            landing_page_context=landing_page_context,
            landing_page=landing_page,
        )
        if creative_strategy:
            signals["creative_strategy"] = creative_strategy

        signals["target_language"] = build_target_language_context(
            campaign=campaign,
            signals=signals,
        )
        return signals

    async def _landing_page_context_for_topics(
        self,
        session: AsyncSession,
        campaign: Campaign,
        work_order_context: dict | None,
    ) -> dict | None:
        metadata_context = _dict_value(campaign.metadata_json.get("landing_page"))
        if _is_fetched_landing_page_context(metadata_context):
            return metadata_context

        latest_snapshot = await self.landing_pages.get_latest_snapshot(session, campaign.id)
        if latest_snapshot and latest_snapshot.status == "fetched":
            return snapshot_to_context(latest_snapshot)

        landing_url = _first_text(
            metadata_context.get("url"),
            _landing_url_from_context(work_order_context),
        )
        if landing_url:
            try:
                snapshot = await self.landing_pages.analyze_campaign_landing_page(
                    session=session,
                    campaign_id=campaign.id,
                    payload=LandingPageAnalyzeRequest(
                        url=landing_url,
                        metadata_json={"source": "topic_generation"},
                    ),
                )
                return snapshot_to_context(snapshot)
            except Exception:
                # Topic generation should still work when a landing page blocks fetching.
                pass

        if metadata_context:
            return metadata_context
        if latest_snapshot:
            return snapshot_to_context(latest_snapshot)
        if landing_url:
            return {"url": landing_url, "status": "provided"}
        return None

    async def reject_topic(self, session: AsyncSession, topic_id: str) -> ContentTopic:
        topic = await get_required(session, ContentTopic, topic_id)
        topic.status = TopicStatus.REJECTED.value
        await session.commit()
        await session.refresh(topic)
        return topic  # type: ignore[return-value]

    async def _reject_existing_topics(self, session: AsyncSession, campaign_id: str) -> None:
        await session.execute(
            update(ContentTopic)
            .where(
                ContentTopic.campaign_id == campaign_id,
                ContentTopic.status.in_(
                    [TopicStatus.PROPOSED.value, TopicStatus.SELECTED.value],
                ),
            )
            .values(status=TopicStatus.REJECTED.value)
        )


def _landing_url_from_context(work_order_context: dict | None) -> str | None:
    if not work_order_context:
        return None
    parsed_fields = _dict_value(work_order_context.get("parsed_fields"))
    return work_order_context.get("landing_url") or parsed_fields.get("landing_url")


def _is_fetched_landing_page_context(context: dict) -> bool:
    return bool(context) and context.get("status") == "fetched"


def _topic_revision_feedback(signals: dict) -> str:
    feedback = signals.get("topic_revision_feedback")
    return feedback.strip() if isinstance(feedback, str) else ""


def _should_replace_existing_topics(signals: dict) -> bool:
    return (
        bool(_topic_revision_feedback(signals))
        and signals.get("topic_generation_mode") == "revise_from_operator_feedback"
    )


def _topic_request_signals(request_signals: dict) -> dict:
    signals: dict = {}
    for key in ("integration", "topic_generation_mode"):
        value = _text_or_none(request_signals.get(key))
        if value:
            signals[key] = _trim(value, 120)

    feedback = _text_or_none(request_signals.get("topic_revision_feedback"))
    if feedback:
        signals["topic_revision_feedback"] = _trim(feedback, TOPIC_TEXT_FIELD_CHARS * 2)

    previous_topics = request_signals.get("previous_topics")
    if isinstance(previous_topics, list):
        compact_topics = [
            topic
            for item in previous_topics[:6]
            if (topic := _compact_previous_topic(item))
        ]
        if compact_topics:
            signals["previous_topics"] = compact_topics

    return signals


def _topic_work_order_context(work_order_context: dict) -> dict:
    parsed_fields = _dict_value(work_order_context.get("parsed_fields"))
    context = {
        "country": _first_text(work_order_context.get("country"), parsed_fields.get("country")),
        "media": _first_text(work_order_context.get("media"), parsed_fields.get("media")),
        "event_name": _first_text(
            work_order_context.get("event_name"),
            parsed_fields.get("event_name"),
            parsed_fields.get("objective"),
        ),
        "age_min": _first_text(parsed_fields.get("age_min"), parsed_fields.get("min_age")),
        "age_max": _first_text(parsed_fields.get("age_max"), parsed_fields.get("max_age")),
        "gender": _first_text(parsed_fields.get("gender"), parsed_fields.get("sex")),
        "audience": _trim(
            _first_text(
                work_order_context.get("audience_description"),
                parsed_fields.get("audience_description_raw"),
                parsed_fields.get("audience_description"),
                parsed_fields.get("audience"),
            ),
            TOPIC_TEXT_FIELD_CHARS,
        ),
        "landing_url": _first_text(
            work_order_context.get("landing_url"),
            parsed_fields.get("landing_url"),
            parsed_fields.get("url"),
        ),
        "report_timezone": _first_text(
            work_order_context.get("report_timezone"),
            parsed_fields.get("report_timezone"),
        ),
    }
    if context["landing_url"]:
        context["landing_domain"] = _domain_from_url(context["landing_url"])
    return {key: value for key, value in context.items() if value not in (None, "", [])}


def _topic_landing_page_context(
    landing_page_context: dict | None,
    work_order_context: dict | None,
) -> dict:
    context = _dict_value(landing_page_context)
    extracted_data = _dict_value(context.get("extracted_data"))
    url = _first_text(context.get("url"), _landing_url_from_context(work_order_context))
    headings = _coerce_text_list(extracted_data.get("headings"))[:TOPIC_SELLING_POINT_LIMIT]
    landing_page = {
        "url": url,
        "domain": _domain_from_url(url),
        "status": _first_text(context.get("status")),
        "http_status": context.get("http_status"),
        "title": _trim(_text_or_none(context.get("title")), 160),
        "description": _trim(_text_or_none(context.get("description")), 320),
        "text_excerpt": _trim(
            _text_or_none(context.get("text_excerpt")),
            TOPIC_LANDING_EXCERPT_CHARS,
        ),
        "headings": [_trim(heading, 160) for heading in headings],
    }
    return {
        key: value
        for key, value in landing_page.items()
        if value not in (None, "", [])
    }


def _topic_selling_points(request_signals: dict, landing_page: dict) -> list[str]:
    points: list[str] = []
    points.extend(_coerce_text_list(request_signals.get("selling_points")))

    product_signals = _dict_value(request_signals.get("product_signals"))
    points.extend(_coerce_text_list(product_signals.get("selling_points")))

    points.extend(_coerce_text_list(landing_page.get("headings")))
    for key in ("title", "description"):
        value = _text_or_none(landing_page.get(key))
        if value:
            points.append(value)

    unique_points: list[str] = []
    seen: set[str] = set()
    for point in points:
        compact = _trim(point, 180)
        normalized = compact.casefold()
        if not compact or normalized in seen:
            continue
        seen.add(normalized)
        unique_points.append(compact)
        if len(unique_points) >= TOPIC_SELLING_POINT_LIMIT:
            break
    return unique_points


def _topic_creative_strategy(
    campaign: Campaign,
    work_order_context: dict | None,
    landing_page_context: dict | None,
    landing_page: dict,
) -> dict | None:
    campaign_metadata = _dict_value(campaign.metadata_json)
    existing_strategy = compact_creative_strategy(
        campaign_metadata.get("creative_strategy")
    )
    if existing_strategy:
        return existing_strategy

    work_order = _dict_value(work_order_context)
    parsed_fields = _dict_value(work_order.get("parsed_fields"))
    landing_context = _dict_value(landing_page_context)
    landing_url = _first_text(
        landing_page.get("url"),
        landing_context.get("url"),
        _landing_url_from_context(work_order),
    )
    strategy = build_creative_strategy(
        {
            "product_name": campaign.product_name,
            "campaign_name": campaign.name,
            "objective": campaign.objective,
            "audience_description": campaign.audience_description,
            "landing_url": landing_url,
            "landing_page": landing_page,
            "work_order": work_order,
            "structured_fields": parsed_fields,
            "reviewed_fields": _dict_value(work_order.get("reviewed_delivery_fields")),
            "brief": _first_text(
                work_order.get("brief"),
                parsed_fields.get("brief"),
                parsed_fields.get("description"),
                parsed_fields.get("audience_description_raw"),
            ),
            "event_name": _first_text(
                work_order.get("event_name"),
                parsed_fields.get("event_name"),
                parsed_fields.get("objective"),
                campaign.objective,
            ),
            "country": _first_text(work_order.get("country"), parsed_fields.get("country")),
            "media": _first_text(work_order.get("media"), parsed_fields.get("media")),
        }
    )
    return compact_creative_strategy(strategy)


def _topic_angle_plan(signals: dict) -> list[dict]:
    strategy = signals.get("creative_strategy")
    if not isinstance(strategy, dict):
        return []
    plan = strategy.get("topic_angle_plan")
    return [item for item in plan if isinstance(item, dict)] if isinstance(plan, list) else []


def _angle_plan_item(plan: list[dict], index: int) -> dict | None:
    if index < 0 or index >= len(plan):
        return None
    return plan[index]


def _compact_previous_topic(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    topic = {
        "title": _trim(_text_or_none(value.get("title")), 160),
        "angle": _trim(_text_or_none(value.get("angle")), 240),
        "status": _trim(_text_or_none(value.get("status")), 80),
        "risk_notes": _trim(_text_or_none(value.get("risk_notes")), 240),
    }
    compact = {key: item for key, item in topic.items() if item}
    return compact or None


def _dict_value(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _coerce_text_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [text for item in value if (text := _text_or_none(item))]
    text = _text_or_none(value)
    return [text] if text else []


def _first_text(*values: object) -> str | None:
    for value in values:
        text = _text_or_none(value)
        if text:
            return text
    return None


def _text_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _trim(value: str | None, max_chars: int) -> str | None:
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
