import re
from copy import deepcopy
from datetime import UTC
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
    MetaAdsDraftCreateRequest,
    MetaAdsPackagePrepareRequest,
    PublishJobCreate,
)
from backend.app.services.utils import get_required


class PublishService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.facebook = FacebookGraphClient(self.settings)

    def _refresh_settings(self) -> None:
        cached_settings = get_settings()
        if self.settings is not cached_settings:
            return
        get_settings.cache_clear()
        self.settings = get_settings()
        self.facebook = FacebookGraphClient(self.settings)

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

        image_url = None
        if payload.creative_asset_id:
            creative = cast(
                CreativeAsset,
                await get_required(session, CreativeAsset, payload.creative_asset_id),
            )
            if creative.campaign_id != campaign.id:
                raise ProviderError("Creative asset does not belong to this campaign.")
            image_url = creative.url

        video_asset = None
        if payload.video_asset_id:
            video_asset = cast(
                VideoAsset,
                await get_required(session, VideoAsset, payload.video_asset_id),
            )
            if video_asset.campaign_id != campaign.id:
                raise ProviderError("Video asset does not belong to this campaign.")

        destination_url = (
            payload.destination_url
            or (work_order.landing_url if work_order else None)
            or (campaign.metadata_json or {}).get("work_order", {}).get("landing_url")
        )
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
        page_id = payload.page_id or self.settings.facebook_page_id or "dry-run-page"
        ad_account_id = (
            payload.ad_account_id or self.settings.facebook_ad_account_id or "dry-run-ad-account"
        )
        facebook_video_id = payload.facebook_video_id
        if not facebook_video_id and video_asset:
            facebook_video_id = (video_asset.metadata_json or {}).get("facebook_video_id")

        media_type = "text"
        if facebook_video_id or video_asset:
            media_type = "video"
        elif image_url:
            media_type = "image"

        object_story_spec: dict[str, Any] = {"page_id": page_id}
        if media_type == "video":
            object_story_spec["video_data"] = {
                "video_id": facebook_video_id or "<META_VIDEO_ID_REQUIRED>",
                "message": primary_text,
                "title": headline,
            }
            if destination_url:
                object_story_spec["video_data"]["call_to_action"] = {
                    "type": payload.cta_type,
                    "value": {"link": destination_url},
                }
        elif media_type == "image":
            object_story_spec["link_data"] = {
                "link": destination_url,
                "message": primary_text,
                "name": headline,
                "description": description,
                "picture": image_url,
                "call_to_action": {
                    "type": payload.cta_type,
                    "value": {"link": destination_url},
                },
            }
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
            "draft_id": draft.id if draft else None,
            "topic_id": topic.id if topic else None,
            "destination_url": destination_url,
            "headline": headline,
            "primary_text": primary_text,
            "description": description,
            "media_type": media_type,
            "creative_asset_id": payload.creative_asset_id,
            "image_url": image_url,
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
                "destination_url": "work_order.landing_url",
                "headline": "topic.title",
                "primary_text": "copy_draft.primary_text",
                "description": "copy_draft.description or campaign.product_name",
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
                draft_id=payload.draft_id,
                topic_id=payload.topic_id,
                creative_asset_id=payload.creative_asset_id,
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
        media_type = str(creative_draft.get("media_type") or "text")
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
            "dry_run": self.settings.facebook_dry_run,
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
            "draft_id": creative_draft.get("draft_id"),
            "topic_id": creative_draft.get("topic_id"),
            "destination_url": creative_draft.get("destination_url"),
            "headline": headline,
            "primary_text": creative_draft.get("primary_text") or "",
            "media_type": media_type,
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

        if not self.settings.facebook_ad_account_id and not payload.ad_account_id:
            raise ProviderError("FACEBOOK_AD_ACCOUNT_ID is required.")
        if not self.settings.facebook_ad_access_token:
            raise ProviderError("FACEBOOK_AD_ACCESS_TOKEN is required.")

        plan_request = AdsPlanDraftRequest(
            campaign_id=payload.campaign_id,
            draft_id=payload.draft_id,
            topic_id=payload.topic_id,
            creative_asset_id=payload.creative_asset_id,
            video_asset_id=payload.video_asset_id,
            facebook_video_id=payload.facebook_video_id,
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
                "dry_run": self.settings.facebook_dry_run,
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
            access_token_ref = FACEBOOK_AD_TOKEN_REF

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
            "dry_run": self.settings.facebook_dry_run,
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

        plan_request = AdsPlanDraftRequest(
            campaign_id=payload.campaign_id,
            draft_id=payload.draft_id,
            topic_id=payload.topic_id,
            creative_asset_id=payload.creative_asset_id,
            video_asset_id=payload.video_asset_id,
            facebook_video_id=payload.facebook_video_id,
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
                "dry_run": self.settings.facebook_dry_run,
                "page_id": plan["page_id"],
                "ad_account_id": plan["ad_account_id"],
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

    async def _execute_meta_ads_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
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
        access_token_ref = FACEBOOK_AD_TOKEN_REF

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
            "dry_run": self.settings.facebook_dry_run,
            "meta_ads_ids": ids,
            "provider_response": responses,
            "plan": plan,
        }

    async def _publish(self, session: AsyncSession, job: PublishJob) -> dict:
        if job.channel == PublishChannel.FACEBOOK_PAGE.value:
            message = job.payload.get("message")
            if not message and job.draft_id:
                draft = await get_required(session, CopyDraft, job.draft_id)
                message = draft.primary_text or draft.body

            page_id = job.payload.get("page_id") or self.settings.facebook_page_id or "dry-run-page"
            access_token_ref = job.payload.get("access_token_ref") or FACEBOOK_PAGE_TOKEN_REF
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
                return await self._execute_meta_ads_plan(plan)

            if ad_operation == "create_video_ad_creative":
                page_id = job.payload.get("page_id") or self.settings.facebook_page_id or ""
                if not self.settings.facebook_dry_run and page_id == "dry-run-page":
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
                    access_token_ref=job.payload.get("access_token_ref") or FACEBOOK_AD_TOKEN_REF,
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
                    access_token_ref=job.payload.get("access_token_ref"),
                    video_url=video_url,
                    video_file_path=video_file_path,
                )
            if self.settings.facebook_dry_run:
                return {"id": f"dry_run_ad_{uuid4()}", "dry_run": True, "payload": job.payload}

        raise ProviderError(f"Publishing channel is not implemented: {job.channel}")

    async def _prepare_payload(self, session: AsyncSession, payload: PublishJobCreate) -> dict:
        prepared = dict(payload.payload or {})
        prepared.setdefault("channel", payload.channel.value)
        prepared.setdefault("campaign_id", payload.campaign_id)
        if payload.channel == PublishChannel.FACEBOOK_PAGE:
            prepared.setdefault("page_id", self.settings.facebook_page_id or "dry-run-page")
            prepared.setdefault("access_token_ref", FACEBOOK_PAGE_TOKEN_REF)
        elif payload.channel == PublishChannel.FACEBOOK_AD:
            prepared.setdefault(
                "ad_account_id",
                self.settings.facebook_ad_account_id or "dry-run-ad-account",
            )
            prepared.setdefault("access_token_ref", FACEBOOK_AD_TOKEN_REF)
            if prepared.get("ad_operation") == "create_video_ad_creative":
                prepared.setdefault("page_id", self.settings.facebook_page_id or "dry-run-page")
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
            if not asset.url:
                raise ProviderError("Selected creative asset has no image URL.")
            prepared.setdefault("media_type", "image")
            prepared["image_url"] = asset.url
            prepared["image_storage_key"] = asset.storage_key

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
            prepared.setdefault("media_type", "text")

        media_type = str(prepared.get("media_type"))
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
        media_type = str(prepared.get("media_type") or "text")
        message = str(prepared.get("message") or "")

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
            return {
                "operation": "facebook_page_feed",
                "method": "POST",
                "endpoint": self.facebook.build_url(f"{page_id}/feed"),
                "body": {"message": message},
            }

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
    return errors


