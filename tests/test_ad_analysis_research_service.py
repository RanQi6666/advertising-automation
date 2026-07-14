import pytest

from backend.app.core.config import get_settings
from backend.app.services.ad_analysis_research_service import AdAnalysisResearchService


def _payload() -> dict:
    return {
        "campaign": {"name": "Puzzle Battle US", "objective": "OUTCOME_TRAFFIC"},
        "adset": {
            "name": "US Android gamers",
            "optimization_goal": "LINK_CLICKS",
            "countries": ["US"],
        },
        "creative": {
            "creative_type": "video",
            "name": "Beat my 3 minute record",
            "message": "My record: 3 minutes. Can you beat it?",
            "video_url": "https://newpixel.messrocts.com/uploads/video.mp4",
        },
        "insight": {"spend": "0.24"},
    }


class _FakeSearchProvider:
    def __init__(self, references=None, exc: Exception | None = None):
        self.references = references or []
        self.exc = exc
        self.query_profiles = []

    async def search(self, query_profile: dict):
        self.query_profiles.append(query_profile)
        if self.exc is not None:
            raise self.exc
        return self.references


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_public_research_runtime_limits_are_declared(monkeypatch):
    monkeypatch.setenv("AD_ANALYSIS_PUBLIC_RESEARCH_TIMEOUT_SECONDS", "4.5")
    monkeypatch.setenv("AD_ANALYSIS_PUBLIC_RESEARCH_MAX_RESULTS", "2")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.ad_analysis_public_research_timeout_seconds == 4.5
    assert settings.ad_analysis_public_research_max_results == 2


@pytest.mark.asyncio
async def test_public_research_is_enabled_by_default_for_complete_external_analysis():
    provider = _FakeSearchProvider(
        references=[
            {
                "source_url": "https://www.facebook.com/ads/library/?id=default-enabled",
                "title": "Similar public Facebook ad",
                "snippet": "Public creative reference for a similar ad.",
            }
        ]
    )

    summary = await AdAnalysisResearchService(search_provider=provider).research(_payload())

    assert summary["status"] == "succeeded"
    assert provider.query_profiles
    assert summary["selected_reference_ads"][0]["performance_evidence"]["verified"] is False


@pytest.mark.asyncio
async def test_public_research_enabled_uses_search_provider_and_normalizes_proxy_evidence(
    monkeypatch,
):
    monkeypatch.setenv("AD_ANALYSIS_PUBLIC_RESEARCH_ENABLED", "true")
    get_settings.cache_clear()
    provider = _FakeSearchProvider(
        references=[
            {
                "reference_id": "raw-1",
                "source_type": "web_search",
                "source_url": "https://www.facebook.com/ads/library/?id=123",
                "advertiser_name": "Puzzle Rival",
                "similarity_score": 4.2,
                "performance_evidence": {
                    "type": "public_proxy_signals",
                    "verified": True,
                    "confidence": "high",
                    "signals": ["public search result mentions playable puzzle ad"],
                },
            }
        ]
    )

    summary = await AdAnalysisResearchService(search_provider=provider).research(_payload())

    assert summary["status"] == "succeeded"
    assert summary["provider"] == "duckduckgo_html"
    assert provider.query_profiles[0]["platform"] == "facebook"
    assert "Beat my 3 minute record" in provider.query_profiles[0]["terms"]
    reference = summary["selected_reference_ads"][0]
    assert reference["source_url"] == "https://www.facebook.com/ads/library/?id=123"
    assert reference["similarity_score"] == 1.0
    assert reference["performance_evidence"]["verified"] is False
    assert reference["performance_evidence"]["type"] == "public_proxy_signals"
    assert reference["performance_evidence"]["limitations"]


@pytest.mark.asyncio
async def test_public_research_network_failure_degrades_without_failing_job(monkeypatch):
    monkeypatch.setenv("PUBLIC_RESEARCH_ENABLED", "true")
    get_settings.cache_clear()
    provider = _FakeSearchProvider(exc=TimeoutError("search timed out"))

    summary = await AdAnalysisResearchService(search_provider=provider).research(_payload())

    assert summary["status"] == "unavailable"
    assert summary["selected_reference_ads"] == []
    assert any("search timed out" in warning for warning in summary["warnings"])
    assert any("cannot verify CTR" in limitation for limitation in summary["limitations"])


@pytest.mark.asyncio
async def test_public_research_discards_malformed_facebook_ads_library_urls():
    provider = _FakeSearchProvider(
        references=[
            {
                "source_url": "https://www.facebook.com/ads/library/))/",
                "title": "Malformed search result",
                "snippet": "HTML residue leaked into the URL.",
            },
            {
                "source_url": "https://www.facebook.com/ads/library/?id=valid-reference",
                "title": "Valid public Facebook ad",
                "snippet": "A usable public creative reference.",
            },
        ]
    )

    summary = await AdAnalysisResearchService(search_provider=provider).research(_payload())

    assert summary["status"] == "succeeded"
    assert [
        reference["source_url"] for reference in summary["selected_reference_ads"]
    ] == ["https://www.facebook.com/ads/library/?id=valid-reference"]
