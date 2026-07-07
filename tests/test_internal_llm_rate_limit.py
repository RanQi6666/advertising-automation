from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from backend.app.core.config import get_settings
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.enums import TopicStatus
from backend.app.db.models.topic import ContentTopic
from backend.app.schemas.ai import (
    CopyDraftCandidate,
    TopicCandidate,
    VideoStoryboardCandidate,
    VideoStoryboardScene,
)
from backend.app.schemas.copywriting import CopyGenerateRequest, CopyReviseRequest
from backend.app.schemas.topic import TopicGenerateRequest
from backend.app.schemas.video import (
    VideoStoryboardGenerateRequest,
    VideoStoryboardRewriteRequest,
)
from backend.app.services import copywriting_service as copywriting_module
from backend.app.services import topic_service as topic_module
from backend.app.services import video_service as video_module
from backend.app.services import work_order_service as work_order_module
from backend.app.services.copywriting_service import CopywritingService
from backend.app.services.topic_service import TopicService
from backend.app.services.video_service import VideoService
from backend.app.services.work_order_service import WorkOrderService


@pytest.fixture(autouse=True)
def internal_llm_rate_limit_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    get_settings.cache_clear()
    yield
    work_order_module._DELIVERY_EXTRACTION_CACHE.clear()
    get_settings.cache_clear()


class RecordingLimiter:
    def __init__(self) -> None:
        self.entered = 0
        self.exited = 0
        self.active = 0

    async def __aenter__(self):
        self.entered += 1
        self.active += 1
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback
        self.exited += 1
        self.active -= 1


class FakeInternalLLM:
    def __init__(self, limiter: RecordingLimiter) -> None:
        self.limiter = limiter
        self.active_states: list[int] = []

    def _record_active_state(self) -> None:
        self.active_states.append(self.limiter.active)

    async def generate_topics(
        self,
        campaign: Campaign,
        limit: int,
        signals: dict,
    ) -> list[TopicCandidate]:
        del campaign, signals
        self._record_active_state()
        return [_topic_candidate(index) for index in range(1, limit + 1)]

    async def stream_topics(
        self,
        campaign: Campaign,
        limit: int,
        signals: dict,
    ) -> AsyncIterator[TopicCandidate]:
        del campaign, signals
        self._record_active_state()
        yield _topic_candidate(1)
        self._record_active_state()
        if limit > 1:
            yield _topic_candidate(2)

    async def generate_copy(
        self,
        campaign: Campaign,
        topic: ContentTopic,
        constraints: dict,
    ) -> CopyDraftCandidate:
        del campaign, topic, constraints
        self._record_active_state()
        return _copy_candidate()

    async def revise_copy(
        self,
        campaign: Campaign,
        topic: ContentTopic,
        draft: CopyDraft,
        feedback: str,
        constraints: dict,
    ) -> CopyDraftCandidate:
        del campaign, topic, draft, feedback, constraints
        self._record_active_state()
        return _copy_candidate(body="Revised body")

    async def generate_video_storyboard(
        self,
        campaign: Campaign,
        draft: CopyDraft | None,
        assets: list,
        duration_seconds: int,
        aspect_ratio: str,
        context: dict,
        instructions: str | None = None,
    ) -> VideoStoryboardCandidate:
        del campaign, draft, assets, context, instructions
        self._record_active_state()
        return _storyboard_candidate(duration_seconds, aspect_ratio)

    async def revise_video_storyboard(
        self,
        campaign: Campaign,
        draft: CopyDraft | None,
        assets: list,
        duration_seconds: int,
        aspect_ratio: str,
        context: dict,
        current_storyboard: list[dict],
        current_storyboard_text: str | None,
        feedback: str,
    ) -> VideoStoryboardCandidate:
        del campaign, draft, assets, context, current_storyboard, current_storyboard_text, feedback
        self._record_active_state()
        return _storyboard_candidate(duration_seconds, aspect_ratio)

    async def stream_video_storyboard_text(
        self,
        **_: object,
    ) -> AsyncIterator[str]:
        self._record_active_state()
        yield "Scene 1"
        self._record_active_state()

    async def stream_video_storyboard_revision_text(
        self,
        **_: object,
    ) -> AsyncIterator[str]:
        self._record_active_state()
        yield "Revised scene"
        self._record_active_state()