def _contains_required_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith("<") and value.endswith("_REQUIRED>")
    if isinstance(value, dict):
        return any(_contains_required_placeholder(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_required_placeholder(item) for item in value)
    return False


def _build_work_order_context(campaign: Campaign, work_order: WorkOrder | None) -> dict[str, Any]:
    metadata_work_order = (campaign.metadata_json or {}).get("work_order") or {}
    metadata_parsed = metadata_work_order.get("parsed_fields") or {}
    work_order_parsed = work_order.parsed_fields if work_order else {}
    parsed_fields = {**metadata_parsed, **(work_order_parsed or {})}
    return {
        "raw_content": (work_order.raw_content if work_order else None)
        or metadata_work_order.get("raw_content"),
        "parsed_fields": parsed_fields,
        "country": (work_order.country if work_order else None)
        or metadata_work_order.get("country"),
        "media": (work_order.media if work_order else None) or metadata_work_order.get("media"),
        "event_name": (work_order.event_name if work_order else None)
        or metadata_work_order.get("event_name"),
        "product_name": (work_order.product_name if work_order else None)
        or metadata_work_order.get("product_name"),
        "audience_description": (work_order.audience_description if work_order else None)
        or metadata_work_order.get("audience_description"),
        "landing_url": (work_order.landing_url if work_order else None)
        or metadata_work_order.get("landing_url"),
    }


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _compact_name(*parts: Any) -> str:
    values = [_first_text(part) for part in parts]
    return " - ".join(value for value in values if value)


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
