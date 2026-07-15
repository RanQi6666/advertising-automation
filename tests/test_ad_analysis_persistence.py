from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.db.base import Base
from backend.app.db.models.ad_analysis_reference_ad import AdAnalysisReferenceAd
from backend.app.db.models.ad_performance_analysis import AdPerformanceAnalysis
from backend.app.db.models.generation_task import GenerationTask
from backend.app.services.ad_analysis_media_service import MediaProcessingResult
from backend.app.services.ad_analysis_research_service import PublicResearchResult
from backend.app.services.external_ad_performance_analysis_service import (
    ExternalAdPerformanceAnalysisService,
)


@pytest.mark.asyncio
async def test_async_analysis_columns_and_reference_ads_persist(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'models.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        columns = await connection.run_sync(
            lambda sync_connection: {
                column["name"]
                for column in inspect(sync_connection).get_columns("ad_performance_analyses")
            }
        )
    assert {
        "analysis_id",
        "external_request_id",
        "payload_hash",
        "normalized_payload",
        "stage",
        "progress",
        "analysis_scope",
        "result_schema_version",
        "generation_task_id",
        "attempt_count",
        "max_attempts",
        "started_at",
        "completed_at",
        "error_code",
        "error_retryable",
        "media_summary",
        "research_summary",
        "dispatch_claimed_at",
        "last_dispatched_at",
        "dispatch_error",
    } <= columns

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        analysis = AdPerformanceAnalysis(
            analysis_id="ana_model_1",
            external_request_id="external-model-1",
            payload_hash="a" * 64,
            request_payload={"creative": {"creative_type": "image"}},
            normalized_payload={"creative": {"creative_type": "image"}},
            status="queued",
            stage="queued",
            progress=0,
            analysis_result={},
        )
        session.add(analysis)
        await session.flush()
        reference = AdAnalysisReferenceAd(
            analysis_record_id=analysis.id,
            reference_id="ref_001",
            source_type="meta_ad_library",
            source_url="https://www.facebook.com/ads/library/?id=1",
            source_domain="facebook.com",
            content_hash="b" * 64,
            similarity_score=0.87,
            performance_evidence_json={"type": "public_proxy_signals", "verified": False},
            creative_analysis_json={},
            raw_excerpt_json={},
        )
        session.add(reference)
        await session.commit()

    assert reference.analysis_record_id == analysis.id
    await engine.dispose()


@pytest.mark.asyncio
async def test_completed_job_persists_only_public_media_summary(monkeypatch, tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'job.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    payload = {
        "external_request_id": "external-private-media-1",
        "campaign": {"objective": "OUTCOME_TRAFFIC"},
        "adset": {"optimization_goal": "LINK_CLICKS"},
        "creative": {
            "creative_type": "image",
            "image_url": "https://newpixel.messrocts.com/uploads/ad.jpg",
        },
        "insight": {"spend": "1", "impressions": "100", "inline_link_clicks": "10"},
        "siblings": [],
    }
    private_summary = {
        "status": "available",
        "thumbnail_generated": True,
        "local_artifacts": {
            "downloaded_path": "/app/storage/ad-analysis/ana-private/source.jpg",
            "thumbnail_path": "/app/storage/ad-analysis/ana-private/thumbnail.jpg",
        },
        "warnings": [],
    }

    async def fake_process_media(self, _payload, *, analysis_id):
        return MediaProcessingResult(summary=private_summary)

    async def fake_research(self, session, analysis, payload, *, media_summary):
        assert "local_artifacts" not in analysis.media_summary
        assert media_summary["local_artifacts"]["thumbnail_path"].endswith("thumbnail.jpg")
        return PublicResearchResult(summary={"status": "skipped", "warnings": []})

    async def fake_cleanup(self, analysis_id):
        return None

    class FakeProvider:
        async def analyze_ad_performance(self, context):
            assert context["media_summary"]["local_artifacts"]["thumbnail_path"].endswith(
                "thumbnail.jpg"
            )
            return {}

    monkeypatch.setattr(
        "backend.app.services.external_ad_performance_analysis_service."
        "AdAnalysisMediaService.process_media",
        fake_process_media,
    )
    monkeypatch.setattr(
        "backend.app.services.external_ad_performance_analysis_service."
        "AdAnalysisMediaService.cleanup_analysis_media",
        fake_cleanup,
    )
    monkeypatch.setattr(
        "backend.app.services.external_ad_performance_analysis_service."
        "AdAnalysisResearchService.research_and_persist",
        fake_research,
    )
    monkeypatch.setattr(
        "backend.app.services.external_ad_performance_analysis_service.get_llm_provider",
        lambda: FakeProvider(),
    )

    async with session_factory() as session:
        task = GenerationTask(
            queue_name="ad_analysis_queue",
            task_type="ad_performance_analysis",
            business_type="ad_performance_analysis",
            business_id="ana_private",
            status="queued",
            priority=0,
            payload_json={"analysis_id": "ana_private"},
            retryable=False,
            attempt_count=0,
            max_attempts=2,
            queued_at=datetime.now(UTC),
            metadata_json={},
        )
        session.add(task)
        await session.flush()
        analysis = AdPerformanceAnalysis(
            analysis_id="ana_private",
            external_request_id="external-private-media-1",
            payload_hash="c" * 64,
            request_payload=payload,
            normalized_payload=payload,
            status="queued",
            stage="queued",
            progress=0,
            analysis_scope="facebook_ad_performance",
            generation_task_id=task.id,
            analysis_result={},
            media_summary={},
            research_summary={},
        )
        session.add(analysis)
        await session.commit()

        await ExternalAdPerformanceAnalysisService().execute_task(session, task)
        await session.refresh(analysis)

        assert analysis.status == "succeeded"
        assert analysis.media_summary["status"] == "available"
        assert "local_artifacts" not in analysis.media_summary
        assert "/app/storage" not in str(analysis.analysis_result)

    await engine.dispose()
