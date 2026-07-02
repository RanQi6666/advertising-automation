import asyncio
import copy
import time
from collections.abc import Callable, Sequence
from typing import Any

from backend.app.core.config import get_settings

_runtime_summary_cache_lock = asyncio.Lock()
_runtime_summary_cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}


class GenerationRuntimeMonitor:
    def __init__(
        self,
        *,
        redis_client_factory: Callable[[str], Any] | None = None,
        celery_app: Any | None = None,
    ) -> None:
        self._redis_client_factory = redis_client_factory
        self._celery_app = celery_app

    async def runtime_summary(
        self,
        queue_names: Sequence[str],
        queue_concurrency: dict[str, int],
    ) -> dict[str, Any]:
        normalized_queue_names = list(dict.fromkeys(queue_names))
        cache_key = _runtime_cache_key(
            normalized_queue_names,
            queue_concurrency,
            self._redis_client_factory,
            self._celery_app,
        )
        cached_summary = _get_runtime_cache(cache_key)
        if cached_summary is not None:
            return cached_summary

        async with _runtime_summary_cache_lock:
            cached_summary = _get_runtime_cache(cache_key)
            if cached_summary is not None:
                return cached_summary

            summary = await self._fresh_runtime_summary(
                normalized_queue_names,
                queue_concurrency,
            )
            _set_runtime_cache(cache_key, summary)
            return copy.deepcopy(summary)

    async def _fresh_runtime_summary(
        self,
        queue_names: Sequence[str],
        queue_concurrency: dict[str, int],
    ) -> dict[str, Any]:
        settings = get_settings()
        if settings.generation_task_execution_backend != "celery":
            return {
                "execution_backend": settings.generation_task_execution_backend,
                "redis_queues": _disabled_redis_summary(queue_names),
                "worker_health": _disabled_worker_summary(queue_names),
            }

        redis_queues, worker_health = await asyncio.gather(
            self._redis_queue_summary(queue_names, queue_concurrency),
            self._worker_health_summary(queue_names),
        )
        return {
            "execution_backend": settings.generation_task_execution_backend,
            "redis_queues": redis_queues,
            "worker_health": worker_health,
        }

    async def _redis_queue_summary(
        self,
        queue_names: Sequence[str],
        queue_concurrency: dict[str, int],
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._read_redis_queue_summary,
            queue_names,
            queue_concurrency,
        )

    def _read_redis_queue_summary(
        self,
        queue_names: Sequence[str],
        queue_concurrency: dict[str, int],
    ) -> dict[str, Any]:
        client = None
        try:
            client = self._make_redis_client()
            queues: dict[str, dict[str, Any]] = {}
            total_depth = 0
            for queue_name in queue_names:
                depth = max(int(client.llen(queue_name) or 0), 0)
                concurrency = max(int(queue_concurrency.get(queue_name, 1) or 1), 1)
                total_depth += depth
                queues[queue_name] = {
                    "depth": depth,
                    "concurrency": concurrency,
                    "backlog": max(depth - concurrency, 0),
                    "pressure_ratio": round(depth / concurrency, 2),
                }
            return {
                "status": "ok",
                "total_depth": total_depth,
                "queues": queues,
                "error": None,
            }
        except Exception as exc:
            return {
                "status": "unavailable",
                "total_depth": 0,
                "queues": {
                    queue_name: {
                        "depth": 0,
                        "concurrency": max(
                            int(queue_concurrency.get(queue_name, 1) or 1),
                            1,
                        ),
                        "backlog": 0,
                        "pressure_ratio": 0.0,
                    }
                    for queue_name in queue_names
                },
                "error": str(exc) or exc.__class__.__name__,
            }
        finally:
            if client is not None:
                _close_redis_client(client)

    def _make_redis_client(self) -> Any:
        settings = get_settings()
        if self._redis_client_factory is not None:
            return self._redis_client_factory(settings.celery_broker_url)

        import redis

        timeout = settings.generation_runtime_monitor_timeout_seconds
        return redis.Redis.from_url(
            settings.celery_broker_url,
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
        )

    async def _worker_health_summary(self, queue_names: Sequence[str]) -> dict[str, Any]:
        return await asyncio.to_thread(self._read_worker_health_summary, queue_names)

    def _read_worker_health_summary(self, queue_names: Sequence[str]) -> dict[str, Any]:
        try:
            celery_app = self._celery_app or _default_celery_app()
            inspector = celery_app.control.inspect(
                timeout=get_settings().generation_runtime_monitor_timeout_seconds
            )
            active_queues_by_worker = inspector.active_queues() or {}
            stats_by_worker = inspector.stats() or {}
            active_by_worker = inspector.active() or {}
        except Exception as exc:
            return {
                "status": "unavailable",
                "online_count": 0,
                "expected_queues": list(queue_names),
                "missing_queues": list(queue_names),
                "total_active_tasks": 0,
                "workers": [],
                "error": str(exc) or exc.__class__.__name__,
            }

        worker_names = sorted(
            {
                *active_queues_by_worker.keys(),
                *stats_by_worker.keys(),
                *active_by_worker.keys(),
            }
        )
        listened_queues: set[str] = set()
        workers: list[dict[str, Any]] = []
        total_active_tasks = 0
        for worker_name in worker_names:
            queues = sorted(
                str(queue.get("name"))
                for queue in active_queues_by_worker.get(worker_name, [])
                if isinstance(queue, dict) and queue.get("name")
            )
            listened_queues.update(queues)
            active_tasks = len(active_by_worker.get(worker_name) or [])
            total_active_tasks += active_tasks
            workers.append(
                {
                    "name": worker_name,
                    "queues": queues,
                    "concurrency": _worker_concurrency(stats_by_worker.get(worker_name)),
                    "active_tasks": active_tasks,
                }
            )

        missing_queues = [
            queue_name for queue_name in queue_names if queue_name not in listened_queues
        ]
        if not workers:
            status = "unavailable"
            error = "No Celery workers responded."
        else:
            status = "degraded" if missing_queues else "ok"
            error = None

        return {
            "status": status,
            "online_count": len(workers),
            "expected_queues": list(queue_names),
            "missing_queues": missing_queues,
            "total_active_tasks": total_active_tasks,
            "workers": workers,
            "error": error,
        }