class FakeDeliveryProvider:
    model = "gateway-text-model"

    def __init__(self, limiter: RecordingLimiter) -> None:
        self.limiter = limiter
        self.calls = 0
        self.active_states: list[int] = []

    async def extract_delivery_fields(self, raw_content: str) -> dict:
        assert raw_content
        self.calls += 1
        self.active_states.append(self.limiter.active)
        return {
            "schema_version": "ad_delivery_extract_v1",
            "fields": {
                "landing_url": {"value": None, "status": "missing"},
                "event_name": {
                    "value": "purchase",
                    "normalized_value": "purchase",
                    "status": "suggested",
                },
                "country": {"value": None, "status": "missing"},
            },
            "review": {},
        }


class FakeLandingPages:
    async def get_latest_snapshot(self, *_: object, **__: object):
        return None


class FakeScalars:
    def __init__(self, items: list[object]) -> None:
        self.items = items

    def all(self) -> list[object]:
        return self.items


class FakeResult:
    def __init__(self, items: list[object]) -> None:
        self.items = items

    def scalars(self) -> FakeScalars:
        return FakeScalars(self.items)


class FakeSession:
    def __init__(
        self,
        objects: dict[tuple[type, str], object],
        execute_results: list[list[object]] | None = None,
    ) -> None:
        self.objects = objects
        self.execute_results = execute_results or []
        self.added: list[object] = []
        self.commits = 0

    async def get(self, model: type, object_id: str):
        return self.objects.get((model, object_id))

    async def execute(self, *_: object, **__: object) -> FakeResult:
        items = self.execute_results.pop(0) if self.execute_results else []
        return FakeResult(items)

    def add(self, item: object) -> None:
        self.added.append(item)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, item: object) -> None:
        if getattr(item, "id", None) is None:
            item.id = f"generated-{len(self.added)}"
        if isinstance(item, ContentTopic) and getattr(item, "status", None) is None:
            item.status = TopicStatus.PROPOSED.value
        now = datetime.now(UTC)
        if getattr(item, "created_at", None) is None:
            item.created_at = now
        if getattr(item, "updated_at", None) is None:
            item.updated_at = now


@pytest.mark.asyncio
async def test_topic_service_enters_limiter_for_generate_and_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limiter = RecordingLimiter()
    provider = FakeInternalLLM(limiter)
    monkeypatch.setattr(topic_module, "llm_text_rate_limiter", lambda: limiter, raising=False)
    monkeypatch.setattr(topic_module, "get_llm_provider", lambda settings=None: provider)
    service = TopicService()
    service.landing_pages = FakeLandingPages()
    campaign = _campaign()

    await service.generate_topics(
        FakeSession({(Campaign, campaign.id): campaign}),
        TopicGenerateRequest(campaign_id=campaign.id, limit=1),
    )
    stream_events = [
        event
        async for event in service.stream_topics(
            FakeSession({(Campaign, campaign.id): campaign}),
            TopicGenerateRequest(campaign_id=campaign.id, limit=1),
        )
    ]

    assert limiter.entered == 2
    assert limiter.exited == 2
    assert all(active == 1 for active in provider.active_states)
    assert any(event["type"] == "topic" for event in stream_events)


@pytest.mark.asyncio
async def test_copywriting_service_enters_limiter_for_generate_and_revise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limiter = RecordingLimiter()
    provider = FakeInternalLLM(limiter)
    monkeypatch.setattr(
        copywriting_module,
        "llm_text_rate_limiter",
        lambda: limiter,
        raising=False,
    )
    monkeypatch.setattr(copywriting_module, "get_llm_provider", lambda settings=None: provider)
    service = CopywritingService()
    service.landing_pages = FakeLandingPages()
    campaign = _campaign()
    topic = _topic(campaign.id)
    draft = _draft(campaign.id, topic.id)
    session = FakeSession(
        {
            (Campaign, campaign.id): campaign,
            (ContentTopic, topic.id): topic,
            (CopyDraft, draft.id): draft,
        }
    )

    await service.generate_copy(session, CopyGenerateRequest(topic_id=topic.id))
    await service.revise_copy(
        session,
        draft.id,
        CopyReviseRequest(feedback="Make it shorter."),
    )

    assert limiter.entered == 2
    assert limiter.exited == 2
    assert all(active == 1 for active in provider.active_states)


