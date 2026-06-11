import json
from pathlib import Path

import pytest

from backend.app.core.config import Settings
from backend.app.db.models.enums import PublishChannel
from backend.app.integrations.facebook.graph_client import FacebookGraphClient
from backend.app.integrations.facebook.token_manager import (
    FACEBOOK_AD_TOKEN_REF,
    FACEBOOK_PAGE_TOKEN_REF,
    FacebookTokenManager,
)
from backend.app.services.publish_service import PublishService


@pytest.mark.asyncio
async def test_facebook_page_video_dry_run_payload() -> None:
    client = FacebookGraphClient(
        Settings(
            facebook_dry_run=True,
            facebook_graph_api_base_url="https://graph.facebook.com",
            facebook_graph_api_version="v24.0",
        )
    )

    response = await client.publish_video_post(
        page_id="page-123",
        video_url="http://127.0.0.1:8000/storage/videos/video.mp4",
        description="Launch copy",
        title="Launch title",
        access_token_ref=None,
    )

    assert response["dry_run"] is True
    assert response["endpoint"] == "https://graph.facebook.com/v24.0/page-123/videos"
    assert response["payload"]["file_url"].endswith("/video.mp4")
    assert response["payload"]["description"] == "Launch copy"
    assert response["payload"]["title"] == "Launch title"


@pytest.mark.asyncio
async def test_facebook_ad_video_dry_run_payload_normalizes_account_id() -> None:
    client = FacebookGraphClient(
        Settings(
            facebook_dry_run=True,
            facebook_graph_api_base_url="https://graph.facebook.com",
            facebook_graph_api_version="v24.0",
        )
    )

    response = await client.create_ad_video(
        ad_account_id="123456",
        name="Video creative",
        access_token_ref=None,
        video_url="http://127.0.0.1:8000/storage/videos/video.mp4",
    )

    assert response["dry_run"] is True
    assert response["endpoint"] == "https://graph.facebook.com/v24.0/act_123456/advideos"
    assert response["payload"] == {
        "name": "Video creative",
        "file_url": "http://127.0.0.1:8000/storage/videos/video.mp4",
    }


@pytest.mark.asyncio
async def test_facebook_ad_video_dry_run_payload_supports_local_file_upload() -> None:
    client = FacebookGraphClient(
        Settings(
            facebook_dry_run=True,
            facebook_graph_api_base_url="https://graph.facebook.com",
            facebook_graph_api_version="v24.0",
        )
    )

    response = await client.create_ad_video(
        ad_account_id="act_123456",
        name="Video creative",
        access_token_ref=None,
        video_file_path=Path("storage/videos/example.mp4"),
    )

    assert response["dry_run"] is True
    assert response["endpoint"] == "https://graph.facebook.com/v24.0/act_123456/advideos"
    assert response["payload"] == {
        "name": "Video creative",
        "source": "example.mp4",
    }


@pytest.mark.asyncio
async def test_facebook_video_ad_creative_dry_run_payload() -> None:
    client = FacebookGraphClient(
        Settings(
            facebook_dry_run=True,
            facebook_graph_api_base_url="https://graph.facebook.com",
            facebook_graph_api_version="v24.0",
        )
    )

    response = await client.create_video_ad_creative(
        ad_account_id="123456",
        page_id="page-123",
        video_id="video-123",
        name="Video ad creative",
        message="Launch message",
        title="Launch title",
        link_url="https://example.com",
        cta_type="LEARN_MORE",
        access_token_ref=None,
    )

    assert response["dry_run"] is True
    assert response["endpoint"] == "https://graph.facebook.com/v24.0/act_123456/adcreatives"
    assert response["payload"]["name"] == "Video ad creative"
    assert response["payload"]["object_story_spec"] == {
        "page_id": "page-123",
        "video_data": {
            "video_id": "video-123",
            "message": "Launch message",
            "title": "Launch title",
            "call_to_action": {
                "type": "LEARN_MORE",
                "value": {"link": "https://example.com"},
            },
        },
    }


@pytest.mark.asyncio
async def test_facebook_meta_ads_object_dry_run_payloads() -> None:
    client = FacebookGraphClient(
        Settings(
            facebook_dry_run=True,
            facebook_graph_api_base_url="https://graph.facebook.com",
            facebook_graph_api_version="v24.0",
        )
    )

    campaign = await client.create_ad_campaign(
        ad_account_id="123456",
        payload={
            "name": "Launch campaign",
            "objective": "OUTCOME_SALES",
            "status": "PAUSED",
            "special_ad_categories": [],
        },
        access_token_ref=None,
    )
    adset = await client.create_adset(
        ad_account_id="123456",
        payload={
            "name": "Launch ad set",
            "campaign_id": "campaign-123",
            "daily_budget": 100,
            "targeting": {"geo_locations": {"countries": ["IN"]}},
            "status": "PAUSED",
        },
        access_token_ref=None,
    )
    ad = await client.create_ad(
        ad_account_id="123456",
        payload={
            "name": "Launch ad",
            "adset_id": "adset-123",
            "creative": {"creative_id": "creative-123"},
            "status": "PAUSED",
        },
        access_token_ref=None,
    )

    assert campaign["dry_run"] is True
    assert campaign["endpoint"].endswith("/act_123456/campaigns")
    assert campaign["payload"]["status"] == "PAUSED"
    assert adset["endpoint"].endswith("/act_123456/adsets")
    assert adset["payload"]["targeting"]["geo_locations"]["countries"] == ["IN"]
    assert ad["endpoint"].endswith("/act_123456/ads")
    assert ad["payload"]["creative"] == {"creative_id": "creative-123"}


