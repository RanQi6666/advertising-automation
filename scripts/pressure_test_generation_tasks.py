import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx

Mode = Literal["monitor", "topic"]
DEFAULT_OPERATOR_ID = "00000000-0000-4000-8000-000000000001"


@dataclass
class PressureConfig:
    base_url: str
    token: str | None
    users: int
    requests_per_user: int
    mode: Mode
    campaign_id: str | None
    operator_id: str


@dataclass(frozen=True)
class PressureRequest:
    user_index: int
    request_index: int
    method: str
    path: str
    headers: dict[str, str]
    json: dict[str, Any] | None = None


@dataclass(frozen=True)
class PressureResult:
    ok: bool
    status_code: int | None
    latency_ms: float
    error: str | None = None


def build_requests(config: PressureConfig) -> list[PressureRequest]:
    headers = _request_headers(config.token, config.operator_id)
    requests: list[PressureRequest] = []
    if config.mode == "topic" and not config.campaign_id:
        raise ValueError("--campaign-id is required when --mode topic is used.")

    for user_index in range(1, config.users + 1):
        for request_index in range(1, config.requests_per_user + 1):
            if config.mode == "monitor":
                requests.append(
                    PressureRequest(
                        user_index=user_index,
                        request_index=request_index,
                        method="GET",
                        path="/generation-tasks?limit=200",
                        headers=headers,
                    )
                )
                continue

            requests.append(
                PressureRequest(
                    user_index=user_index,
                    request_index=request_index,
                    method="POST",
                    path="/topics/generate/task",
                    headers=headers,
                    json={
                        "campaign_id": config.campaign_id,
                        "limit": 3,
                        "signals": {
                            "pressure_user": user_index,
                            "pressure_request": request_index,
                        },
                    },
                )
            )
    return requests


async def run_pressure_test(config: PressureConfig) -> dict[str, Any]:
    requests = build_requests(config)
    timeout = httpx.Timeout(30.0, connect=5.0)
    limits = httpx.Limits(max_connections=max(config.users * 2, 10))
    async with httpx.AsyncClient(
        base_url=config.base_url.rstrip("/"),
        timeout=timeout,
        limits=limits,
    ) as client:
        started_at = time.perf_counter()
        semaphore = asyncio.Semaphore(config.users)
        results = await asyncio.gather(
            *(_send_request(client, semaphore, request) for request in requests)
        )
        elapsed_ms = (time.perf_counter() - started_at) * 1000
    return summarize_results(results, elapsed_ms)


async def _send_request(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    request: PressureRequest,
) -> PressureResult:
    async with semaphore:
        started_at = time.perf_counter()
        try:
            response = await client.request(
                request.method,
                request.path,
                headers=request.headers,
                json=request.json,
            )
            latency_ms = (time.perf_counter() - started_at) * 1000
            return PressureResult(
                ok=response.status_code < 400,
                status_code=response.status_code,
                latency_ms=latency_ms,
                error=None if response.status_code < 400 else response.text[:300],
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - started_at) * 1000
            return PressureResult(
                ok=False,
                status_code=None,
                latency_ms=latency_ms,
                error=str(exc) or exc.__class__.__name__,
            )


def summarize_results(results: list[PressureResult], elapsed_ms: float) -> dict[str, Any]:
    latencies = [result.latency_ms for result in results]
    status_counts: dict[str, int] = {}
    for result in results:
        key = str(result.status_code) if result.status_code is not None else "network_error"
        status_counts[key] = status_counts.get(key, 0) + 1

    failures = [result for result in results if not result.ok]
    return {
        "total": len(results),
        "succeeded": len(results) - len(failures),
        "failed": len(failures),
        "success_rate": round((len(results) - len(failures)) / max(len(results), 1), 4),
        "elapsed_ms": round(elapsed_ms, 2),
        "latency_ms": {
            "min": round(min(latencies), 2) if latencies else 0,
            "p50": round(statistics.median(latencies), 2) if latencies else 0,
            "p95": round(_percentile(latencies, 0.95), 2) if latencies else 0,
            "max": round(max(latencies), 2) if latencies else 0,
        },
        "status_counts": dict(sorted(status_counts.items())),
        "sample_errors": [failure.error for failure in failures[:5] if failure.error],
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = min(round((len(sorted_values) - 1) * percentile), len(sorted_values) - 1)
    return sorted_values[index]


def _request_headers(token: str | None, operator_id: str) -> dict[str, str]:
    headers = {"X-Operator-Id": operator_id}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def parse_args() -> PressureConfig:
    parser = argparse.ArgumentParser(
        description=(
            "Pressure-test generation task monitoring or explicit topic task enqueueing. "
            "Use monitor mode by default to avoid calling real model providers."
        )
    )
    parser.add_argument("--base-url", default="http://127.0.0.1/api/v1")
    parser.add_argument("--token", default=None)
    parser.add_argument("--users", type=int, default=30)
    parser.add_argument("--requests-per-user", type=int, default=3)
    parser.add_argument("--mode", choices=["monitor", "topic"], default="monitor")
    parser.add_argument("--campaign-id", default=None)
    parser.add_argument("--operator-id", default=DEFAULT_OPERATOR_ID)
    args = parser.parse_args()
    return PressureConfig(
        base_url=args.base_url,
        token=args.token,
        users=max(args.users, 1),
        requests_per_user=max(args.requests_per_user, 1),
        mode=args.mode,
        campaign_id=args.campaign_id,
        operator_id=args.operator_id,
    )


async def main() -> None:
    config = parse_args()
    summary = await run_pressure_test(config)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
