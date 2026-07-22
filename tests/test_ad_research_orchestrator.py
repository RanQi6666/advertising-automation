import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import get_settings
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


class SlowModel(Model):
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def classify(self, *, category, candidate):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            return await super().classify(category=category, candidate=candidate)
        finally:
            self.active -= 1


async def run(sizes: list[int], model: Model | None = None):
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
            collector=collector, media=Media(), model=model or Model(), service=service
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


@pytest.mark.asyncio
async def test_orchestrator_bounds_model_classification_concurrency(monkeypatch) -> None:
    monkeypatch.setenv("AD_RESEARCH_MODEL_CONCURRENCY", "2")
    get_settings.cache_clear()
    model = SlowModel()
    try:
        _, result = await run([25], model=model)
        assert result.status == "completed"
        assert model.max_active == 2
    finally:
        get_settings.cache_clear()