def test_publish_service_builds_video_meta_request_preview() -> None:
    service = PublishService()

    preview = service._build_meta_request_preview(
        PublishChannel.FACEBOOK_PAGE,
        {
            "media_type": "video",
            "page_id": "page-123",
            "message": "Launch copy",
            "title": "Launch title",
            "video_url": "http://127.0.0.1:8000/storage/videos/video.mp4",
        },
    )

    assert preview["operation"] == "facebook_page_video"
    assert preview["method"] == "POST"
    assert preview["endpoint"].endswith("/page-123/videos")
    assert preview["body"]["file_url"].endswith("/video.mp4")
    assert preview["body"]["description"] == "Launch copy"


def test_publish_service_builds_ad_video_local_upload_preview() -> None:
    service = PublishService()

    preview = service._build_meta_request_preview(
        PublishChannel.FACEBOOK_AD,
        {
            "media_type": "video",
            "ad_account_id": "123456",
            "title": "Video creative",
            "video_url": "http://127.0.0.1:8000/storage/videos/video.mp4",
            "video_storage_key": "local://videos/video-id/video.mp4",
        },
    )

    assert preview["operation"] == "facebook_ad_video_upload"
    assert preview["endpoint"].endswith("/act_123456/advideos")
    assert preview["body"] == {
        "name": "Video creative",
        "source": "video.mp4",
        "upload_mode": "multipart_file",
    }


def test_publish_service_builds_video_ad_creative_preview() -> None:
    service = PublishService()

    preview = service._build_meta_request_preview(
        PublishChannel.FACEBOOK_AD,
        {
            "ad_operation": "create_video_ad_creative",
            "media_type": "video",
            "ad_account_id": "123456",
            "page_id": "page-123",
            "facebook_video_id": "video-123",
            "name": "Video ad creative",
            "message": "Launch message",
            "title": "Launch title",
            "link_url": "https://example.com",
            "cta_type": "LEARN_MORE",
        },
    )

    assert preview["operation"] == "facebook_ad_video_creative"
    assert preview["endpoint"].endswith("/act_123456/adcreatives")
    assert preview["body"]["name"] == "Video ad creative"
    assert preview["body"]["object_story_spec"]["video_data"]["video_id"] == "video-123"
    assert preview["body"]["object_story_spec"]["video_data"]["call_to_action"] == {
        "type": "LEARN_MORE",
        "value": {"link": "https://example.com"},
    }


def test_publish_service_ad_preview_body_does_not_self_reference() -> None:
    service = PublishService()
    prepared = {
        "media_type": "text",
        "ad_account_id": "123456",
        "message": "Launch copy",
    }

    preview = service._build_meta_request_preview(PublishChannel.FACEBOOK_AD, prepared)
    prepared["meta_request"] = preview

    assert preview["body"] is not prepared
    assert preview["endpoint"].endswith("/act_123456/adcreatives")
    assert json.dumps(prepared)


@pytest.mark.asyncio
async def test_facebook_token_manager_resolves_env_token_refs() -> None:
    manager = FacebookTokenManager(
        Settings(
            facebook_page_access_token="page-token",
            facebook_ad_access_token="ad-token",
        )
    )

    assert await manager.resolve_token(FACEBOOK_PAGE_TOKEN_REF) == "page-token"
    assert await manager.resolve_token(FACEBOOK_AD_TOKEN_REF) == "ad-token"
    assert await manager.resolve_token("raw:test-token") == "test-token"


def test_publish_service_meta_config_reports_presence_without_tokens() -> None:
    service = PublishService()
    service.settings = Settings(
        facebook_dry_run=True,
        facebook_app_id="app-123",
        facebook_app_secret="app-secret",
        facebook_page_id="page-123",
        facebook_page_access_token="page-token",
        facebook_ad_account_id="act_123456",
        facebook_ad_access_token="ad-token",
    )

    config = service.get_meta_config()

    assert config["dry_run"] is True
    assert config["app"]["app_id"] == "app-123"
    assert config["app"]["app_secret_configured"] is True
    assert config["page"]["id"] == "page-123"
    assert config["page"]["access_token_configured"] is True
    assert config["page"]["access_token_ref"] == FACEBOOK_PAGE_TOKEN_REF
    assert config["ads"]["ad_account_id"] == "act_123456"
    assert config["ads"]["access_token_configured"] is True
    assert "app-secret" not in json.dumps(config)
    assert "page-token" not in json.dumps(config)
    assert "ad-token" not in json.dumps(config)
