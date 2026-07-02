import pytest

from backend.app.core.config import get_settings
from backend.app.services.generation_runtime_monitor import GenerationRuntimeMonitor


class FakeRedisClient:
    def __init__(self, depths: dict[str, int]) -> None:
        self.depths = depths
        self.closed = False
        self.llen_count = 0

    def llen(self, queue_name: str) -> int:
        self.llen_count += 1
        return self.depths.get(queue_name, 0)

    def close(self) -> None:
        self.closed = True


class FakeInspect:
    def active_queues(self) -> dict[str, list[dict[str, str]]]:
        return {
            "text@worker": [{"name": "text_queue"}],
            "image@worker": [{"name": "image_queue"}],
            "video@worker": [{"name": "video_queue"}],
            "callback@worker": [{"name": "callback_queue"}],
        }

    def stats(self) -> dict[str, dict]:
        return {
            "text@worker": {"pool": {"max-concurrency": 6}},
            "image@worker": {"pool": {"max-concurrency": 4}},
            "video@worker": {"pool": {"max-concurrency": 4}},
            "callback@worker": {"pool": {"max-concurrency": 3}},
        }

    def active(self) -> dict[str, list[dict]]:
        return {
            "text@worker": [{}, {}],
            "image@worker": [{}],
            "video@worker": [],
            "callback@worker": [],
        }


class FakeControl:
    def __init__(self) -> None:
        self.timeout: float | None = None
        self.inspect_count = 0

    def inspect(self, timeout: float) -> FakeInspect:
        self.timeout = timeout
        self.inspect_count += 1
        return FakeInspect()


class FakeCeleryApp:
    def __init__(self) -> None:
        self.control = FakeControl()


@pytest.mark.asyncio
async def test_generation_runtime_monitor_reports_redis_depths_and_worker_health(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GENERATION_TASK_EXECUTION_BACKEND", "celery")
    get_settings.cache_clear()
    fake_redis = FakeRedisClient(
        {
            "text_queue": 5,
            "image_queue": 8,
            "video_queue": 1,
            "callback_queue": 0,
        }
    )
    fake_celery_app = FakeCeleryApp()

    monitor = GenerationRuntimeMonitor(
        redis_client_factory=lambda _url: fake_redis,
        celery_app=fake_celery_app,
    )

    summary = await monitor.runtime_summary(
        ["text_queue", "image_queue", "video_queue", "callback_queue"],
        {
            "text_queue": 6,
            "image_queue": 4,
            "video_queue": 4,
            "callback_queue": 3,
        },
    )

    assert summary["execution_backend"] == "celery"
    assert summary["redis_queues"]["status"] == "ok"
    assert summary["redis_queues"]["total_depth"] == 14
    assert summary["redis_queues"]["queues"]["image_queue"] == {
        "depth": 8,
        "concurrency": 4,
        "backlog": 4,
        "pressure_ratio": 2.0,
    }
    assert summary["worker_health"]["status"] == "ok"
    assert summary["worker_health"]["online_count"] == 4
    assert summary["worker_health"]["missing_queues"] == []
    assert summary["worker_health"]["total_active_tasks"] == 3
    assert summary["worker_health"]["workers"][0] == {
        "name": "callback@worker",
        "queues": ["callback_queue"],
        "concurrency": 3,
        "active_tasks": 0,
    }
    assert fake_redis.closed is True
    assert fake_celery_app.control.timeout == 1.0

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_generation_runtime_monitor_reuses_short_cache(monkeypatch) -> None:
    monkeypatch.setenv("GENERATION_TASK_EXECUTION_BACKEND", "celery")
    get_settings.cache_clear()
    fake_redis = FakeRedisClient({"text_queue": 1})
    fake_celery_app = FakeCeleryApp()
    monitor = GenerationRuntimeMonitor(
        redis_client_factory=lambda _url: fake_redis,
        celery_app=fake_celery_app,
    )

    first = await monitor.runtime_summary(["text_queue"], {"text_queue": 6})
    second = await monitor.runtime_summary(["text_queue"], {"text_queue": 6})

    assert first == second
    assert fake_redis.llen_count == 1
    assert fake_celery_app.control.inspect_count == 1

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_generation_runtime_monitor_degrades_without_redis_or_workers(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GENERATION_TASK_EXECUTION_BACKEND", "celery")
    get_settings.cache_clear()

    class BrokenRedisClient:
        def llen(self, queue_name: str) -> int:
            raise RuntimeError(f"{queue_name} unavailable")

        def close(self) -> None:
            return None

    class BrokenInspect:
        def active_queues(self) -> None:
            raise RuntimeError("inspect unavailable")

    class BrokenControl:
        def inspect(self, timeout: float) -> BrokenInspect:
            return BrokenInspect()

    class BrokenCeleryApp:
        control = BrokenControl()

    monitor = GenerationRuntimeMonitor(
        redis_client_factory=lambda _url: BrokenRedisClient(),
        celery_app=BrokenCeleryApp(),
    )

    summary = await monitor.runtime_summary(["text_queue"], {"text_queue": 6})

    assert summary["redis_queues"]["status"] == "unavailable"
    assert "unavailable" in summary["redis_queues"]["error"]
    assert summary["worker_health"]["status"] == "unavailable"
    assert "inspect unavailable" in summary["worker_health"]["error"]

    get_settings.cache_clear()