def _default_celery_app() -> Any:
    from backend.app.worker.celery_app import celery_app

    return celery_app


def _worker_concurrency(stats: dict[str, Any] | None) -> int | None:
    if not isinstance(stats, dict):
        return None
    pool = stats.get("pool")
    if not isinstance(pool, dict):
        return None
    value = pool.get("max-concurrency") or pool.get("max_concurrency")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _close_redis_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


def _runtime_cache_key(
    queue_names: Sequence[str],
    queue_concurrency: dict[str, int],
    redis_client_factory: Callable[[str], Any] | None,
    celery_app: Any | None,
) -> tuple[Any, ...]:
    settings = get_settings()
    return (
        settings.generation_task_execution_backend,
        settings.celery_broker_url,
        settings.celery_result_backend,
        tuple(queue_names),
        tuple(sorted(queue_concurrency.items())),
        id(redis_client_factory) if redis_client_factory is not None else 0,
        id(celery_app) if celery_app is not None else 0,
    )


def _get_runtime_cache(cache_key: tuple[Any, ...]) -> dict[str, Any] | None:
    ttl_seconds = get_settings().generation_runtime_monitor_cache_seconds
    if ttl_seconds <= 0:
        return None
    cached = _runtime_summary_cache.get(cache_key)
    if cached is None:
        return None
    expires_at, summary = cached
    if expires_at <= time.monotonic():
        _runtime_summary_cache.pop(cache_key, None)
        return None
    return copy.deepcopy(summary)


def _set_runtime_cache(cache_key: tuple[Any, ...], summary: dict[str, Any]) -> None:
    ttl_seconds = get_settings().generation_runtime_monitor_cache_seconds
    if ttl_seconds <= 0:
        return
    _runtime_summary_cache[cache_key] = (
        time.monotonic() + ttl_seconds,
        copy.deepcopy(summary),
    )


def _disabled_redis_summary(queue_names: Sequence[str]) -> dict[str, Any]:
    return {
        "status": "disabled",
        "total_depth": 0,
        "queues": {
            queue_name: {
                "depth": 0,
                "concurrency": 0,
                "backlog": 0,
                "pressure_ratio": 0.0,
            }
            for queue_name in queue_names
        },
        "error": None,
    }


def _disabled_worker_summary(queue_names: Sequence[str]) -> dict[str, Any]:
    return {
        "status": "disabled",
        "online_count": 0,
        "expected_queues": list(queue_names),
        "missing_queues": [],
        "total_active_tasks": 0,
        "workers": [],
        "error": None,
    }
