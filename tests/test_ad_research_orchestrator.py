import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.db.base import Base
from backend.app.schemas.ad_research import AdResearchCreateRequest, CollectorAd
from backend.app.services.ad_research_orchestrator import AdResearchOrchestrator
from backend.app.services.ad_research_service import AdResearchService


def candidate(index: int) -> CollectorAd:
    return CollectorAd(
        ad_library_id=f"ad-{index}",
        advertiser_name="Example",
        status="ACTIVE",
        days_running=3,
        text_variants=["matching creative"],
        video_url=f"https://cdn.example/{index}.mp4",
        thumbnail_url=f"https://cdn.example/{index}.jpg",
        duration_seconds=20,
    )


class Collector:
    def __init__(self, sizes: list[int]) -> None:
        self.sizes = sizes
        self.calls = 0

    async def collect(self, **kwargs):
        self.calls += 1
        start = sum(self.sizes[: self.calls - 1])
        return [candidate(index) for index in range(start, start + self.sizes[self.calls - 1])]


class Media:
    async def is_technically_qualified(self, item):
        return True


class Model:
    async def plan_queries(self, **kwargs):
        return ["query"]

    async def classify(self, *, category, candidate):
        return {
            "category_match": True,
            "category_confidence": 0.9,
            "business_type": category,
            "creative_relevance_score": 80,
            "public_performance_signal_score": 70,
            "real_money_signal_score": 0.0,
            "is_obviously_unrelated": False,
            "text_evidence": [],
            "visual_evidence": [],
            "public_signal_evidence": [],
            "public_risk_signals": [],
            "recommendation": "keep",
        }


async def run(sizes: list[int]):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        service = AdResearchService()
        created = await service.create_job(
            session,
            AdResearchCreateRequest(
                external_user_id=f"job-{sizes}", country="IN", category="gambling", target_count=25
            ),
        )
        collector = Collector(sizes)
        result = await AdResearchOrchestrator(
            collector=collector, media=Media(), model=Model(), service=service
        ).run(session, created.job)
    await engine.dispose()
    return collector, result


@pytest.mark.asyncio
async def test_orchestrator_collects_again_when_first_round_is_short() -> None:
    collector, result = await run([10, 15])
    assert collector.calls == 2
    assert result.status == "completed"
    assert len(result.ads) == 25


@pytest.mark.asyncio
async def test_orchestrator_returns_insufficient_without_padding() -> None:
    _, result = await run([5, 0, 0, 0])
    assert result.status == "insufficient"
    assert len(result.ads) == 5
    assert result.summary["reason"] == "insufficient_qualified_ads"