@pytest.mark.asyncio
async def test_video_service_enters_limiter_for_storyboards_and_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limiter = RecordingLimiter()
    provider = FakeInternalLLM(limiter)
    monkeypatch.setattr(video_module, "llm_text_rate_limiter", lambda: limiter, raising=False)
    monkeypatch.setattr(video_module, "get_llm_provider", lambda settings=None: provider)
    service = VideoService()
    service.landing_pages = FakeLandingPages()
    campaign = _campaign()
    draft = _draft(campaign.id, "topic-1")
    session = FakeSession(
        {
            (Campaign, campaign.id): campaign,
            (CopyDraft, draft.id): draft,
        },
        execute_results=[[], [], [], []],
    )
    generate_payload = VideoStoryboardGenerateRequest(
        campaign_id=campaign.id,
        draft_id=draft.id,
    )
    rewrite_payload = VideoStoryboardRewriteRequest(
        campaign_id=campaign.id,
        draft_id=draft.id,
        storyboard=[{"scene_index": 1, "visual": "Old scene"}],
        feedback="Make it clearer.",
    )

    await service.generate_storyboard(session, generate_payload)
    await service.rewrite_storyboard(session, rewrite_payload)
    stream_events = [
        event async for event in service.stream_storyboard_text(session, generate_payload)
    ]
    rewrite_stream_events = [
        event async for event in service.stream_rewrite_storyboard_text(session, rewrite_payload)
    ]

    assert limiter.entered == 4
    assert limiter.exited == 4
    assert all(active == 1 for active in provider.active_states)
    assert any(event["type"] == "delta" for event in stream_events)
    assert any(event["type"] == "delta" for event in rewrite_stream_events)


@pytest.mark.asyncio
async def test_work_order_delivery_extraction_enters_limiter_only_on_llm_cache_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limiter = RecordingLimiter()
    provider = FakeDeliveryProvider(limiter)
    monkeypatch.setattr(
        work_order_module,
        "llm_text_rate_limiter",
        lambda: limiter,
        raising=False,
    )
    monkeypatch.setattr(work_order_module, "get_llm_provider", lambda: provider)
    raw_content = "Need a campaign brief, but no landing URL or country yet."
    service = WorkOrderService()

    await service.extract_delivery_fields(raw_content)
    await service.extract_delivery_fields(raw_content)

    assert limiter.entered == 1
    assert limiter.exited == 1
    assert provider.calls == 1
    assert provider.active_states == [1]


def _campaign() -> Campaign:
    return Campaign(
        id="campaign-1",
        name="Campaign",
        objective="purchase",
        product_name="Product",
        audience_description="Audience",
        metadata_json={},
    )


def _topic(campaign_id: str) -> ContentTopic:
    return ContentTopic(
        id="topic-1",
        campaign_id=campaign_id,
        title="Topic",
        angle="Angle",
        selling_points=[],
        status=TopicStatus.PROPOSED.value,
        source_data={},
    )


def _draft(campaign_id: str, topic_id: str) -> CopyDraft:
    return CopyDraft(
        id="draft-1",
        campaign_id=campaign_id,
        topic_id=topic_id,
        body="Body",
        primary_text="Body",
        headline="Headline",
        description="Description",
        cta="LEARN_MORE",
        version=1,
        metadata_json={},
    )


def _topic_candidate(index: int) -> TopicCandidate:
    return TopicCandidate(
        title=f"Topic {index}",
        angle="Angle",
        audience="Audience",
        selling_points=["Point"],
        rationale="Reason",
    )


def _copy_candidate(body: str = "Body") -> CopyDraftCandidate:
    return CopyDraftCandidate(
        body=body,
        primary_text=body,
        headline="Headline",
        description="Description",
        cta="LEARN_MORE",
    )


def _storyboard_candidate(duration_seconds: int, aspect_ratio: str) -> VideoStoryboardCandidate:
    return VideoStoryboardCandidate(
        duration_seconds=duration_seconds,
        aspect_ratio=aspect_ratio,
        scenes=[
            VideoStoryboardScene(
                scene_index=1,
                visual="Show product.",
                subtitle="Start now",
                motion="Push in",
                voiceover="Try it today.",
            )
        ],
    )
