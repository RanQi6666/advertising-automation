import re
from copy import deepcopy
from datetime import UTC
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.core.errors import ProviderError
from backend.app.db.base import utcnow
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.enums import PublishChannel, PublishStatus
from backend.app.db.models.facebook_account import FacebookAccount
from backend.app.db.models.insight import InsightDaily
from backend.app.db.models.publish_job import PublishJob
from backend.app.db.models.topic import ContentTopic
from backend.app.db.models.video_asset import VideoAsset
from backend.app.db.models.work_order import WorkOrder
from backend.app.integrations.facebook.graph_client import FacebookGraphClient
from backend.app.integrations.facebook.token_manager import (
    FACEBOOK_AD_TOKEN_REF,
    FACEBOOK_PAGE_TOKEN_REF,
)
from backend.app.schemas.publishing import (
    AdCreativeDraftRequest,
    AdsPlanDraftRequest,
    MetaAdsActivationRequest,
    MetaAdsDraftCreateRequest,
    MetaAdsPackagePrepareRequest,
    MetaAdsPauseRequest,
    PublishJobCreate,
)
from backend.app.services.creative_asset_urls import resolve_creative_image_url
from backend.app.services.image_storage_service import ImageStorageService
from backend.app.services.utils import get_required


class PublishService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.facebook = FacebookGraphClient(self.settings)
        self.image_storage = ImageStorageService(self.settings)

    def _refresh_settings(self) -> None:
        cached_settings = get_settings()
        if self.settings is not cached_settings:
            return
        get_settings.cache_clear()
        self.settings = get_settings()
        self.facebook = FacebookGraphClient(self.settings)
        self.image_storage = ImageStorageService(self.settings)

    def _ads_dry_run_enabled(self) -> bool:
        return self.facebook.ads_dry_run_enabled()

    async def list_ad_pixels(
        self,
        session: AsyncSession,
        facebook_account_id: str | None = None,
        ad_account_id: str | None = None,
    ) -> list[dict[str, Any]]:
        self._refresh_settings()
        facebook_account = await self._get_facebook_account(session, facebook_account_id)
        selected_ad_account_id = (
            ad_account_id
            or (facebook_account.ad_account_id if facebook_account else None)
            or self.settings.facebook_ad_account_id
        )
        if not selected_ad_account_id:
            raise ProviderError("Ad Account ID is required to fetch Meta Pixels.")

        access_token_ref = (
            facebook_account.access_token_ref if facebook_account else FACEBOOK_AD_TOKEN_REF
        )
        if not access_token_ref:
            raise ProviderError("Facebook ads access token is required to fetch Meta Pixels.")

        response = await self.facebook.list_ad_pixels(
            ad_account_id=selected_ad_account_id,
            access_token_ref=access_token_ref,
        )
        pixels = response.get("data", [])
        if not isinstance(pixels, list):
            return []
        return [
            {
                "id": str(pixel.get("id")),
                "name": pixel.get("name"),
                "last_fired_time": pixel.get("last_fired_time"),
                "metadata_json": {
                    key: value
                    for key, value in pixel.items()
                    if key not in {"id", "name", "last_fired_time"}
                },
            }
            for pixel in pixels
            if isinstance(pixel, dict) and pixel.get("id")
        ]

    async def create_job(self, session: AsyncSession, payload: PublishJobCreate) -> PublishJob:
        self._refresh_settings()
        await get_required(session, Campaign, payload.campaign_id)
        if payload.draft_id:
            await get_required(session, CopyDraft, payload.draft_id)
        data = payload.model_dump()
        data["channel"] = payload.channel.value
        data["payload"] = await self._prepare_payload(session, payload)
        job = PublishJob(**data)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job

    async def list_jobs(
        self,
        session: AsyncSession,
        limit: int,
        offset: int,
        campaign_id: str | None = None,
    ) -> list[PublishJob]:
        query = select(PublishJob)
        if campaign_id:
            query = query.where(PublishJob.campaign_id == campaign_id)
        result = await session.execute(
            query.order_by(PublishJob.created_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def build_ad_creative_draft(
        self,
        session: AsyncSession,
        payload: AdCreativeDraftRequest,
    ) -> dict[str, Any]:
        self._refresh_settings()
        campaign = cast(Campaign, await get_required(session, Campaign, payload.campaign_id))
        work_order = None
        if campaign.work_order_id:
            work_order = cast(
                WorkOrder,
                await get_required(session, WorkOrder, campaign.work_order_id),
            )
        facebook_account = await self._get_facebook_account(
            session,
            payload.facebook_account_id,
        )

        draft = None
        if payload.draft_id:
            draft = cast(CopyDraft, await get_required(session, CopyDraft, payload.draft_id))
            if draft.campaign_id != campaign.id:
                raise ProviderError("Copy draft does not belong to this campaign.")

        topic = None
        topic_id = payload.topic_id or (draft.topic_id if draft else None)
        if topic_id:
            topic = cast(ContentTopic, await get_required(session, ContentTopic, topic_id))
            if topic.campaign_id != campaign.id:
                raise ProviderError("Topic does not belong to this campaign.")

        creative = None
        image_url = None
        if payload.creative_asset_id:
            creative = cast(
                CreativeAsset,
                await get_required(session, CreativeAsset, payload.creative_asset_id),
            )
            if creative.campaign_id != campaign.id:
                raise ProviderError("Creative asset does not belong to this campaign.")
            image_url = resolve_creative_image_url(creative, self.image_storage)

        video_asset = None
        if payload.video_asset_id:
            video_asset = cast(
                VideoAsset,
                await get_required(session, VideoAsset, payload.video_asset_id),
            )
            if video_asset.campaign_id != campaign.id:
                raise ProviderError("Video asset does not belong to this campaign.")
        if not creative and video_asset and video_asset.source_asset_ids:
            creative = cast(
                CreativeAsset,
                await get_required(session, CreativeAsset, str(video_asset.source_asset_ids[0])),
            )
            if creative.campaign_id != campaign.id:
                raise ProviderError("Video thumbnail asset does not belong to this campaign.")
            image_url = resolve_creative_image_url(creative, self.image_storage)

        work_order_context = _build_work_order_context(campaign, work_order)
        destination_url = payload.destination_url or work_order_context.get("landing_url")
        headline = (
            (topic.title if topic else None)
            or (draft.headline if draft else None)
            or campaign.name
        )
        primary_text = (
            (draft.primary_text if draft else None)
            or (draft.body if draft else None)
            or (topic.angle if topic else None)
            or ""
        )
        description = (draft.description if draft else None) or campaign.product_name
        page_id = (
            payload.page_id
            or (facebook_account.page_id if facebook_account else None)
            or self.settings.facebook_page_id
            or "dry-run-page"
        )
        ad_account_id = (
            payload.ad_account_id
            or (facebook_account.ad_account_id if facebook_account else None)
            or self.settings.facebook_ad_account_id
            or "dry-run-ad-account"
        )
        facebook_image_hash = payload.facebook_image_hash
        if not facebook_image_hash and creative:
            facebook_image_hash = self._facebook_image_hash_for_account(creative, ad_account_id)
        facebook_video_id = payload.facebook_video_id
        if not facebook_video_id and video_asset:
            facebook_video_id = (video_asset.metadata_json or {}).get("facebook_video_id")

        media_type = ""
        if facebook_video_id or video_asset:
            media_type = "video"
        elif facebook_image_hash or image_url:
            media_type = "image"
        else:
            raise ProviderError("Meta ad creatives require an image or video material.")

        object_story_spec: dict[str, Any] = {"page_id": page_id}
        if media_type == "video":
            object_story_spec["video_data"] = {
                "video_id": facebook_video_id or "<META_VIDEO_ID_REQUIRED>",
                "message": primary_text,
                "title": headline,
            }
            if facebook_image_hash:
                object_story_spec["video_data"]["image_hash"] = facebook_image_hash
            if destination_url:
                object_story_spec["video_data"]["call_to_action"] = {
                    "type": payload.cta_type,
                    "value": {"link": destination_url},
                }
        elif media_type == "image":
            link_data = {
                "link": destination_url,
                "message": primary_text,
                "name": headline,
                "description": description,
                "call_to_action": {
                    "type": payload.cta_type,
                    "value": {"link": destination_url},
                },
            }
            if facebook_image_hash:
                link_data["image_hash"] = facebook_image_hash
            elif image_url:
                link_data["picture"] = image_url
            object_story_spec["link_data"] = link_data
        else:
            object_story_spec["link_data"] = {
                "link": destination_url,
                "message": primary_text,
                "name": headline,
                "description": description,
                "call_to_action": {
                    "type": payload.cta_type,
                    "value": {"link": destination_url},
                },
            }

        return {
            "campaign_id": campaign.id,
            "facebook_account_id": facebook_account.id if facebook_account else None,
            "draft_id": draft.id if draft else None,
            "topic_id": topic.id if topic else None,
            "destination_url": destination_url,
            "headline": headline,
            "primary_text": primary_text,
            "description": description,
            "media_type": media_type,
            "creative_asset_id": creative.id if creative else payload.creative_asset_id,
            "image_url": image_url,
            "facebook_image_hash": facebook_image_hash,
            "video_asset_id": video_asset.id if video_asset else None,
            "facebook_video_id": facebook_video_id,
            "page_id": page_id,
            "ad_account_id": ad_account_id,
            "cta_type": payload.cta_type,
            "meta_payload": {
                "endpoint": self.facebook.build_url(
                    f"{self.facebook.normalize_ad_account_id(ad_account_id)}/adcreatives"
                ),
                "body": {
                    "name": headline,
                    "object_story_spec": object_story_spec,
                },
            },
            "source_mapping": {
                "facebook_account_id": "request.facebook_account_id",
                "destination_url": "work_order.landing_url",
                "headline": "topic.title",
                "primary_text": "copy_draft.primary_text",
                "description": "copy_draft.description or campaign.product_name",
                "image_hash": "creative_asset uploaded to Meta adimages",
            },
        }

    async def build_ads_plan_draft(
        self,
        session: AsyncSession,
        payload: AdsPlanDraftRequest,
    ) -> dict[str, Any]:
        self._refresh_settings()
        campaign = cast(Campaign, await get_required(session, Campaign, payload.campaign_id))
        work_order = None
        if campaign.work_order_id:
            work_order = cast(
                WorkOrder,
                await get_required(session, WorkOrder, campaign.work_order_id),
            )

        creative_draft = await self.build_ad_creative_draft(
            session,
            AdCreativeDraftRequest(
                campaign_id=payload.campaign_id,
                facebook_account_id=payload.facebook_account_id,
                draft_id=payload.draft_id,
                topic_id=payload.topic_id,
                creative_asset_id=payload.creative_asset_id,
                facebook_image_hash=payload.facebook_image_hash,
                video_asset_id=payload.video_asset_id,
                facebook_video_id=payload.facebook_video_id,
                page_id=payload.page_id,
                ad_account_id=payload.ad_account_id,
                destination_url=payload.destination_url,
                cta_type=payload.cta_type,
            ),
        )
        work_order_context = _build_work_order_context(campaign, work_order)
        parsed_fields = cast(dict[str, Any], work_order_context.get("parsed_fields") or {})
        country = _first_text(work_order_context.get("country"), parsed_fields.get("country"))
        media = _first_text(work_order_context.get("media"), parsed_fields.get("media"))
        audience_description = _first_text(
            campaign.audience_description,
            work_order_context.get("audience_description"),
            parsed_fields.get("audience_description"),
        )
        event_name = _first_text(
            payload.conversion_event,
            work_order_context.get("event_name"),
            parsed_fields.get("event_name"),
            campaign.objective,
        )
        payout_amount = _first_text(parsed_fields.get("payout_amount"))
        targeting = _build_targeting(
            country,
            audience_description,
            work_order_context.get("raw_content"),
        )
        requested_event_mapping = _map_event_to_meta(event_name)
        pixel_id = _first_text(payload.pixel_id)
        event_mapping = _event_mapping_for_available_inputs(requested_event_mapping, pixel_id)
        status = (payload.status or "PAUSED").upper()
        if status not in {"PAUSED", "ACTIVE", "ARCHIVED", "DELETED"}:
            status = "PAUSED"

        warnings: list[str] = []
        if not creative_draft.get("destination_url"):
            warnings.append("工单或请求中没有落地页链接，真实创建广告前需要补充 destination_url。")
        if not payload.daily_budget:
            warnings.append("未设置 daily_budget；真实创建 Ad Set 前需要人工填写每日预算。")
        if payout_amount:
            warnings.append("工单里的打款金额只作为业务备注，没有自动映射为 Meta daily_budget。")
        if (
            requested_event_mapping["optimization_goal"] == "OFFSITE_CONVERSIONS"
            and not pixel_id
        ):
            warnings.append(
                "未填写 Pixel ID，已自动按流量/链接点击目标准备投放；"
                "后续接 Pixel 后可升级为购买转化。"
            )
        if creative_draft.get("page_id") == "dry-run-page":
            warnings.append("当前没有 Page ID，真实创建 Ad Creative 前需要配置 FACEBOOK_PAGE_ID。")
        if not payload.ad_creative_id:
            warnings.append("Ad payload 暂用创意 ID 占位符；需要先创建 Ad Creative 后再创建 Ad。")
        if status != "PAUSED":
            warnings.append("建议首次真实创建时使用 PAUSED，检查无误后再手动启用。")

        campaign_name = payload.campaign_name or campaign.name
        ad_account_id = str(creative_draft.get("ad_account_id") or "dry-run-ad-account")
        normalized_account_id = self.facebook.normalize_ad_account_id(ad_account_id)
        headline = str(creative_draft.get("headline") or campaign_name)
        media_type = str(creative_draft.get("media_type") or "")
        if media_type not in {"image", "video"}:
            raise ProviderError("Meta ads plan requires an image or video material.")
        facebook_image_hash = _first_text(creative_draft.get("facebook_image_hash"))
        if media_type == "image" and creative_draft.get("image_url") and not facebook_image_hash:
            warnings.append(
                "Image is not uploaded to Meta yet; preparing or creating Meta ads will "
                "upload it first and use image_hash instead of a local picture URL."
            )
        adset_name = payload.adset_name or _compact_name(
            campaign_name,
            targeting["summary"].get("country_code"),
            targeting["summary"].get("gender_label"),
            targeting["summary"].get("age_label"),
        )
        ad_name = payload.ad_name or _compact_name(headline, media_type)
        campaign_payload = {
            "name": campaign_name,
            "objective": event_mapping["campaign_objective"],
            "status": status,
            "special_ad_categories": [],
            "is_adset_budget_sharing_enabled": False,
        }
        adset_payload: dict[str, Any] = {
            "name": adset_name,
            "campaign_id": "<CAMPAIGN_ID_AFTER_CREATION>",
            "billing_event": "IMPRESSIONS",
            "optimization_goal": event_mapping["optimization_goal"],
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
            "destination_type": "WEBSITE",
            "targeting": targeting["payload"],
            "status": status,
        }
        if event_mapping["optimization_goal"] == "OFFSITE_CONVERSIONS" and pixel_id:
            adset_payload["promoted_object"] = {
                "pixel_id": pixel_id,
                "custom_event_type": event_mapping["custom_event_type"],
            }
        if payload.daily_budget:
            adset_payload["daily_budget"] = payload.daily_budget
        else:
            adset_payload["daily_budget"] = "<DAILY_BUDGET_REQUIRED>"

        creative_payload = cast(dict[str, Any], creative_draft["meta_payload"]["body"])
        ad_payload = {
            "name": ad_name,
            "adset_id": "<AD_SET_ID_AFTER_CREATION>",
            "creative": {
                "creative_id": payload.ad_creative_id or "<AD_CREATIVE_ID_AFTER_CREATION>",
            },
            "status": status,
        }
        targeting_summary = {
            **targeting["summary"],
            "event_name": event_name,
            "mapped_objective": event_mapping["campaign_objective"],
            "optimization_goal": event_mapping["optimization_goal"],
            "conversion_event_type": event_mapping["custom_event_type"],
            "media": media,
            "destination_url": creative_draft.get("destination_url"),
        }
        meta_payload = {
            "dry_run": self._ads_dry_run_enabled(),
            "campaign": {
                "method": "POST",
                "endpoint": self.facebook.build_url(f"{normalized_account_id}/campaigns"),
                "body": campaign_payload,
            },
            "adset": {
                "method": "POST",
                "endpoint": self.facebook.build_url(f"{normalized_account_id}/adsets"),
                "body": adset_payload,
            },
            "creative": creative_draft["meta_payload"],
            "ad": {
                "method": "POST",
                "endpoint": self.facebook.build_url(f"{normalized_account_id}/ads"),
                "body": ad_payload,
            },
        }

        return {
            "campaign_id": campaign.id,
            "facebook_account_id": creative_draft.get("facebook_account_id"),
            "draft_id": creative_draft.get("draft_id"),
            "topic_id": creative_draft.get("topic_id"),
            "destination_url": creative_draft.get("destination_url"),
            "headline": headline,
            "primary_text": creative_draft.get("primary_text") or "",
            "media_type": media_type,
            "creative_asset_id": creative_draft.get("creative_asset_id"),
            "facebook_image_hash": facebook_image_hash,
            "page_id": creative_draft.get("page_id"),
            "ad_account_id": ad_account_id,
            "ad_creative_id": payload.ad_creative_id,
            "campaign_payload": campaign_payload,
            "adset_payload": adset_payload,
            "creative_payload": creative_payload,
            "ad_payload": ad_payload,
            "meta_payload": meta_payload,
            "targeting_summary": targeting_summary,
            "source_mapping": {
                "destination_url": "work_order.landing_url",
                "headline": "topic.title",
                "primary_text": "copy_draft.primary_text",
                "campaign.name": "campaign.name or request.campaign_name",
                "campaign.objective": "work_order.event_name mapped to Meta objective",
                "adset.targeting.geo_locations": "work_order.country",
                "adset.targeting.age_min/age_max": "work_order.audience_description",
                "adset.targeting.genders": "work_order.audience_description",
                "adset.daily_budget": "request.daily_budget only",
            },
            "warnings": warnings,
        }

    async def create_meta_ads_draft(
        self,
        session: AsyncSession,
        payload: MetaAdsDraftCreateRequest,
    ) -> dict[str, Any]:
        self._refresh_settings()
        if not payload.confirm_create_paused:
            raise ProviderError("Creating Meta ads requires explicit PAUSED confirmation.")

        if (
            not payload.facebook_account_id
            and not self.settings.facebook_ad_account_id
            and not payload.ad_account_id
        ):
            raise ProviderError("FACEBOOK_AD_ACCOUNT_ID is required.")
        if not payload.facebook_account_id and not self.settings.facebook_ad_access_token:
            raise ProviderError("FACEBOOK_AD_ACCESS_TOKEN is required.")

        facebook_image_hash = await self._ensure_facebook_image_hash(session, payload)
        facebook_video_id = await self._ensure_facebook_video_id(session, payload)
        plan_request = AdsPlanDraftRequest(
            campaign_id=payload.campaign_id,
            facebook_account_id=payload.facebook_account_id,
            draft_id=payload.draft_id,
            topic_id=payload.topic_id,
            creative_asset_id=payload.creative_asset_id,
            facebook_image_hash=facebook_image_hash or payload.facebook_image_hash,
            video_asset_id=payload.video_asset_id,
            facebook_video_id=facebook_video_id or payload.facebook_video_id,
            page_id=payload.page_id,
            ad_account_id=payload.ad_account_id,
            destination_url=payload.destination_url,
            cta_type=payload.cta_type,
            daily_budget=payload.daily_budget,
            pixel_id=payload.pixel_id,
            conversion_event=payload.conversion_event,
            campaign_name=payload.campaign_name,
            adset_name=payload.adset_name,
            ad_name=payload.ad_name,
            status="PAUSED",
        )
        plan = await self.build_ads_plan_draft(session, plan_request)
        critical_errors = _meta_ads_plan_errors(plan)
        if critical_errors:
            raise ProviderError(" ".join(critical_errors))

        job = PublishJob(
            campaign_id=payload.campaign_id,
            draft_id=payload.draft_id,
            channel=PublishChannel.FACEBOOK_AD.value,
            payload={
                "ad_operation": "create_meta_ads_draft",
                "status": "PAUSED",
                "dry_run": self._ads_dry_run_enabled(),
                "plan": plan["meta_payload"],
                "warnings": plan["warnings"],
            },
            status=PublishStatus.PUBLISHING.value,
        )
        session.add(job)
        await session.flush()

        responses: dict[str, Any] = {}
        ids: dict[str, str | None] = {
            "campaign_id": None,
            "adset_id": None,
            "ad_creative_id": None,
            "ad_id": None,
        }
        try:
            ad_account_id = str(plan["ad_account_id"])
            access_token_ref = await self._ads_access_token_ref_for_plan(session, plan)

            campaign_payload = deepcopy(plan["campaign_payload"])
            campaign_response = await self.facebook.create_ad_campaign(
                ad_account_id=ad_account_id,
                payload=campaign_payload,
                access_token_ref=access_token_ref,
            )
            responses["campaign"] = campaign_response
            ids["campaign_id"] = campaign_response.get("id")

            adset_payload = deepcopy(plan["adset_payload"])
            adset_payload["campaign_id"] = ids["campaign_id"]
            adset_response = await self.facebook.create_adset(
                ad_account_id=ad_account_id,
                payload=adset_payload,
                access_token_ref=access_token_ref,
            )
            responses["adset"] = adset_response
            ids["adset_id"] = adset_response.get("id")

            creative_payload = deepcopy(plan["creative_payload"])
            creative_response = await self.facebook.create_ad_creative(
                ad_account_id=ad_account_id,
                payload=creative_payload,
                access_token_ref=access_token_ref,
            )
            responses["creative"] = creative_response
            ids["ad_creative_id"] = creative_response.get("id")

            ad_payload = deepcopy(plan["ad_payload"])
            ad_payload["adset_id"] = ids["adset_id"]
            ad_payload["creative"] = {"creative_id": ids["ad_creative_id"]}
            ad_response = await self.facebook.create_ad(
                ad_account_id=ad_account_id,
                payload=ad_payload,
                access_token_ref=access_token_ref,
            )
            responses["ad"] = ad_response
            ids["ad_id"] = ad_response.get("id")

            job.external_id = ids["ad_id"] or ids["ad_creative_id"] or ids["adset_id"]
            job.status = PublishStatus.PUBLISHED.value
            job.published_at = utcnow().astimezone(UTC)
        except ProviderError as exc:
            job.status = PublishStatus.FAILED.value
            job.error_message = str(exc)
        finally:
            job.metadata_json = {
                **(job.metadata_json or {}),
                "meta_ads_ids": ids,
                "provider_response": responses,
                "plan": plan,
            }
            await session.commit()
            await session.refresh(job)

        return {
            "job_id": job.id,
            "status": job.status,
            "dry_run": self._ads_dry_run_enabled(),
            "campaign_id": payload.campaign_id,
            "draft_id": payload.draft_id,
            "meta_campaign_id": ids["campaign_id"],
            "meta_adset_id": ids["adset_id"],
            "meta_ad_creative_id": ids["ad_creative_id"],
            "meta_ad_id": ids["ad_id"],
            "error_message": job.error_message,
            "ids": ids,
            "responses": responses,
            "plan": plan,
            "warnings": plan["warnings"],
        }

    async def prepare_meta_ads_package(
        self,
        session: AsyncSession,
        payload: MetaAdsPackagePrepareRequest,
    ) -> PublishJob:
        self._refresh_settings()
        if not payload.confirm_prepare:
            raise ProviderError("Preparing a Meta ads package requires explicit confirmation.")

        facebook_image_hash = await self._ensure_facebook_image_hash(session, payload)
        facebook_video_id = await self._ensure_facebook_video_id(session, payload)
        plan_request = AdsPlanDraftRequest(
            campaign_id=payload.campaign_id,
            facebook_account_id=payload.facebook_account_id,
            draft_id=payload.draft_id,
            topic_id=payload.topic_id,
            creative_asset_id=payload.creative_asset_id,
            facebook_image_hash=facebook_image_hash or payload.facebook_image_hash,
            video_asset_id=payload.video_asset_id,
            facebook_video_id=facebook_video_id or payload.facebook_video_id,
            page_id=payload.page_id,
            ad_account_id=payload.ad_account_id,
            destination_url=payload.destination_url,
            cta_type=payload.cta_type,
            daily_budget=payload.daily_budget,
            pixel_id=payload.pixel_id,
            conversion_event=payload.conversion_event,
            campaign_name=payload.campaign_name,
            adset_name=payload.adset_name,
            ad_name=payload.ad_name,
            status="PAUSED",
        )
        plan = await self.build_ads_plan_draft(session, plan_request)
        critical_errors = _meta_ads_plan_errors(plan)
        if critical_errors:
            raise ProviderError(" ".join(critical_errors))

        job = PublishJob(
            campaign_id=payload.campaign_id,
            draft_id=payload.draft_id,
            channel=PublishChannel.FACEBOOK_AD.value,
            payload={
                "ad_operation": "create_meta_ads_draft",
                "media_type": plan["media_type"],
                "status": "PAUSED",
                "dry_run": self._ads_dry_run_enabled(),
                "page_id": plan["page_id"],
                "ad_account_id": plan["ad_account_id"],
                "facebook_account_id": plan.get("facebook_account_id"),
                "plan": plan,
            },
            status=PublishStatus.QUEUED.value,
            metadata_json={
                "review_required": True,
                "review_status": "pending",
                "preflight_warnings": plan["warnings"],
                "publish_mode": "meta_ads_package",
            },
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job

    def get_meta_config(self) -> dict[str, Any]:
        self._refresh_settings()
        missing: list[str] = []
        if not self.settings.facebook_app_id:
            missing.append("FACEBOOK_APP_ID")
        if not self.settings.facebook_app_secret:
            missing.append("FACEBOOK_APP_SECRET")
        if not self.settings.facebook_page_id:
            missing.append("FACEBOOK_PAGE_ID")
        if not self.settings.facebook_page_access_token:
            missing.append("FACEBOOK_PAGE_ACCESS_TOKEN")
        if not self.settings.facebook_ad_account_id:
            missing.append("FACEBOOK_AD_ACCOUNT_ID")
        if not self.settings.facebook_ad_access_token:
            missing.append("FACEBOOK_AD_ACCESS_TOKEN")

        return {
            "dry_run": self.settings.facebook_dry_run,
            "graph_api_version": self.settings.facebook_graph_api_version,
            "app": {
                "app_id": self.settings.facebook_app_id,
                "app_id_configured": bool(self.settings.facebook_app_id),
                "app_secret_configured": bool(self.settings.facebook_app_secret),
            },
            "page": {
                "id": self.settings.facebook_page_id,
                "id_configured": bool(self.settings.facebook_page_id),
                "access_token_configured": bool(self.settings.facebook_page_access_token),
                "access_token_ref": FACEBOOK_PAGE_TOKEN_REF,
            },
            "ads": {
                "ad_account_id": self.settings.facebook_ad_account_id,
                "ad_account_configured": bool(self.settings.facebook_ad_account_id),
                "access_token_configured": bool(self.settings.facebook_ad_access_token),
                "access_token_ref": FACEBOOK_AD_TOKEN_REF,
                "dry_run": self._ads_dry_run_enabled(),
            },
            "missing_fields": missing,
        }

    async def publish_job(self, session: AsyncSession, job_id: str) -> PublishJob:
        self._refresh_settings()
        job = cast(PublishJob, await get_required(session, PublishJob, job_id))
        job.status = PublishStatus.PUBLISHING.value
        await session.flush()

        try:
            response = await self._publish(session, job)
            job.external_id = response.get("id", str(uuid4()))
            job.metadata_json = {**job.metadata_json, "provider_response": response}
            job.status = PublishStatus.PUBLISHED.value
            job.error_message = None
            job.published_at = utcnow().astimezone(UTC)
        except ProviderError as exc:
            job.status = PublishStatus.FAILED.value
            job.error_message = str(exc)

        await session.commit()
        await session.refresh(job)
        return job

    async def activate_meta_ads_job(
        self,
        session: AsyncSession,
        job_id: str,
        payload: MetaAdsActivationRequest,
    ) -> PublishJob:
        self._refresh_settings()
        if not payload.confirm_activate or payload.confirmation_text != "ACTIVE":
            raise ProviderError("Activating Meta ads requires typing ACTIVE to confirm.")

        job = cast(PublishJob, await get_required(session, PublishJob, job_id))
        if job.channel != PublishChannel.FACEBOOK_AD.value:
            raise ProviderError("Only Facebook Ads publish jobs can be activated.")
        if not is_meta_ads_package_payload(job.payload):
            raise ProviderError("Only Meta ads package jobs can be activated.")
        if job.status != PublishStatus.PUBLISHED.value:
            raise ProviderError("Only successfully published Meta ads jobs can be activated.")

        ids = _meta_ads_ids_from_job(job)
        missing_ids = [
            label
            for label in ("campaign_id", "adset_id", "ad_id")
            if not ids.get(label)
        ]
        if missing_ids:
            raise ProviderError(
                "Meta ads activation requires created Meta IDs: " + ", ".join(missing_ids)
            )

        activation_metadata = job.metadata_json.get("activation")
        if isinstance(activation_metadata, dict) and activation_metadata.get("status") == "active":
            return job

        plan = _meta_ads_plan_from_job(job)
        access_token_ref = await self._ads_access_token_ref_for_plan(session, plan)

        responses: dict[str, Any] = {}
        try:
            responses["campaign"] = await self.facebook.update_ad_object_status(
                object_id=str(ids["campaign_id"]),
                status="ACTIVE",
                access_token_ref=access_token_ref,
            )
            responses["adset"] = await self.facebook.update_ad_object_status(
                object_id=str(ids["adset_id"]),
                status="ACTIVE",
                access_token_ref=access_token_ref,
            )
            responses["ad"] = await self.facebook.update_ad_object_status(
                object_id=str(ids["ad_id"]),
                status="ACTIVE",
                access_token_ref=access_token_ref,
            )
        except ProviderError as exc:
            job.metadata_json = {
                **(job.metadata_json or {}),
                "activation": {
                    "status": "failed",
                    "error_message": str(exc),
                    "attempted_at": utcnow().astimezone(UTC).isoformat(),
                    "responses": responses,
                    "meta_ads_ids": ids,
                },
            }
            job.error_message = str(exc)
            await session.commit()
            await session.refresh(job)
            raise

        job.metadata_json = {
            **(job.metadata_json or {}),
            "activation": {
                "status": "active",
                "activated_at": utcnow().astimezone(UTC).isoformat(),
                "responses": responses,
                "meta_ads_ids": ids,
            },
            "delivery_status": "active",
        }
        job.error_message = None
        await session.commit()
        await session.refresh(job)
        return job

    async def pause_meta_ads_job(
        self,
        session: AsyncSession,
        job_id: str,
        payload: MetaAdsPauseRequest,
    ) -> PublishJob:
        self._refresh_settings()
        if not payload.confirm_pause or payload.confirmation_text != "PAUSE":
            raise ProviderError("Pausing Meta ads requires typing PAUSE to confirm.")

        job = cast(PublishJob, await get_required(session, PublishJob, job_id))
        if job.channel != PublishChannel.FACEBOOK_AD.value:
            raise ProviderError("Only Facebook Ads publish jobs can be paused.")
        if not is_meta_ads_package_payload(job.payload):
            raise ProviderError("Only Meta ads package jobs can be paused.")
        if job.status != PublishStatus.PUBLISHED.value:
            raise ProviderError("Only successfully published Meta ads jobs can be paused.")

        ids = _meta_ads_ids_from_job(job)
        missing_ids = [
            label
            for label in ("campaign_id", "adset_id", "ad_id")
            if not ids.get(label)
        ]
        if missing_ids:
            raise ProviderError(
                "Meta ads pause requires created Meta IDs: " + ", ".join(missing_ids)
            )

        plan = _meta_ads_plan_from_job(job)
        access_token_ref = await self._ads_access_token_ref_for_plan(session, plan)

        responses: dict[str, Any] = {}
        try:
            responses["ad"] = await self.facebook.update_ad_object_status(
                object_id=str(ids["ad_id"]),
                status="PAUSED",
                access_token_ref=access_token_ref,
            )
            responses["adset"] = await self.facebook.update_ad_object_status(
                object_id=str(ids["adset_id"]),
                status="PAUSED",
                access_token_ref=access_token_ref,
            )
            responses["campaign"] = await self.facebook.update_ad_object_status(
                object_id=str(ids["campaign_id"]),
                status="PAUSED",
                access_token_ref=access_token_ref,
            )
        except ProviderError as exc:
            job.metadata_json = {
                **(job.metadata_json or {}),
                "pause": {
                    "status": "failed",
                    "error_message": str(exc),
                    "attempted_at": utcnow().astimezone(UTC).isoformat(),
                    "responses": responses,
                    "meta_ads_ids": ids,
                },
            }
            job.error_message = str(exc)
            await session.commit()
            await session.refresh(job)
            raise

        paused_at = utcnow().astimezone(UTC).isoformat()
        job.metadata_json = {
            **(job.metadata_json or {}),
            "pause": {
                "status": "paused",
                "paused_at": paused_at,
                "responses": responses,
                "meta_ads_ids": ids,
            },
            "activation": {
                "status": "paused",
                "paused_at": paused_at,
                "responses": responses,
                "meta_ads_ids": ids,
            },
            "meta_review_status": "paused",
            "delivery_status": "paused",
        }
        job.error_message = None
        await session.commit()
        await session.refresh(job)
        return job

    async def sync_meta_ads_status_job(
        self,
        session: AsyncSession,
        job_id: str,
    ) -> PublishJob:
        self._refresh_settings()
        job = cast(PublishJob, await get_required(session, PublishJob, job_id))
        if job.channel != PublishChannel.FACEBOOK_AD.value:
            raise ProviderError("Only Facebook Ads publish jobs can be synced.")
        if not is_meta_ads_package_payload(job.payload):
            raise ProviderError("Only Meta ads package jobs can be synced.")

        ids = _meta_ads_ids_from_job(job)
        missing_ids = [
            label
            for label in ("campaign_id", "adset_id", "ad_id")
            if not ids.get(label)
        ]
        if missing_ids:
            raise ProviderError(
                "Meta ads status sync requires created Meta IDs: " + ", ".join(missing_ids)
            )

        plan = _meta_ads_plan_from_job(job)
        access_token_ref = await self._ads_access_token_ref_for_plan(session, plan)
        fields = ["id", "name", "status", "effective_status", "configured_status", "updated_time"]
        objects = {
            "campaign": await self.facebook.get_ad_object_status(
                object_id=str(ids["campaign_id"]),
                access_token_ref=access_token_ref,
                fields=fields,
            ),
            "adset": await self.facebook.get_ad_object_status(
                object_id=str(ids["adset_id"]),
                access_token_ref=access_token_ref,
                fields=fields,
            ),
            "ad": await self.facebook.get_ad_object_status(
                object_id=str(ids["ad_id"]),
                access_token_ref=access_token_ref,
                fields=fields,
            ),
        }
        summary = summarize_meta_ads_status(objects)
        synced_at = utcnow().astimezone(UTC).isoformat()
        job.metadata_json = {
            **(job.metadata_json or {}),
            "meta_status": {
                "synced_at": synced_at,
                "summary": summary,
                "objects": objects,
                "meta_ads_ids": ids,
            },
            "meta_review_status": summary["review_status"],
            "delivery_status": summary["delivery_status"],
        }
        await session.commit()
        await session.refresh(job)
        return job

    async def sync_meta_ads_insights_job(
        self,
        session: AsyncSession,
        job_id: str,
        date_preset: str = "today",
    ) -> PublishJob:
        self._refresh_settings()
        job = cast(PublishJob, await get_required(session, PublishJob, job_id))
        if job.channel != PublishChannel.FACEBOOK_AD.value:
            raise ProviderError("Only Facebook Ads publish jobs can sync insights.")
        if not is_meta_ads_package_payload(job.payload):
            raise ProviderError("Only Meta ads package jobs can sync insights.")

        ids = _meta_ads_ids_from_job(job)
        missing_ids = [
            label
            for label in ("campaign_id", "adset_id", "ad_id")
            if not ids.get(label)
        ]
        if missing_ids:
            raise ProviderError(
                "Meta ads insights sync requires created Meta IDs: " + ", ".join(missing_ids)
            )

        plan = _meta_ads_plan_from_job(job)
        access_token_ref = await self._ads_access_token_ref_for_plan(session, plan)
        objects = {
            "campaign": await self.facebook.get_ad_object_status(
                object_id=str(ids["campaign_id"]),
                access_token_ref=access_token_ref,
                fields=[
                    "id",
                    "name",
                    "status",
                    "effective_status",
                    "configured_status",
                    "updated_time",
                    "bid_strategy",
                ],
            ),
            "adset": await self.facebook.get_ad_object_status(
                object_id=str(ids["adset_id"]),
                access_token_ref=access_token_ref,
                fields=[
                    "id",
                    "name",
                    "status",
                    "effective_status",
                    "configured_status",
                    "updated_time",
                    "daily_budget",
                    "lifetime_budget",
                    "bid_strategy",
                    "billing_event",
                    "optimization_goal",
                    "start_time",
                    "end_time",
                    "attribution_spec",
                ],
            ),
            "ad": await self.facebook.get_ad_object_status(
                object_id=str(ids["ad_id"]),
                access_token_ref=access_token_ref,
                fields=[
                    "id",
                    "name",
                    "status",
                    "effective_status",
                    "configured_status",
                    "updated_time",
                ],
            ),
        }
        insights = await self.facebook.get_ad_insights(
            ad_id=str(ids["ad_id"]),
            access_token_ref=access_token_ref,
            date_preset=date_preset,
        )
        insight_row = _first_insight_row(insights)
        status_summary = summarize_meta_ads_status(objects)
        insight_summary = summarize_meta_ads_insights(
            insight_row=insight_row,
            objects=objects,
            plan=plan,
        )
        synced_at = utcnow().astimezone(UTC)

        daily_insight = InsightDaily(
            campaign_id=job.campaign_id,
            publish_job_id=job.id,
            metric_date=synced_at.date(),
            impressions=_int_value(insight_summary.get("impressions")),
            clicks=_int_value(insight_summary.get("clicks")),
            spend=_decimal_value(insight_summary.get("spend")),
            ctr=_decimal_value(insight_summary.get("ctr")),
            cpc=_decimal_value(insight_summary.get("cpc")),
            conversions=_int_value(insight_summary.get("conversions")),
            metadata_json={
                "source": "meta_ads_insights",
                "date_preset": date_preset,
                "synced_at": synced_at.isoformat(),
                "summary": insight_summary,
                "raw_insight": insight_row,
                "objects": objects,
                "meta_ads_ids": ids,
            },
        )
        session.add(daily_insight)
        await session.flush()

        job.metadata_json = {
            **(job.metadata_json or {}),
            "meta_insights": {
                "synced_at": synced_at.isoformat(),
                "date_preset": date_preset,
                "summary": insight_summary,
                "raw": insights,
                "objects": objects,
                "meta_ads_ids": ids,
                "insight_daily_id": daily_insight.id,
            },
            "meta_status": {
                "synced_at": synced_at.isoformat(),
                "summary": status_summary,
                "objects": objects,
                "meta_ads_ids": ids,
            },
            "meta_review_status": status_summary["review_status"],
            "delivery_status": status_summary["delivery_status"],
        }
        await session.commit()
        await session.refresh(job)
        return job

    async def _execute_meta_ads_plan(
        self,
        session: AsyncSession,
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        critical_errors = _meta_ads_plan_errors(plan)
        if critical_errors:
            raise ProviderError(" ".join(critical_errors))

        responses: dict[str, Any] = {}
        ids: dict[str, str | None] = {
            "campaign_id": None,
            "adset_id": None,
            "ad_creative_id": None,
            "ad_id": None,
        }
        ad_account_id = str(plan["ad_account_id"])
        access_token_ref = await self._ads_access_token_ref_for_plan(session, plan)

        campaign_payload = deepcopy(plan["campaign_payload"])
        campaign_response = await self.facebook.create_ad_campaign(
            ad_account_id=ad_account_id,
            payload=campaign_payload,
            access_token_ref=access_token_ref,
        )
        responses["campaign"] = campaign_response
        ids["campaign_id"] = campaign_response.get("id")

        adset_payload = deepcopy(plan["adset_payload"])
        adset_payload["campaign_id"] = ids["campaign_id"]
        adset_response = await self.facebook.create_adset(
            ad_account_id=ad_account_id,
            payload=adset_payload,
            access_token_ref=access_token_ref,
        )
        responses["adset"] = adset_response
        ids["adset_id"] = adset_response.get("id")

        creative_payload = deepcopy(plan["creative_payload"])
        creative_response = await self.facebook.create_ad_creative(
            ad_account_id=ad_account_id,
            payload=creative_payload,
            access_token_ref=access_token_ref,
        )
        responses["creative"] = creative_response
        ids["ad_creative_id"] = creative_response.get("id")

        ad_payload = deepcopy(plan["ad_payload"])
        ad_payload["adset_id"] = ids["adset_id"]
        ad_payload["creative"] = {"creative_id": ids["ad_creative_id"]}
        ad_response = await self.facebook.create_ad(
            ad_account_id=ad_account_id,
            payload=ad_payload,
            access_token_ref=access_token_ref,
        )
        responses["ad"] = ad_response
        ids["ad_id"] = ad_response.get("id")

        return {
            "id": ids["ad_id"] or ids["ad_creative_id"] or ids["adset_id"] or ids["campaign_id"],
            "dry_run": self._ads_dry_run_enabled(),
            "meta_ads_ids": ids,
            "provider_response": responses,
            "plan": plan,
        }

    async def _get_facebook_account(
        self,
        session: AsyncSession,
        facebook_account_id: str | None,
    ) -> FacebookAccount | None:
        if not facebook_account_id:
            return None
        account = cast(
            FacebookAccount,
            await get_required(session, FacebookAccount, facebook_account_id),
        )
        if account.status != "active":
            raise ProviderError("Selected Meta account is not active.")
        return account

    async def _ads_access_token_ref_for_plan(
        self,
        session: AsyncSession,
        plan: dict[str, Any],
    ) -> str:
        facebook_account_id = plan.get("facebook_account_id")
        if not facebook_account_id:
            return FACEBOOK_AD_TOKEN_REF
        account = await self._get_facebook_account(session, str(facebook_account_id))
        if not account or not account.access_token_ref:
            raise ProviderError("Selected Meta account has no ads access token.")
        return account.access_token_ref

    async def _ensure_facebook_image_hash(
        self,
        session: AsyncSession,
        payload: MetaAdsDraftCreateRequest | MetaAdsPackagePrepareRequest,
    ) -> str | None:
        if payload.facebook_image_hash:
            return payload.facebook_image_hash
        creative_asset_id = payload.creative_asset_id
        if not creative_asset_id and payload.video_asset_id:
            video = cast(
                VideoAsset,
                await get_required(session, VideoAsset, payload.video_asset_id),
            )
            if video.campaign_id != payload.campaign_id:
                raise ProviderError("Video asset does not belong to this campaign.")
            if video.source_asset_ids:
                creative_asset_id = str(video.source_asset_ids[0])
        if not creative_asset_id:
            return None

        asset = cast(
            CreativeAsset,
            await get_required(session, CreativeAsset, creative_asset_id),
        )
        if asset.campaign_id != payload.campaign_id:
            raise ProviderError("Creative asset does not belong to this campaign.")

        facebook_account = await self._get_facebook_account(
            session,
            payload.facebook_account_id,
        )
        ad_account_id = (
            payload.ad_account_id
            or (facebook_account.ad_account_id if facebook_account else None)
            or self.settings.facebook_ad_account_id
        )
        if not ad_account_id:
            raise ProviderError("Meta image upload requires an Ad Account ID.")

        normalized_account_id = self.facebook.normalize_ad_account_id(ad_account_id)
        existing_hash = self._facebook_image_hash_for_account(asset, normalized_account_id)
        if existing_hash:
            return existing_hash

        access_token_ref = (
            facebook_account.access_token_ref if facebook_account else FACEBOOK_AD_TOKEN_REF
        )
        if not access_token_ref:
            raise ProviderError("Meta image upload requires an ads access token.")

        local_image_path = await self._local_image_path_for_asset(asset)
        upload_response = await self.facebook.create_ad_image(
            ad_account_id=ad_account_id,
            image_file_path=local_image_path,
            access_token_ref=access_token_ref,
        )
        image_hash = _extract_facebook_image_hash(upload_response, local_image_path.name)
        if not image_hash:
            raise ProviderError("Meta image upload returned no image hash.")

        existing_metadata = asset.metadata_json or {}
        existing_hashes = existing_metadata.get("facebook_image_hashes", {})
        if not isinstance(existing_hashes, dict):
            existing_hashes = {}
        asset.metadata_json = {
            **existing_metadata,
            "facebook_image_hash": image_hash,
            "facebook_image_ad_account_id": normalized_account_id,
            "facebook_image_hashes": {
                **existing_hashes,
                normalized_account_id: image_hash,
            },
            "facebook_image_upload_response": upload_response,
        }
        await session.flush()
        return image_hash

    async def _local_image_path_for_asset(self, asset: CreativeAsset) -> Path:
        storage_key = asset.storage_key or self.image_storage.storage_key_for_public_url(asset.url)
        local_image_path = self._local_image_path(storage_key)
        if local_image_path and local_image_path.exists():
            if storage_key and asset.storage_key != storage_key:
                asset.storage_key = storage_key
                asset.url = self.image_storage.public_url_for_storage_key(storage_key) or asset.url
            return local_image_path

        if asset.url and not _is_local_url(asset.url):
            stored_url, stored_key = await self.image_storage.transfer_provider_image(
                source_url=asset.url,
                campaign_id=asset.campaign_id,
                image_id=asset.id,
            )
            asset.url = stored_url
            asset.storage_key = stored_key
            local_image_path = self._local_image_path(stored_key)
            if local_image_path and local_image_path.exists():
                return local_image_path

        if local_image_path and not local_image_path.exists():
            raise ProviderError(
                "Meta image upload requires the generated local image file, but the file "
                f"does not exist for storage key: {storage_key}. Regenerate the image "
                "before creating the ad."
            )
        raise ProviderError(
            "Meta image upload requires a locally stored image file or an existing "
            "Meta image hash. Regenerate the image before creating the ad."
        )

    def _facebook_image_hash_for_account(
        self,
        asset: CreativeAsset,
        ad_account_id: str | None,
    ) -> str | None:
        if not ad_account_id:
            return None
        normalized_account_id = self.facebook.normalize_ad_account_id(ad_account_id)
        metadata = asset.metadata_json or {}
        hashes = metadata.get("facebook_image_hashes")
        if isinstance(hashes, dict):
            value = hashes.get(normalized_account_id)
            if isinstance(value, str) and value.strip():
                return value

        legacy_hash = metadata.get("facebook_image_hash")
        legacy_account_id = metadata.get("facebook_image_ad_account_id")
        if (
            isinstance(legacy_hash, str)
            and legacy_hash.strip()
            and isinstance(legacy_account_id, str)
            and self.facebook.normalize_ad_account_id(legacy_account_id) == normalized_account_id
        ):
            return legacy_hash
        return None

    async def _ensure_facebook_video_id(
        self,
        session: AsyncSession,
        payload: MetaAdsDraftCreateRequest | MetaAdsPackagePrepareRequest,
    ) -> str | None:
        if payload.facebook_video_id:
            return payload.facebook_video_id
        if not payload.video_asset_id:
            return None

        video = cast(VideoAsset, await get_required(session, VideoAsset, payload.video_asset_id))
        existing_video_id = (video.metadata_json or {}).get("facebook_video_id")
        if existing_video_id:
            return str(existing_video_id)

        facebook_account = await self._get_facebook_account(
            session,
            payload.facebook_account_id,
        )
        ad_account_id = (
            payload.ad_account_id
            or (facebook_account.ad_account_id if facebook_account else None)
            or self.settings.facebook_ad_account_id
        )
        if not ad_account_id:
            raise ProviderError("Meta video upload requires an Ad Account ID.")

        access_token_ref = (
            facebook_account.access_token_ref if facebook_account else FACEBOOK_AD_TOKEN_REF
        )
        if not access_token_ref:
            raise ProviderError("Meta video upload requires an ads access token.")

        local_video_path = self._local_video_path(video.storage_key)
        if local_video_path and not local_video_path.exists():
            raise ProviderError(
                "Meta video upload requires the generated local video file, but the file "
                f"does not exist for storage key: {video.storage_key}. Refresh the video "
                "status or regenerate the video before creating the ad."
            )
        if not local_video_path:
            if video.url:
                raise ProviderError(
                    "Meta video upload requires a locally stored video file or an existing "
                    "Meta Video ID. The selected video only has a URL, which Meta may be "
                    "unable to fetch. Refresh the video status to store the mp4 locally, "
                    "or regenerate the video."
                )
            raise ProviderError(
                "Meta video upload requires a locally stored video file or an existing "
                "Meta Video ID. Regenerate the video before creating the ad."
            )

        upload_response = await self.facebook.create_ad_video(
            ad_account_id=ad_account_id,
            name=payload.ad_name or payload.campaign_name or f"AI video {video.id}",
            access_token_ref=access_token_ref,
            video_url=None,
            video_file_path=local_video_path,
        )
        facebook_video_id = upload_response.get("id")
        if not facebook_video_id:
            raise ProviderError("Meta video upload returned no video id.")

        video.metadata_json = {
            **(video.metadata_json or {}),
            "facebook_video_id": str(facebook_video_id),
            "facebook_video_ad_account_id": ad_account_id,
            "facebook_video_upload_response": upload_response,
        }
        await session.flush()
        return str(facebook_video_id)

    async def _access_token_ref_for_payload(
        self,
        session: AsyncSession,
        payload: dict[str, Any],
        token_kind: str,
        default_ref: str,
    ) -> str:
        facebook_account_id = _first_text(payload.get("facebook_account_id"))
        if not facebook_account_id:
            token_ref = _first_text(payload.get("access_token_ref"))
            return token_ref or default_ref

        account = await self._get_facebook_account(session, facebook_account_id)
        if token_kind == "page":
            page_token_ref = _page_access_token_ref(account, payload.get("page_id"))
            if page_token_ref:
                return str(page_token_ref)
            raise ProviderError("Selected Meta account has no Page access token.")

        if account.access_token_ref:
            return account.access_token_ref
        raise ProviderError("Selected Meta account has no ads access token.")

    async def _publish(self, session: AsyncSession, job: PublishJob) -> dict:
        if job.channel == PublishChannel.FACEBOOK_PAGE.value:
            message = job.payload.get("message")
            if not message and job.draft_id:
                draft = await get_required(session, CopyDraft, job.draft_id)
                message = draft.primary_text or draft.body

            page_id = job.payload.get("page_id") or self.settings.facebook_page_id or "dry-run-page"
            access_token_ref = await self._access_token_ref_for_payload(
                session,
                job.payload,
                "page",
                FACEBOOK_PAGE_TOKEN_REF,
            )
            video_url = job.payload.get("video_url")
            if video_url:
                return await self.facebook.publish_video_post(
                    page_id=page_id,
                    video_url=video_url,
                    description=message or "",
                    access_token_ref=access_token_ref,
                    title=job.payload.get("title"),
                    published=bool(job.payload.get("published", True)),
                )

            image_url = job.payload.get("image_url")
            if image_url:
                return await self.facebook.publish_photo_post(
                    page_id=page_id,
                    image_url=image_url,
                    caption=message or "",
                    access_token_ref=access_token_ref,
                )
            return await self.facebook.publish_page_post(
                page_id=page_id,
                message=message or "",
                access_token_ref=access_token_ref,
            )

        if job.channel == PublishChannel.FACEBOOK_AD.value:
            ad_operation = job.payload.get("ad_operation")
            if ad_operation == "create_meta_ads_draft":
                if job.metadata_json.get("review_required"):
                    review_status = job.metadata_json.get("review_status")
                    if review_status != "approved":
                        raise ProviderError("Meta ads package must be approved before publishing.")
                plan = job.payload.get("plan")
                if not isinstance(plan, dict):
                    raise ProviderError("Meta ads package is missing its prepared plan.")
                return await self._execute_meta_ads_plan(session, plan)

            if ad_operation == "create_video_ad_creative":
                page_id = job.payload.get("page_id") or self.settings.facebook_page_id or ""
                if not self._ads_dry_run_enabled() and page_id == "dry-run-page":
                    raise ProviderError("Real ad creative creation requires a Facebook Page ID.")
                video_id = job.payload.get("facebook_video_id") or job.payload.get("video_id") or ""
                creative_name = (
                    job.payload.get("name") or job.payload.get("title") or "AI video ad creative"
                )
                return await self.facebook.create_video_ad_creative(
                    ad_account_id=job.payload.get("ad_account_id")
                    or self.settings.facebook_ad_account_id
                    or "dry-run-ad-account",
                    page_id=page_id,
                    video_id=video_id,
                    name=creative_name,
                    message=job.payload.get("message") or "",
                    title=job.payload.get("title") or "AI video ad",
                    access_token_ref=await self._access_token_ref_for_payload(
                        session,
                        job.payload,
                        "ad",
                        FACEBOOK_AD_TOKEN_REF,
                    ),
                    link_url=job.payload.get("link_url"),
                    cta_type=job.payload.get("cta_type") or "LEARN_MORE",
                )

            video_url = job.payload.get("video_url")
            video_file_path = self._local_video_path(job.payload.get("video_storage_key"))
            ad_account_id = (
                job.payload.get("ad_account_id")
                or self.settings.facebook_ad_account_id
                or "dry-run-ad-account"
            )
            if video_url or video_file_path:
                return await self.facebook.create_ad_video(
                    ad_account_id=ad_account_id,
                    name=job.payload.get("name") or job.payload.get("title") or "AI video creative",
                    access_token_ref=await self._access_token_ref_for_payload(
                        session,
                        job.payload,
                        "ad",
                        FACEBOOK_AD_TOKEN_REF,
                    ),
                    video_url=video_url,
                    video_file_path=video_file_path,
                )
            if self._ads_dry_run_enabled():
                return {"id": f"dry_run_ad_{uuid4()}", "dry_run": True, "payload": job.payload}

        raise ProviderError(f"Publishing channel is not implemented: {job.channel}")

    async def _prepare_payload(self, session: AsyncSession, payload: PublishJobCreate) -> dict:
        prepared = dict(payload.payload or {})
        prepared.setdefault("channel", payload.channel.value)
        prepared.setdefault("campaign_id", payload.campaign_id)
        facebook_account = await self._get_facebook_account(
            session,
            _first_text(prepared.get("facebook_account_id")),
        )
        if payload.channel == PublishChannel.FACEBOOK_PAGE:
            prepared.setdefault(
                "page_id",
                (facebook_account.page_id if facebook_account else None)
                or self.settings.facebook_page_id
                or "dry-run-page",
            )
            page_access_token_ref = (
                _page_access_token_ref(facebook_account, prepared.get("page_id"))
                if facebook_account
                else None
            )
            prepared.setdefault(
                "access_token_ref",
                _facebook_account_token_ref(facebook_account.id, "page")
                if page_access_token_ref and facebook_account
                else FACEBOOK_PAGE_TOKEN_REF,
            )
        elif payload.channel == PublishChannel.FACEBOOK_AD:
            prepared.setdefault(
                "ad_account_id",
                (facebook_account.ad_account_id if facebook_account else None)
                or self.settings.facebook_ad_account_id
                or "dry-run-ad-account",
            )
            prepared.setdefault(
                "access_token_ref",
                _facebook_account_token_ref(facebook_account.id, "ad")
                if facebook_account and facebook_account.access_token_ref
                else FACEBOOK_AD_TOKEN_REF,
            )
            if prepared.get("ad_operation") == "create_video_ad_creative":
                prepared.setdefault(
                    "page_id",
                    (facebook_account.page_id if facebook_account else None)
                    or self.settings.facebook_page_id
                    or "dry-run-page",
                )
                prepared.setdefault("cta_type", "LEARN_MORE")
        if payload.draft_id:
            draft = cast(CopyDraft, await get_required(session, CopyDraft, payload.draft_id))
            prepared.setdefault("draft_id", payload.draft_id)
            prepared.setdefault("message", draft.primary_text or draft.body)
            prepared.setdefault("title", draft.headline or "AI generated ad")

        creative_asset_id = prepared.get("creative_asset_id")
        if creative_asset_id:
            asset = cast(
                CreativeAsset,
                await get_required(session, CreativeAsset, str(creative_asset_id)),
            )
            if asset.campaign_id != payload.campaign_id:
                raise ProviderError("Creative asset does not belong to this campaign.")
            image_url = resolve_creative_image_url(asset, self.image_storage)
            if not image_url:
                raise ProviderError("Selected creative asset has no image URL.")
            prepared.setdefault("media_type", "image")
            prepared["image_url"] = image_url
            prepared["image_storage_key"] = (
                asset.storage_key or self.image_storage.storage_key_for_public_url(asset.url)
            )

        video_asset_id = prepared.get("video_asset_id")
        if video_asset_id:
            video = cast(VideoAsset, await get_required(session, VideoAsset, str(video_asset_id)))
            if video.campaign_id != payload.campaign_id:
                raise ProviderError("Video asset does not belong to this campaign.")
            if not video.url:
                raise ProviderError("Selected video asset has no video URL.")
            prepared["media_type"] = "video"
            prepared["video_url"] = video.url
            prepared["video_storage_key"] = video.storage_key
            prepared["provider_job_id"] = video.provider_job_id
            prepared["duration_seconds"] = video.duration_seconds
            prepared["aspect_ratio"] = video.aspect_ratio

        if prepared.get("ad_operation") == "create_video_ad_creative":
            prepared["media_type"] = "video"
        elif prepared.get("video_url"):
            prepared["media_type"] = "video"
        elif prepared.get("image_url"):
            prepared.setdefault("media_type", "image")
        else:
            raise ProviderError("Publishing requires an image or video material.")

        media_type = str(prepared.get("media_type"))
        if media_type not in {"image", "video"}:
            raise ProviderError("Publishing media_type must be image or video.")
        if (
            media_type == "video"
            and prepared.get("ad_operation") != "create_video_ad_creative"
            and not prepared.get("video_url")
        ):
            raise ProviderError("Video publishing requires a generated video URL.")
        if prepared.get("ad_operation") == "create_video_ad_creative":
            if not prepared.get("facebook_video_id") and not prepared.get("video_id"):
                raise ProviderError("Ad creative creation requires a Meta video ID.")
            if not prepared.get("page_id"):
                raise ProviderError("Ad creative creation requires a Facebook Page ID.")
        if media_type == "image" and not prepared.get("image_url"):
            raise ProviderError("Image publishing requires an image URL.")

        prepared["meta_request"] = self._build_meta_request_preview(payload.channel, prepared)
        return prepared

    def _build_meta_request_preview(
        self,
        channel: PublishChannel,
        prepared: dict[str, Any],
    ) -> dict[str, Any]:
        media_type = str(prepared.get("media_type") or "")
        message = str(prepared.get("message") or "")
        if media_type not in {"image", "video"}:
            raise ProviderError("Publishing media_type must be image or video.")

        if channel == PublishChannel.FACEBOOK_PAGE:
            page_id = str(
                prepared.get("page_id") or self.settings.facebook_page_id or "dry-run-page"
            )
            if media_type == "video":
                body: dict[str, Any] = {
                    "file_url": prepared.get("video_url"),
                    "description": message,
                    "published": bool(prepared.get("published", True)),
                }
                if prepared.get("title"):
                    body["title"] = prepared["title"]
                return {
                    "operation": "facebook_page_video",
                    "method": "POST",
                    "endpoint": self.facebook.build_url(f"{page_id}/videos"),
                    "body": body,
                }
            if media_type == "image":
                return {
                    "operation": "facebook_page_photo",
                    "method": "POST",
                    "endpoint": self.facebook.build_url(f"{page_id}/photos"),
                    "body": {
                        "url": prepared.get("image_url"),
                        "caption": message,
                    },
                }
            raise ProviderError("Facebook Page publishing requires an image or video material.")

        if channel == PublishChannel.FACEBOOK_AD:
            ad_account_id = str(
                prepared.get("ad_account_id")
                or self.settings.facebook_ad_account_id
                or "dry-run-ad-account"
            )
            account_id = self.facebook.normalize_ad_account_id(ad_account_id)
            if prepared.get("ad_operation") == "create_video_ad_creative":
                video_id = prepared.get("facebook_video_id") or prepared.get("video_id")
                object_story_spec: dict[str, Any] = {
                    "page_id": prepared.get("page_id"),
                    "video_data": {
                        "video_id": video_id,
                        "message": message,
                        "title": prepared.get("title") or "AI video ad",
                    },
                }
                if prepared.get("link_url") and prepared.get("cta_type"):
                    object_story_spec["video_data"]["call_to_action"] = {
                        "type": prepared.get("cta_type"),
                        "value": {"link": prepared.get("link_url")},
                    }
                return {
                    "operation": "facebook_ad_video_creative",
                    "method": "POST",
                    "endpoint": self.facebook.build_url(f"{account_id}/adcreatives"),
                    "body": {
                        "name": prepared.get("name")
                        or prepared.get("title")
                        or "AI video ad creative",
                        "object_story_spec": object_story_spec,
                    },
                }
            if media_type == "video":
                body: dict[str, Any] = {
                    "name": prepared.get("name")
                    or prepared.get("title")
                    or "AI video creative",
                }
                local_video_path = self._local_video_path(prepared.get("video_storage_key"))
                if local_video_path:
                    body["source"] = local_video_path.name
                    body["upload_mode"] = "multipart_file"
                else:
                    body["file_url"] = prepared.get("video_url")
                    body["upload_mode"] = "file_url"
                return {
                    "operation": "facebook_ad_video_upload",
                    "method": "POST",
                    "endpoint": self.facebook.build_url(f"{account_id}/advideos"),
                    "body": body,
                }
            return {
                "operation": "facebook_ad_payload_preview",
                "method": "POST",
                "endpoint": self.facebook.build_url(f"{account_id}/adcreatives"),
                "body": dict(prepared),
            }

        raise ProviderError(f"Publishing channel is not implemented: {channel}")

    def _local_video_path(self, storage_key: Any) -> Path | None:
        if not isinstance(storage_key, str) or not storage_key.startswith("local://"):
            return None
        relative_key = storage_key.removeprefix("local://").lstrip("/\\")
        return Path(self.settings.local_storage_root) / Path(relative_key)

    def _local_image_path(self, storage_key: Any) -> Path | None:
        if not isinstance(storage_key, str) or not storage_key.startswith("local://"):
            return None
        relative_key = storage_key.removeprefix("local://").lstrip("/\\")
        relative_path = Path(relative_key)
        if not relative_key or relative_path.is_absolute() or ".." in relative_path.parts:
            raise ProviderError("Invalid local image storage key.")
        return Path(self.settings.local_storage_root) / relative_path


def _extract_facebook_image_hash(response: dict[str, Any], file_name: str) -> str | None:
    images = response.get("images")
    if isinstance(images, dict):
        image_data = images.get(file_name)
        if not isinstance(image_data, dict):
            image_data = next(
                (item for item in images.values() if isinstance(item, dict)),
                None,
            )
        if isinstance(image_data, dict):
            image_hash = image_data.get("hash")
            if isinstance(image_hash, str) and image_hash.strip():
                return image_hash

    image_hash = response.get("hash")
    if isinstance(image_hash, str) and image_hash.strip():
        return image_hash
    return None


COUNTRY_CODE_MAP = {
    "india": "IN",
    "印度": "IN",
    "in": "IN",
    "unitedstates": "US",
    "usa": "US",
    "us": "US",
    "美国": "US",
    "unitedkingdom": "GB",
    "uk": "GB",
    "英国": "GB",
    "indonesia": "ID",
    "印尼": "ID",
    "印度尼西亚": "ID",
    "thailand": "TH",
    "泰国": "TH",
    "vietnam": "VN",
    "越南": "VN",
    "philippines": "PH",
    "菲律宾": "PH",
    "malaysia": "MY",
    "马来西亚": "MY",
    "singapore": "SG",
    "新加坡": "SG",
    "brazil": "BR",
    "巴西": "BR",
    "mexico": "MX",
    "墨西哥": "MX",
    "unitedarabemirates": "AE",
    "uae": "AE",
    "阿联酋": "AE",
}


def _meta_ads_plan_errors(plan: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not plan.get("destination_url"):
        errors.append("Meta ads creation requires a destination URL.")
    if not plan.get("headline"):
        errors.append("Meta ads creation requires a headline.")
    if not plan.get("primary_text"):
        errors.append("Meta ads creation requires primary text.")
    if plan.get("page_id") == "dry-run-page":
        errors.append("Meta ads creation requires a real Facebook Page ID.")
    if plan.get("ad_account_id") == "dry-run-ad-account":
        errors.append("Meta ads creation requires a real Ad Account ID.")
    adset_payload = plan.get("adset_payload") or {}
    if adset_payload.get("daily_budget") == "<DAILY_BUDGET_REQUIRED>":
        errors.append("Meta ads creation requires daily_budget.")
    if _contains_required_placeholder(plan.get("creative_payload")):
        errors.append("Meta ads creation requires all creative placeholders to be resolved.")
    link_data = (
        (plan.get("creative_payload") or {})
        .get("object_story_spec", {})
        .get("link_data", {})
    )
    if isinstance(link_data, dict) and _is_local_picture_without_hash(link_data):
        errors.append(
            "Meta ads creation requires image_hash for local images. Re-prepare the "
            "Meta ads package so the image is uploaded to Meta first."
        )
    return errors


def is_meta_ads_package_payload(payload: dict[str, Any]) -> bool:
    return payload.get("ad_operation") == "create_meta_ads_draft"


def summarize_meta_ads_status(objects: dict[str, dict[str, Any]]) -> dict[str, Any]:
    effective_statuses = [
        _upper_text(value.get("effective_status") or value.get("status"))
        for value in objects.values()
    ]
    configured_statuses = [
        _upper_text(value.get("configured_status") or value.get("status"))
        for value in objects.values()
    ]
    all_statuses = [status for status in [*effective_statuses, *configured_statuses] if status]

    if any(status in {"DISAPPROVED", "REJECTED"} for status in all_statuses):
        review_status = "rejected"
        delivery_status = "meta_rejected"
    elif any(status in {"PENDING_REVIEW", "PREAPPROVED"} for status in all_statuses):
        review_status = "reviewing"
        delivery_status = "meta_reviewing"
    elif effective_statuses and all(status == "ACTIVE" for status in effective_statuses if status):
        review_status = "approved"
        delivery_status = "meta_active"
    elif any("PAUSED" in status for status in all_statuses):
        review_status = "paused"
        delivery_status = "paused"
    elif any(status in {"ARCHIVED", "DELETED"} for status in all_statuses):
        review_status = "inactive"
        delivery_status = "archived"
    else:
        review_status = "unknown"
        delivery_status = "meta_unknown"

    return {
        "review_status": review_status,
        "delivery_status": delivery_status,
        "effective_statuses": {
            key: _first_text(value.get("effective_status") or value.get("status"))
            for key, value in objects.items()
        },
        "configured_statuses": {
            key: _first_text(value.get("configured_status") or value.get("status"))
            for key, value in objects.items()
        },
    }


def summarize_meta_ads_insights(
    insight_row: dict[str, Any],
    objects: dict[str, dict[str, Any]],
    plan: dict[str, Any],
) -> dict[str, Any]:
    adset = objects.get("adset") or {}
    ad = objects.get("ad") or {}
    campaign = objects.get("campaign") or {}
    action_types = _result_action_types(plan)
    result_count, result_type = _first_action_value(insight_row.get("actions"), action_types)
    cost_per_result = _first_action_decimal(insight_row.get("cost_per_action_type"), action_types)
    spend = _decimal_value(insight_row.get("spend"))
    if cost_per_result == 0 and result_count:
        cost_per_result = spend / Decimal(result_count)

    budget_type, budget_amount = _budget_from_adset(adset)
    clicks = _int_value(
        insight_row.get("inline_link_clicks"),
        insight_row.get("clicks"),
        _first_action_value(insight_row.get("actions"), ["link_click"])[0],
    )
    conversions = result_count if result_type and result_type != "link_click" else 0

    return {
        "ad_name": _first_text(insight_row.get("ad_name"), ad.get("name")),
        "adset_name": _first_text(insight_row.get("adset_name"), adset.get("name")),
        "campaign_name": _first_text(insight_row.get("campaign_name"), campaign.get("name")),
        "currency": _first_text(insight_row.get("account_currency")) or "USD",
        "spend": _decimal_text(spend),
        "impressions": _int_value(insight_row.get("impressions")),
        "reach": _int_value(insight_row.get("reach")),
        "clicks": clicks,
        "result_count": result_count,
        "result_type": result_type or action_types[0],
        "cost_per_result": _decimal_text(cost_per_result),
        "cpc": _decimal_text(_decimal_value(insight_row.get("cpc"))),
        "ctr": _decimal_text(_decimal_value(insight_row.get("ctr"))),
        "conversions": conversions,
        "budget_type": budget_type,
        "budget_amount": budget_amount,
        "end_time": _first_text(adset.get("end_time")),
        "attribution_spec": adset.get("attribution_spec"),
        "bid_strategy": _first_text(adset.get("bid_strategy"), campaign.get("bid_strategy")),
        "optimization_goal": _first_text(adset.get("optimization_goal")),
        "billing_event": _first_text(adset.get("billing_event")),
        "last_update_time": _latest_text_time(
            campaign.get("updated_time"),
            adset.get("updated_time"),
            ad.get("updated_time"),
        ),
        "quality_ranking": _first_text(insight_row.get("quality_ranking"), "UNKNOWN"),
        "engagement_rate_ranking": _first_text(
            insight_row.get("engagement_rate_ranking"),
            "UNKNOWN",
        ),
        "conversion_rate_ranking": _first_text(
            insight_row.get("conversion_rate_ranking"),
            "UNKNOWN",
        ),
    }


def _first_insight_row(insights: dict[str, Any]) -> dict[str, Any]:
    data = insights.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return {}


def _result_action_types(plan: dict[str, Any]) -> list[str]:
    target = (
        ((plan.get("targeting_summary") or {}).get("conversion_event_type"))
        or (plan.get("conversion_event") or "")
    )
    normalized = str(target).strip().upper()
    if normalized == "PURCHASE":
        return ["purchase", "offsite_conversion.fb_pixel_purchase", "omni_purchase"]
    if normalized == "ADD_TO_CART":
        return [
            "add_to_cart",
            "offsite_conversion.fb_pixel_add_to_cart",
            "omni_add_to_cart",
        ]
    if normalized == "LEAD":
        return ["lead", "offsite_conversion.fb_pixel_lead", "onsite_conversion.lead_grouped"]
    return ["link_click", "landing_page_view", "onsite_conversion.post_save"]


def _first_action_value(actions: Any, action_types: list[str]) -> tuple[int, str | None]:
    if not isinstance(actions, list):
        return 0, None
    action_type_set = set(action_types)
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_type = _first_text(action.get("action_type"))
        if action_type in action_type_set:
            return _int_value(action.get("value")), action_type
    return 0, None


def _first_action_decimal(actions: Any, action_types: list[str]) -> Decimal:
    if not isinstance(actions, list):
        return Decimal("0")
    action_type_set = set(action_types)
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_type = _first_text(action.get("action_type"))
        if action_type in action_type_set:
            return _decimal_value(action.get("value"))
    return Decimal("0")


def _budget_from_adset(adset: dict[str, Any]) -> tuple[str | None, str | None]:
    daily_budget = _first_text(adset.get("daily_budget"))
    if daily_budget:
        return "daily_budget", daily_budget
    lifetime_budget = _first_text(adset.get("lifetime_budget"))
    if lifetime_budget:
        return "lifetime_budget", lifetime_budget
    return None, None


def _latest_text_time(*values: Any) -> str | None:
    texts = [text for text in (_first_text(value) for value in values) if text]
    return max(texts) if texts else None


def _meta_ads_ids_from_job(job: PublishJob) -> dict[str, str | None]:
    metadata = job.metadata_json or {}
    ids = metadata.get("meta_ads_ids")
    if not isinstance(ids, dict):
        provider_response = metadata.get("provider_response")
        if isinstance(provider_response, dict):
            ids = provider_response.get("meta_ads_ids")
    if not isinstance(ids, dict):
        ids = {}
    return {
        "campaign_id": _first_text(ids.get("campaign_id")),
        "adset_id": _first_text(ids.get("adset_id")),
        "ad_creative_id": _first_text(ids.get("ad_creative_id")),
        "ad_id": _first_text(ids.get("ad_id"), job.external_id),
    }


def _meta_ads_plan_from_job(job: PublishJob) -> dict[str, Any]:
    metadata_plan = (job.metadata_json or {}).get("plan")
    if isinstance(metadata_plan, dict):
        plan = dict(metadata_plan)
    else:
        payload_plan = job.payload.get("plan")
        plan = dict(payload_plan) if isinstance(payload_plan, dict) else {}

    if not plan.get("facebook_account_id") and job.payload.get("facebook_account_id"):
        plan["facebook_account_id"] = job.payload.get("facebook_account_id")
    return plan


def _contains_required_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith("<") and value.endswith("_REQUIRED>")
    if isinstance(value, dict):
        return any(_contains_required_placeholder(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_required_placeholder(item) for item in value)
    return False


def _is_local_url(url: str) -> bool:
    return url.startswith(("http://127.0.0.1", "http://localhost", "https://localhost"))


def _is_local_picture_without_hash(link_data: dict[str, Any]) -> bool:
    picture = link_data.get("picture")
    return (
        not link_data.get("image_hash")
        and isinstance(picture, str)
        and _is_local_url(picture)
    )


def _build_work_order_context(campaign: Campaign, work_order: WorkOrder | None) -> dict[str, Any]:
    metadata_work_order = (campaign.metadata_json or {}).get("work_order") or {}
    metadata_parsed = metadata_work_order.get("parsed_fields") or {}
    work_order_parsed = work_order.parsed_fields if work_order else {}
    parsed_fields = {**metadata_parsed, **(work_order_parsed or {})}
    reviewed_delivery_fields = _merge_delivery_fields(
        metadata_work_order.get("reviewed_delivery_fields"),
        (work_order.metadata_json or {}).get("reviewed_delivery_fields") if work_order else None,
    )
    reviewed_audience_description = _audience_from_reviewed_delivery_fields(
        reviewed_delivery_fields
    )
    return {
        "raw_content": (work_order.raw_content if work_order else None)
        or metadata_work_order.get("raw_content"),
        "parsed_fields": parsed_fields,
        "reviewed_delivery_fields": reviewed_delivery_fields,
        "country": _delivery_field_text(reviewed_delivery_fields, "country")
        or (work_order.country if work_order else None)
        or metadata_work_order.get("country"),
        "media": (work_order.media if work_order else None) or metadata_work_order.get("media"),
        "event_name": _delivery_field_text(reviewed_delivery_fields, "event_name")
        or (work_order.event_name if work_order else None)
        or metadata_work_order.get("event_name"),
        "product_name": (work_order.product_name if work_order else None)
        or metadata_work_order.get("product_name"),
        "audience_description": reviewed_audience_description
        or (work_order.audience_description if work_order else None)
        or metadata_work_order.get("audience_description"),
        "landing_url": _delivery_field_text(reviewed_delivery_fields, "landing_url")
        or (work_order.landing_url if work_order else None)
        or metadata_work_order.get("landing_url"),
    }


def _merge_delivery_fields(*values: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for value in values:
        if isinstance(value, dict):
            merged.update(value)
    return merged


def _delivery_field_text(fields: dict[str, Any], key: str) -> str | None:
    value = fields.get(key)
    if isinstance(value, dict):
        value = value.get("value") or value.get("normalized_value")
    return _first_text(value)


def _audience_from_reviewed_delivery_fields(fields: dict[str, Any]) -> str | None:
    raw = _delivery_field_text(fields, "audience_description_raw")
    gender = _delivery_field_text(fields, "gender")
    age_min = _delivery_field_text(fields, "age_min")
    age_max = _delivery_field_text(fields, "age_max")
    parts = [raw] if raw else []
    if gender and gender not in {"不限", "all"}:
        parts.append(gender)
    if age_min and age_max and age_min != "不限" and age_max != "不限":
        parts.append(f"年龄{age_min}-{age_max}")
    if not parts and (gender == "不限" or age_min == "不限" or age_max == "不限"):
        return "不限"
    return "。".join(dict.fromkeys(parts)) if parts else None


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _int_value(*values: Any) -> int:
    text = _first_text(*values)
    if not text:
        return 0
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return 0


def _decimal_value(value: Any) -> Decimal:
    text = _first_text(value)
    if not text:
        return Decimal("0")
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _decimal_text(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def _upper_text(value: Any) -> str:
    text = _first_text(value)
    return text.upper() if text else ""


def _compact_name(*parts: Any) -> str:
    values = [_first_text(part) for part in parts]
    return " - ".join(value for value in values if value)


def _facebook_account_token_ref(account_id: str, token_kind: str) -> str:
    return f"facebook_account:{account_id}:{token_kind}"


def _page_access_token_ref(account: FacebookAccount, page_id: Any | None) -> str | None:
    metadata = account.metadata_json or {}
    selected_page_id = _first_text(page_id) or _first_text(account.page_id)
    page_token_refs = metadata.get("page_access_token_refs")
    if isinstance(page_token_refs, dict) and selected_page_id:
        token_ref = page_token_refs.get(selected_page_id)
        if token_ref:
            return str(token_ref)
    token_ref = metadata.get("page_access_token_ref")
    return str(token_ref) if token_ref else None


def _build_targeting(
    country: str | None,
    audience_description: str | None,
    raw_content: Any,
) -> dict[str, Any]:
    country_code = _country_code(country)
    age_min, age_max = _parse_age_range(audience_description, raw_content)
    genders, gender_label = _parse_gender(audience_description, raw_content)
    payload: dict[str, Any] = {}
    if country_code:
        payload["geo_locations"] = {"countries": [country_code]}
    if age_min:
        payload["age_min"] = age_min
    if age_max:
        payload["age_max"] = age_max
    if genders:
        payload["genders"] = genders
    payload["targeting_automation"] = {"advantage_audience": 0}

    summary = {
        "country": country,
        "country_code": country_code,
        "audience_description": audience_description,
        "age_min": age_min,
        "age_max": age_max,
        "age_label": f"{age_min}-{age_max}" if age_min and age_max else None,
        "genders": genders,
        "gender_label": gender_label,
    }
    return {"payload": payload, "summary": summary}


def _country_code(country: str | None) -> str | None:
    if not country:
        return None
    cleaned = re.sub(r"[\s_\-]+", "", country.strip().lower())
    if len(cleaned) == 2 and cleaned.isascii() and cleaned.isalpha():
        return cleaned.upper()
    return COUNTRY_CODE_MAP.get(cleaned)


def _parse_age_range(*texts: Any) -> tuple[int | None, int | None]:
    pattern = re.compile(r"(?:年龄|age)?[^\d]{0,8}(\d{2})\s*(?:-|~|至|到|—|–)\s*(\d{2})", re.I)
    for value in texts:
        if not value:
            continue
        match = pattern.search(str(value))
        if not match:
            continue
        age_min = int(match.group(1))
        age_max = int(match.group(2))
        if 13 <= age_min <= age_max <= 65:
            return age_min, age_max
    return None, None


def _parse_gender(*texts: Any) -> tuple[list[int], str | None]:
    combined = " ".join(str(value) for value in texts if value).lower()
    if not combined:
        return [], None
    has_female = "女" in combined or re.search(r"\b(female|women|woman)\b", combined) is not None
    has_male = "男" in combined or re.search(r"\b(male|men|man)\b", combined) is not None
    if has_male and not has_female:
        return [1], "male"
    if has_female and not has_male:
        return [2], "female"
    return [], "all" if has_male and has_female else None


def _event_mapping_for_available_inputs(
    event_mapping: dict[str, str],
    pixel_id: str | None,
) -> dict[str, str]:
    if event_mapping["optimization_goal"] != "OFFSITE_CONVERSIONS" or pixel_id:
        return event_mapping
    return {
        "campaign_objective": "OUTCOME_TRAFFIC",
        "optimization_goal": "LINK_CLICKS",
        "custom_event_type": "LINK_CLICK",
    }


def _map_event_to_meta(event_name: str | None) -> dict[str, str]:
    normalized = (event_name or "").strip().lower()
    normalized = re.sub(r"\s+", "", normalized)
    if any(keyword in normalized for keyword in ["购物", "购买", "purchase", "shop", "shopping"]):
        return {
            "campaign_objective": "OUTCOME_SALES",
            "optimization_goal": "OFFSITE_CONVERSIONS",
            "custom_event_type": "PURCHASE",
        }
    if any(keyword in normalized for keyword in ["加购", "addtocart", "cart"]):
        return {
            "campaign_objective": "OUTCOME_SALES",
            "optimization_goal": "OFFSITE_CONVERSIONS",
            "custom_event_type": "ADD_TO_CART",
        }
    if any(keyword in normalized for keyword in ["注册", "lead", "线索"]):
        return {
            "campaign_objective": "OUTCOME_LEADS",
            "optimization_goal": "OFFSITE_CONVERSIONS",
            "custom_event_type": "LEAD",
        }
    return {
        "campaign_objective": "OUTCOME_TRAFFIC",
        "optimization_goal": "LINK_CLICKS",
        "custom_event_type": "OTHER",
    }
