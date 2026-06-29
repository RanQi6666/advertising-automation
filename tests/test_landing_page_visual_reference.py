from backend.app.db.models.campaign import Campaign
from backend.app.services.landing_page_service import LandingPageService, snapshot_to_context


class FakeResponse:
    is_error = False
    status_code = 200
    text = (
        "<html><head><title>GAJA777</title>"
        '<meta name="description" content="Premium game lobby">'
        "</head><body><h1>GAJA777</h1></body></html>"
    )
    url = "https://www.gaja777.game/#/?invite=YBG71118&register=true"


class FakeAsyncClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str):
        return FakeResponse()


async def test_fetch_snapshot_does_not_store_gaja_visual_reference(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.app.services.landing_page_service.httpx.AsyncClient",
        FakeAsyncClient,
    )
    campaign = Campaign(
        id="campaign-1",
        name="GAJA777",
        product_name="GAJA777",
        metadata_json={},
    )

    snapshot = await LandingPageService()._fetch_snapshot(
        campaign=campaign,
        url="https://www.gaja777.game/#/?invite=YBG71118&register=true",
        metadata={"reference_images": ["C:/temp/gaja-reference.png"]},
    )

    assert "visual_reference" not in snapshot.extracted_data
    assert "visual_reference" not in snapshot_to_context(snapshot)["extracted_data"]
