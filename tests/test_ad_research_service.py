from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import get_settings
from backend.app.db.base import Base, utcnow
from backend.app.db.models.ad_research_job import AdResearchJob
from backend.app.schemas.ad_research import AdResearchCreateRequest
from backend.app.services.ad_research_media import AdResearchMediaInspector
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
async def test_create_job_recovers_idempotent_replay_after_unique_constraint_race() -> None:
    payload = AdResearchCreateRequest(
        external_user_id="research-race", country="IN", category="gambling", keywords=["rummy"]
    )
    existing = AdResearchJob(
        id="adr_existing",
        external_user_id=payload.external_user_id,
        request_fingerprint=request_fingerprint(
            payload.country, payload.category, payload.keywords, payload.target_count
        ),
        country="IN",
        category="gambling",
        seed_keywords_json=["rummy"],
        target_count=25,
        status="queued",
        stage="queued",
        progress_json={},
        summary_json={},
    )
    session = AsyncMock()
    session.add_all = Mock()
    session.scalar.side_effect = [None, existing]
    session.commit.side_effect = IntegrityError("insert", {}, Exception("duplicate key"))

    result = await AdResearchService().create_job(session, payload)

    assert result.idempotent_replay is True
    assert result.job is existing
    session.rollback.assert_awaited_once()


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


@pytest.mark.asyncio
async def test_failed_job_is_requeued_when_external_user_id_retries() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    payload = AdResearchCreateRequest(
        external_user_id="research-retry", country="IN", category="gambling", keywords=["rummy"]
    )
    service = AdResearchService()
    async with factory() as session:
        created = await service.create_job(session, payload)
        created.job.status = "failed"
        created.job.stage = "dispatch_failed"
        created.job.error_code = "queue_dispatch_failed"
        task = created.task
        assert task is not None
        task.status = "failed"
        await session.commit()

        retried = await service.create_job(session, payload)

        assert retried.idempotent_replay is False
        assert retried.job.id == created.job.id
        assert retried.job.status == "queued"
        assert retried.task is not None
        assert retried.task.status == "queued"
    await engine.dispose()


@pytest.mark.asyncio
async def test_expiring_result_deletes_selected_ad_research_media(tmp_path, monkeypatch) -> None:
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(storage_root))
    monkeypatch.setenv("AD_RESEARCH_MEDIA_ROOT", str(storage_root / "ad-research"))
    get_settings.cache_clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        service = AdResearchService(media=AdResearchMediaInspector())
        async with factory() as session:
            created = await service.create_job(
                session,
                AdResearchCreateRequest(
                    external_user_id="research-expire-media", country="IN", category="gambling"
                ),
            )
            artifact = storage_root / "ad-research" / created.job.id / "ad-1" / "cover.jpg"
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"image")
            created.job.status = "completed"
            created.job.result_json = {"ads": [{"ad_library_id": "ad-1"}]}
            created.job.result_expires_at = utcnow() - timedelta(seconds=1)
            await session.commit()

            assert await service.cleanup_expired_results(session) == 1
            assert not artifact.parent.parent.exists()
            assert created.job.status == "expired"
    finally:
        await engine.dispose()
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_failed_job_deletes_ad_research_media(tmp_path, monkeypatch) -> None:
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(storage_root))
    monkeypatch.setenv("AD_RESEARCH_MEDIA_ROOT", str(storage_root / "ad-research"))
    get_settings.cache_clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        service = AdResearchService(media=AdResearchMediaInspector())
        async with factory() as session:
            created = await service.create_job(
                session,
                AdResearchCreateRequest(
                    external_user_id="research-fail-media", country="IN", category="gambling"
                ),
            )
            artifact = storage_root / "ad-research" / created.job.id / "ad-1" / "cover.jpg"
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"image")

            await service.fail_job(session, created.job, RuntimeError("worker failed"))

            assert not artifact.parent.parent.exists()
            assert created.job.status == "failed"
    finally:
        await engine.dispose()
        get_settings.cache_clear()
