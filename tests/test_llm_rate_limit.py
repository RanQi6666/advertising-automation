import asyncio
import time

import pytest

from backend.app.core.config import get_settings
from backend.app.core.errors import ProviderError
from backend.app.services import llm_rate_limit


class FakeRedis:
    def __init__(self) -> None:
        self.strings: dict[str, str] = {}
        self.zsets: dict[str, dict[str, float]] = {}

    async def eval(self, script: str, numkeys: int, *args):
        keys = list(args[:numkeys])
        argv = list(args[numkeys:])
        if "ZREMRANGEBYSCORE" in script:
            return self._eval_acquire(keys, argv)
        if "DECR" in script:
            return self._eval_release(keys)
        if "redis.call('GET', lock_key)" in script or 'redis.call("GET", lock_key)' in script:
            return self._eval_release_lock(keys, argv)
        raise AssertionError(f"Unexpected script: {script[:80]}")

    def _eval_acquire(self, keys: list[str], argv: list[object]):
        rpm_key, inflight_key = keys
        now_ms = float(argv[0])
        window_ms = float(argv[1])
        rpm_limit = int(argv[2])
        max_inflight = int(argv[3])
        member = str(argv[4])

        zset = self.zsets.setdefault(rpm_key, {})
        for name, score in list(zset.items()):
            if score <= now_ms - window_ms:
                zset.pop(name, None)
        rpm_count = len(zset)
        inflight = int(self.strings.get(inflight_key, "0"))
        if rpm_count < rpm_limit and inflight < max_inflight:
            zset[member] = now_ms
            inflight += 1
            self.strings[inflight_key] = str(inflight)
            return [1, 0, rpm_count + 1, inflight]
        return [0, 1, rpm_count, inflight]

    def _eval_release(self, keys: list[str]):
        inflight_key = keys[0]
        inflight = int(self.strings.get(inflight_key, "0"))
        if inflight <= 1:
            self.strings.pop(inflight_key, None)
            return 0
        inflight -= 1
        self.strings[inflight_key] = str(inflight)
        return inflight

    def _eval_release_lock(self, keys: list[str], argv: list[object]):
        key = keys[0]
        expected = str(argv[0])
        if self.strings.get(key) == expected:
            self.strings.pop(key, None)
            return 1
        return 0

    async def set(self, key: str, value: str, nx: bool = False, px: int | None = None, ex=None):
        del px, ex
        if nx and key in self.strings:
            return None
        self.strings[key] = value
        return True

    async def get(self, key: str):
        return self.strings.get(key)

    async def delete(self, key: str):
        self.strings.pop(key, None)
        return 1

    async def close(self) -> None:
        return None


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    redis = FakeRedis()
    monkeypatch.setenv("LLM_TEXT_RPM_LIMIT", "10")
    monkeypatch.setenv("LLM_TEXT_MAX_INFLIGHT", "1")
    get_settings.cache_clear()
    llm_rate_limit.set_redis_client_factory_for_tests(lambda _url: redis)
    yield redis
    llm_rate_limit.set_redis_client_factory_for_tests(None)
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_llm_text_rate_limiter_caps_global_inflight(fake_redis: FakeRedis) -> None:
    async with llm_rate_limit.llm_text_rate_limiter(
        acquire_timeout_seconds=0.01,
        poll_interval_seconds=0,
    ):
        with pytest.raises(ProviderError, match="rate limit"):
            async with llm_rate_limit.llm_text_rate_limiter(
                acquire_timeout_seconds=0.01,
                poll_interval_seconds=0,
            ):
                pass

    async with llm_rate_limit.llm_text_rate_limiter(
        acquire_timeout_seconds=0.01,
        poll_interval_seconds=0,
    ):
        assert int(fake_redis.strings["llm:text:inflight"]) == 1


@pytest.mark.asyncio
async def test_llm_text_rate_limiter_keeps_rpm_tokens_after_release(
    fake_redis: FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_TEXT_RPM_LIMIT", "1")
    monkeypatch.setenv("LLM_TEXT_MAX_INFLIGHT", "10")
    get_settings.cache_clear()

    async with llm_rate_limit.llm_text_rate_limiter(
        acquire_timeout_seconds=0.01,
        poll_interval_seconds=0,
    ):
        pass

    with pytest.raises(ProviderError, match="rate limit"):
        async with llm_rate_limit.llm_text_rate_limiter(
            acquire_timeout_seconds=0.01,
            poll_interval_seconds=0,
        ):
            pass

    assert len(fake_redis.zsets["llm:text:rpm"]) == 1


@pytest.mark.asyncio
async def test_external_ai_idempotency_lock_uses_set_nx(fake_redis: FakeRedis) -> None:
    first = llm_rate_limit.ExternalAIIdempotencyLock(
        "external_copy_generation",
        "same-request",
        ttl_ms=1000,
    )
    second = llm_rate_limit.ExternalAIIdempotencyLock(
        "external_copy_generation",
        "same-request",
        ttl_ms=1000,
    )

    async with first as first_acquired:
        async with second as second_acquired:
            assert first_acquired is True
            assert second_acquired is False

    async with second as acquired_after_release:
        assert acquired_after_release is True


@pytest.mark.asyncio
async def test_rate_limiter_does_not_block_event_loop(fake_redis: FakeRedis) -> None:
    started = time.perf_counter()
    async with llm_rate_limit.llm_text_rate_limiter(
        acquire_timeout_seconds=0.01,
        poll_interval_seconds=0,
    ):
        await asyncio.sleep(0)
    assert time.perf_counter() - started < 0.5
