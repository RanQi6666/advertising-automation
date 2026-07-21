from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.db.base import Base, utcnow
from backend.app.schemas.ad_research import AdResearchCreateRequest
from backend.app.services.ad_research_service import (
    AdResearchIdempotencyConflict,
    AdResearchResultExpired,
    AdResearchService,
    request_fingerprint,
)


def test_request_fingerprint_is_insensitive_to_keyword_order_and_whitespace() -> None:
    assert request_fingerprint("IN", " gambling ", ["Rummy", "casino"], 25) == request_fingerprint(
        "in", "gambling", [" casino ", "rummy"], 25
    )


@pytest.mark.asyncio
async def test_external_user_id_is_reusable_after_expiry() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    payload = AdResearchCreateRequest(
        external_user_id="research-1", country="in", category="gambling", keywords=["rummy"]
    )
    service = AdResearchService()
    async with factory() as session:
        created = await service.create_job(session, payload)
        replay = await service.create_job(session, payload)
        assert replay.idempotent_replay and replay.job.id == created.job.id
        with pytest.raises(AdResearchIdempotencyConflict):
            await service.create_job(
                session,
                AdResearchCreateRequest(
                    external_user_id="research-1", country="IN", category="games"
                ),
            )
        created.job.status = "completed"
        created.job.result_json = {"ads": [{"ad_library_id": "x"}]}
        created.job.result_expires_at = utcnow() - timedelta(seconds=1)
        await session.commit()
        with pytest.raises(AdResearchResultExpired):
            await service.get_job(session, created.job.id)
        fresh = await service.create_job(session, payload)
        assert fresh.job.id != created.job.id
    await engine.dispose()
