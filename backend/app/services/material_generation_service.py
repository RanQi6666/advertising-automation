from typing import Any

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.topic import ContentTopic
from backend.app.schemas.material_generation import (
    MATERIAL_CODE_BRAND_SAFETY_ERROR,
    MATERIAL_CODE_VALIDATION_ERROR,
    MaterialCopyGenerateRequest,
    MaterialGenerationAPIError,
    MaterialGenerationEnvelope,
)
from backend.app.services.brand_safety_policy import scan_brand_safety
from backend.app.services.copywriting_service import CopywritingService

SOURCE = "external_material_generation"


class MaterialGenerationService:
    def __init__(self) -> None:
        self.copywriting = CopywritingService()

    async def generate_copy(
        self,
        session: AsyncSession,
        payload: MaterialCopyGenerateRequest,
    ) -> MaterialGenerationEnvelope:
        _validate_base_payload(payload)

        existing = await self._find_existing_copy(session, payload.external_request_id)
        if existing:
            return self._copy_response(existing, payload)

        try:
            campaign, topic = await self._create_context(session, payload)
            candidate = await self.copywriting.llm.generate_copy(
                campaign=campaign,
                topic=topic,
                constraints=payload.constraints,
            )
            custom_event_type = payload.customEventType or _custom_event_type(payload.event_name)
            draft = CopyDraft(
                campaign_id=campaign.id,
                topic_id=topic.id,
                body=candidate.body,
                primary_text=candidate.primary_text,
                headline=candidate.headline,
                description=candidate.description,
                cta=candidate.cta,
                model_name=self.copywriting.settings.llm_model,
                prompt_version="copywriting.v1",
                metadata_json={
                    "source": SOURCE,
                    "external_request_id": payload.external_request_id,
                    "material_type": "copy",
                    "customEventType": custom_event_type,
                },
            )
            session.add(draft)
            await session.flush()

            response = self._copy_response(draft, payload)
            self._raise_if_brand_safety_blocked(response.data)

            await session.commit()
            await session.refresh(draft)
            return self._copy_response(draft, payload)
        except MaterialGenerationAPIError:
            await session.rollback()
            raise

    async def _create_context(
        self,
        session: AsyncSession,
        payload: MaterialCopyGenerateRequest,
    ) -> tuple[Campaign, ContentTopic]:
        product_name = (payload.product_name or "").strip()
        brief = (payload.brief or "").strip()
        selling_points = [point.strip() for point in payload.selling_points if point.strip()]
        landing_url = str(payload.landing_url) if payload.landing_url else None

        external_context = {
            "source": SOURCE,
            "external_request_id": payload.external_request_id,
            "material_type": "copy",
            "product_name": product_name,
            "landing_url": landing_url,
            "audience": payload.audience,
            "country": payload.country,
            "event_name": payload.event_name,
            "customEventType": payload.customEventType,
            "language": payload.language,
            "brief": brief,
            "selling_points": selling_points,
            "constraints": payload.constraints,
        }
        work_order = {
            "source": SOURCE,
            "landing_url": landing_url,
            "country": payload.country,
            "event_name": payload.event_name,
            "parsed_fields": {
                "landing_url": landing_url,
                "country": payload.country,
                "event_name": payload.event_name,
            },
        }

        campaign = Campaign(
            name=product_name,
            product_name=product_name,
            objective=payload.event_name,
            audience_description=payload.audience,
            metadata_json={**external_context, "work_order": work_order},
        )
        session.add(campaign)
        await session.flush()

        topic = ContentTopic(
            campaign_id=campaign.id,
            title=product_name,
            angle=brief or "Create a clear ad for daily use.",
            audience=payload.audience,
            selling_points=selling_points,
            source_data=external_context,
        )
        session.add(topic)
        await session.flush()
        return campaign, topic

    async def _find_existing_copy(
        self,
        session: AsyncSession,
        external_request_id: str | None,
    ) -> CopyDraft | None:
        if not (external_request_id or "").strip():
            return None

        result = await session.execute(select(CopyDraft).order_by(CopyDraft.created_at.desc()))
        for draft in result.scalars().all():
            metadata = draft.metadata_json or {}
            if (
                metadata.get("source") == SOURCE
                and metadata.get("material_type") == "copy"
                and metadata.get("external_request_id") == external_request_id
            ):
                return draft
        return None

    def _copy_response(
        self,
        draft: CopyDraft,
        payload: MaterialCopyGenerateRequest,
    ) -> MaterialGenerationEnvelope:
        metadata = draft.metadata_json or {}
        custom_event_type = metadata.get("customEventType") or metadata.get("custom_event_type")
        return MaterialGenerationEnvelope(
            code=0,
            message="success",
            data={
                "request_id": draft.id,
                "primary_text": draft.primary_text or draft.body,
                "headline": draft.headline,
                "description": draft.description,
                "cta": draft.cta,
                "customEventType": custom_event_type
                or payload.customEventType
                or _custom_event_type(payload.event_name),
            },
        )

    def _raise_if_brand_safety_blocked(self, data: dict[str, Any]) -> None:
        result = scan_brand_safety(data)
        if result["status"] != "blocked":
            return
        raise MaterialGenerationAPIError(
            "brand safety policy blocked generated copy",
            code=MATERIAL_CODE_BRAND_SAFETY_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            data={"brand_safety": result},
        )


def _validate_base_payload(payload: MaterialCopyGenerateRequest) -> None:
    if not (payload.product_name or "").strip():
        raise MaterialGenerationAPIError(
            "product_name is required",
            code=MATERIAL_CODE_VALIDATION_ERROR,
        )
    has_brief = bool((payload.brief or "").strip())
    has_selling_points = any(point.strip() for point in payload.selling_points)
    if not has_brief and not has_selling_points:
        raise MaterialGenerationAPIError(
            "brief or selling_points is required",
            code=MATERIAL_CODE_VALIDATION_ERROR,
        )


def _custom_event_type(event_name: str | None) -> str:
    normalized = (event_name or "").strip().lower().replace("-", "_").replace(" ", "_")
    if any(key in normalized for key in ("complete_registration", "registration", "register")):
        return "COMPLETE_REGISTRATION"
    if any(key in normalized for key in ("purchase", "shop", "order")):
        return "PURCHASE"
    if any(key in normalized for key in ("add_to_cart", "cart")):
        return "ADD_TO_CART"
    if "lead" in normalized:
        return "LEAD"
    return "LINK_CLICK"
