import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import ProviderError
from backend.app.db.models.generation_task import GenerationTask

logger = logging.getLogger(__name__)

LLM_TEXT_RPM_KEY = "llm:text:rpm"
LLM_TEXT_INFLIGHT_KEY = "llm:text:inflight"
GENERATION_TASK_STATUS_CACHE_PREFIX = "generation_task:status:"
EXTERNAL_AI_IDEMPOTENCY_PREFIX = "ai:idem:"
_TOKEN_WINDOW_MS = 60_000
_REDIS_KEY_TTL_SECONDS = 180
_IDEMPOTENCY_LOCK_TTL_MS = 10_000

_redis_client_factory_for_tests: Callable[[str], object] | None = None

_ACQUIRE_SCRIPT = """
local rpm_key = KEYS[1]
local inflight_key = KEYS[2]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local rpm_limit = tonumber(ARGV[3])
local max_inflight = tonumber(ARGV[4])
local member = ARGV[5]
local ttl_seconds = tonumber(ARGV[6])

redis.call('ZREMRANGEBYSCORE', rpm_key, 0, now_ms - window_ms)
local rpm_count = redis.call('ZCARD', rpm_key)
local inflight = tonumber(redis.call('GET', inflight_key) or '0')

if rpm_count < rpm_limit and inflight < max_inflight then
    redis.call('ZADD', rpm_key, now_ms, member)
    redis.call('EXPIRE', rpm_key, ttl_seconds)
    redis.call('INCR', inflight_key)
    redis.call('EXPIRE', inflight_key, ttl_seconds)
    return {1, 0, rpm_count + 1, inflight + 1}
end

local wait_ms = 250
if rpm_count >= rpm_limit then
    local oldest = redis.call('ZRANGE', rpm_key, 0, 0, 'WITHSCORES')
    if oldest[2] then
        local rpm_wait_ms = tonumber(oldest[2]) + window_ms - now_ms + 1
        if rpm_wait_ms > wait_ms then
            wait_ms = rpm_wait_ms
        end
    end
end

if inflight >= max_inflight and wait_ms < 1000 then
    wait_ms = 1000
end

return {0, wait_ms, rpm_count, inflight}
"""

_RELEASE_INFLIGHT_SCRIPT = """
local inflight_key = KEYS[1]
local inflight = tonumber(redis.call('GET', inflight_key) or '0')
if inflight <= 1 then
    redis.call('DEL', inflight_key)
    return 0
end
return redis.call('DECR', inflight_key)
"""

_RELEASE_LOCK_SCRIPT = """
local lock_key = KEYS[1]
local owner = ARGV[1]
if redis.call('GET', lock_key) == owner then
    return redis.call('DEL', lock_key)
end
return 0
"""


def set_redis_client_factory_for_tests(factory: Callable[[str], object] | None) -> None:
    global _redis_client_factory_for_tests
    _redis_client_factory_for_tests = factory


@asynccontextmanager
async def llm_text_rate_limiter(
    *,
    acquire_timeout_seconds: float = 30.0,
    poll_interval_seconds: float | None = None,
) -> AsyncIterator[None]:
    settings = get_settings()
    client = _make_redis_client(settings)
    member = str(uuid4())
    acquired = False
    deadline = time.monotonic() + max(acquire_timeout_seconds, 0.001)
    try:
        while True:
            wait_ms = await _try_acquire_text_token(client, settings, member)
            if wait_ms is None:
                acquired = True
                break
            if time.monotonic() >= deadline:
                raise ProviderError("LLM text rate limit wait timeout")
            sleep_seconds = (
                min(max(wait_ms / 1000, 0.001), 1.0)
                if poll_interval_seconds is None
                else max(poll_interval_seconds, 0)
            )
            await asyncio.sleep(sleep_seconds)
        yield
    finally:
        if acquired:
            await _release_text_inflight(client)
        await _close_redis_client(client)


class ExternalAIIdempotencyLock:
    def __init__(
        self,
        task_type: str,
        external_request_id: str,
        *,
        ttl_ms: int = _IDEMPOTENCY_LOCK_TTL_MS,
    ) -> None:
        self.key = f"{EXTERNAL_AI_IDEMPOTENCY_PREFIX}{task_type}:{external_request_id}"
        self.owner = str(uuid4())
        self.ttl_ms = ttl_ms
        self.client: object | None = None
        self.acquired = False

    async def __aenter__(self) -> bool:
        self.client = _make_redis_client(get_settings())
        try:
            result = await self.client.set(self.key, self.owner, nx=True, px=self.ttl_ms)
            self.acquired = bool(result)
            return self.acquired
        except Exception as exc:
            await _close_redis_client(self.client)
            self.client = None
            raise ProviderError(f"External AI idempotency lock unavailable: {exc}") from exc

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        if self.client is None:
            return
        try:
            if self.acquired:
                await self.client.eval(_RELEASE_LOCK_SCRIPT, 1, self.key, self.owner)
        finally:
            await _close_redis_client(self.client)


async def cache_generation_task_terminal_status(task: GenerationTask) -> None:
    if task.status not in {"succeeded", "failed"}:
        return
    settings = get_settings()
    client = _make_redis_client(settings)
    try:
        payload = {
            "job_id": task.id,
            "task_type": task.task_type,
            "status": task.status,
            "result_json": task.result_json or {},
            "error_message": task.error_message,
        }
        await client.set(
            _task_status_cache_key(task.id),
            json.dumps(payload, ensure_ascii=False, default=str),
            ex=settings.job_status_cache_ttl_seconds,
        )
    except Exception:
        logger.exception("Failed to cache generation task terminal status.")
    finally:
        await _close_redis_client(client)


async def get_cached_generation_task_status(job_id: str) -> dict | None:
    client = _make_redis_client(get_settings())
    try:
        raw = await client.get(_task_status_cache_key(job_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(str(raw))
        return data if isinstance(data, dict) else None
    except Exception:
        logger.exception("Failed to read generation task terminal status cache.")
        return None
    finally:
        await _close_redis_client(client)


def _make_redis_client(settings: Settings) -> object:
    if _redis_client_factory_for_tests is not None:
        return _redis_client_factory_for_tests(settings.redis_url)
    import redis.asyncio as redis

    return redis.Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=5,
    )


async def _try_acquire_text_token(client: object, settings: Settings, member: str) -> int | None:
    try:
        result = await client.eval(
            _ACQUIRE_SCRIPT,
            2,
            LLM_TEXT_RPM_KEY,
            LLM_TEXT_INFLIGHT_KEY,
            int(time.time() * 1000),
            _TOKEN_WINDOW_MS,
            settings.llm_text_rpm_limit,
            settings.llm_text_max_inflight,
            member,
            _REDIS_KEY_TTL_SECONDS,
        )
    except Exception as exc:
        raise ProviderError(f"LLM text rate limit unavailable: {exc}") from exc
    acquired = bool(int(result[0]))
    if acquired:
        return None
    return max(int(result[1]), 1)


async def _release_text_inflight(client: object) -> None:
    try:
        await client.eval(_RELEASE_INFLIGHT_SCRIPT, 1, LLM_TEXT_INFLIGHT_KEY)
    except Exception:
        logger.exception("Failed to release LLM text inflight token.")


async def _close_redis_client(client: object) -> None:
    close = getattr(client, "aclose", None) or getattr(client, "close", None)
    if close is None:
        return
    result = close()
    if asyncio.iscoroutine(result):
        await result


def _task_status_cache_key(job_id: str) -> str:
    return f"{GENERATION_TASK_STATUS_CACHE_PREFIX}{job_id}"
