from backend.app.services.ad_research_collector import normalize_collector_ad


def test_collector_normalizes_an_active_video_ad() -> None:
    result = normalize_collector_ad(
        {
            "id": "meta-ad-1",
            "status": "ACTIVE",
            "video_urls": ["https://cdn.example/ad.mp4"],
            "media_urls": ["https://cdn.example/cover.jpg"],
            "days_running": 3,
            "body_variants": ["example"],
        }
    )
    assert result["ad_library_id"] == "meta-ad-1"
    assert result["video_url"] == "https://cdn.example/ad.mp4"
