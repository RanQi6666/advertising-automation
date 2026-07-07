import argparse
import asyncio
import json
import time
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Literal

import httpx

CliMode = Literal["text", "image", "both"]
RequestMode = Literal["text", "image"]

DEFAULT_BASE_URL = "http://127.0.0.1:8317/v1"
DEFAULT_LEVELS = "1,2,4,6,8"
RATE_LIMIT_MARKERS = ("model_cooldown", "rate limit", "too many requests")


@dataclass(frozen=True)
class BenchmarkConfig:
    base_url: str
    api_key: str
    mode: CliMode
    text_model: str | None
    image_model: str | None
    image_size: str
    levels: list[int]
    requests_per_level: int | None
    text_timeout: float
    image_timeout: float


@dataclass(frozen=True)
class BenchmarkRequest:
    mode: RequestMode
    request_index: int
    path: str
    headers: dict[str, str]
    json: dict[str, Any]
    timeout_seconds: float


@dataclass(frozen=True)
class BenchmarkResult:
    ok: bool
    status_code: int | None
    latency_ms: float
    rate_limited: bool
    error: str | None = None


@dataclass(frozen=True)
class LevelSummary:
    mode: RequestMode
    level: int
    total: int
    succeeded: int
    failed: int
    success_rate: float
    rate_limited: int
    throughput_req_per_min: float
    elapsed_ms: float
    latency_min_ms: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_max_ms: float
    status_counts: dict[str, int]
    sample_errors: list[str]


def build_requests(
    config: BenchmarkConfig,
    mode: RequestMode,
    total: int,
) -> list[BenchmarkRequest]:
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    requests: list[BenchmarkRequest] = []
    for request_index in range(1, total + 1):
        if mode == "text":
            if config.text_model is None:
                raise ValueError("文本模式必须提供 --text-model。")
            requests.append(
                BenchmarkRequest(
                    mode=mode,
                    request_index=request_index,
                    path="responses",
                    headers=headers,
                    json={
                        "model": config.text_model,
                        "input": [
                            {
                                "role": "system",
                                "content": "你是广告素材吞吐压测助手，请用简短中文回答。",
                            },
                            {
                                "role": "user",
                                "content": (
                                    "请用一句话总结夏季广告素材测试要点。"
                                    f"请求序号：{request_index}。"
                                ),
                            },
                        ],
                    },
                    timeout_seconds=config.text_timeout,
                )
            )
            continue

        if config.image_model is None:
            raise ValueError("图片模式必须提供 --image-model。")
        requests.append(
            BenchmarkRequest(
                mode=mode,
                request_index=request_index,
                path="images/generations",
                headers=headers,
                json={
                    "model": config.image_model,
                    "prompt": (
                        "一张简洁的夏季饮品广告海报，明亮自然光，产品居中，"
                        f"中文短标题“清爽一夏”。请求序号：{request_index}。"
                    ),
                    "size": config.image_size,
                },
                timeout_seconds=config.image_timeout,
            )
        )
    return requests


async def run_level(
    config: BenchmarkConfig,
    mode: RequestMode,
    level: int,
) -> LevelSummary:
    total = config.requests_per_level if config.requests_per_level is not None else level
    requests = build_requests(config, mode, total)
    timeout = httpx.Timeout(None)
    limits = httpx.Limits(
        max_connections=max(level * 2, 10),
        max_keepalive_connections=max(level, 10),
    )
    async with httpx.AsyncClient(
        base_url=_normalize_base_url(config.base_url),
        timeout=timeout,
        limits=limits,
    ) as client:
        started_at = time.perf_counter()
        semaphore = asyncio.Semaphore(level)
        results = await asyncio.gather(
            *(_send_request(client, semaphore, request) for request in requests)
        )
        elapsed_ms = (time.perf_counter() - started_at) * 1000
    return summarize_results(mode, level, results, elapsed_ms)


async def _send_request(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    request: BenchmarkRequest,
) -> BenchmarkResult:
    async with semaphore:
        started_at = time.perf_counter()
        try:
            response = await client.post(
                request.path,
                headers=request.headers,
                json=request.json,
                timeout=request.timeout_seconds,
            )
            latency_ms = (time.perf_counter() - started_at) * 1000
            body_excerpt = _response_excerpt(response)
            rate_limited = _is_rate_limited(response.status_code, body_excerpt)
            ok = response.status_code < 400 and not rate_limited
            return BenchmarkResult(
                ok=ok,
                status_code=response.status_code,
                latency_ms=latency_ms,
                rate_limited=rate_limited,
                error=None if ok else _error_summary(response.status_code, body_excerpt),
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - started_at) * 1000
            return BenchmarkResult(
                ok=False,
                status_code=None,
                latency_ms=latency_ms,
                rate_limited=_is_rate_limited(None, str(exc)),
                error=str(exc) or exc.__class__.__name__,
            )


def summarize_results(
    mode: RequestMode,
    level: int,
    results: list[BenchmarkResult],
    elapsed_ms: float,
) -> LevelSummary:
    latencies = [result.latency_ms for result in results]
    status_counts: dict[str, int] = {}
    for result in results:
        key = str(result.status_code) if result.status_code is not None else "network_error"
        status_counts[key] = status_counts.get(key, 0) + 1

    failed = [result for result in results if not result.ok]
    succeeded = len(results) - len(failed)
    elapsed_minutes = elapsed_ms / 60000
    return LevelSummary(
        mode=mode,
        level=level,
        total=len(results),
        succeeded=succeeded,
        failed=len(failed),
        success_rate=round(succeeded / max(len(results), 1), 4),
        rate_limited=sum(1 for result in results if result.rate_limited),
        throughput_req_per_min=round(succeeded / max(elapsed_minutes, 0.000001), 2),
        elapsed_ms=round(elapsed_ms, 2),
        latency_min_ms=round(min(latencies), 2) if latencies else 0.0,
        latency_p50_ms=round(_percentile(latencies, 0.50), 2) if latencies else 0.0,
        latency_p95_ms=round(_percentile(latencies, 0.95), 2) if latencies else 0.0,
        latency_max_ms=round(max(latencies), 2) if latencies else 0.0,
        status_counts=dict(sorted(status_counts.items())),
        sample_errors=[_trim_text(result.error, 300) for result in failed[:3] if result.error],
    )


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = min(round((len(sorted_values) - 1) * percentile), len(sorted_values) - 1)
    return sorted_values[index]


def _selected_modes(mode: CliMode) -> list[RequestMode]:
    if mode == "both":
        return ["text", "image"]
    return [mode]


def _normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/"


def _response_excerpt(response: httpx.Response, limit: int = 2000) -> str:
    return response.content[:limit].decode("utf-8", errors="replace")


def _is_rate_limited(status_code: int | None, body: str | None) -> bool:
    if status_code == 429:
        return True
    lowered = (body or "").lower()
    return any(marker in lowered for marker in RATE_LIMIT_MARKERS)


def _error_summary(status_code: int | None, body: str) -> str:
    if status_code is None:
        return _trim_text(body, 300)
    if body.strip():
        return _trim_text(f"HTTP {status_code}: {body.strip()}", 300)
    return f"HTTP {status_code}"


def _trim_text(value: str | None, limit: int) -> str:
    if not value:
        return ""
    collapsed = " ".join(value.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


def print_progress(summary: LevelSummary) -> None:
    errors = "；".join(summary.sample_errors) if summary.sample_errors else "无"
    print(
        "进度："
        f"模式={summary.mode} "
        f"并发={summary.level} "
        f"吞吐={summary.throughput_req_per_min:.2f} req/min "
        f"成功率={summary.success_rate * 100:.1f}% "
        f"成功={summary.succeeded}/{summary.total} "
        f"429={summary.rate_limited} "
        f"延迟ms=min {summary.latency_min_ms:.2f}, "
        f"p50 {summary.latency_p50_ms:.2f}, "
        f"p95 {summary.latency_p95_ms:.2f}, "
        f"max {summary.latency_max_ms:.2f} "
        f"错误样本={errors}"
    )


def print_summary_table(summaries: list[LevelSummary]) -> None:
    rows = [
        {
            "模式": summary.mode,
            "并发": str(summary.level),
            "请求": str(summary.total),
            "成功": f"{summary.succeeded}/{summary.total}",
            "成功率": f"{summary.success_rate * 100:.1f}%",
            "429": str(summary.rate_limited),
            "req/min": f"{summary.throughput_req_per_min:.2f}",
            "min(ms)": f"{summary.latency_min_ms:.2f}",
            "p50(ms)": f"{summary.latency_p50_ms:.2f}",
            "p95(ms)": f"{summary.latency_p95_ms:.2f}",
            "max(ms)": f"{summary.latency_max_ms:.2f}",
            "错误样本": _trim_text("；".join(summary.sample_errors), 120)
            if summary.sample_errors
            else "",
        }
        for summary in summaries
    ]
    headers = [
        "模式",
        "并发",
        "请求",
        "成功",
        "成功率",
        "429",
        "req/min",
        "min(ms)",
        "p50(ms)",
        "p95(ms)",
        "max(ms)",
        "错误样本",
    ]
    widths = {
        header: max([_display_width(header), *[_display_width(row[header]) for row in rows]])
        for header in headers
    }
    print("\n汇总表：")
    print("  ".join(_pad(header, widths[header]) for header in headers))
    print("  ".join("-" * widths[header] for header in headers))
    for row in rows:
        print("  ".join(_pad(row[header], widths[header]) for header in headers))


def _display_width(value: str) -> int:
    width = 0
    for char in value:
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def _pad(value: str, width: int) -> str:
    return value + " " * max(width - _display_width(value), 0)


def parse_args() -> BenchmarkConfig:
    parser = argparse.ArgumentParser(
        description="直接压测 CLIProxyAPI 号池的文本和图片吞吐。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="号池 /v1 地址。")
    parser.add_argument("--api-key", required=True, help="号池 API Key。")
    parser.add_argument(
        "--mode",
        choices=["text", "image", "both"],
        default="both",
        help="压测模式。",
    )
    parser.add_argument("--text-model", default=None, help="文本模型名。")
    parser.add_argument("--image-model", default=None, help="图片模型名。")
    parser.add_argument("--image-size", default="1024x1024", help="图片尺寸。")
    parser.add_argument("--levels", default=DEFAULT_LEVELS, help="逗号分隔的并发档。")
    parser.add_argument(
        "--requests-per-level",
        type=int,
        default=None,
        help="每档请求数；不填时等于当前并发档。",
    )
    parser.add_argument("--text-timeout", type=float, default=60, help="文本超时秒数。")
    parser.add_argument("--image-timeout", type=float, default=300, help="图片超时秒数。")
    args = parser.parse_args()

    mode: CliMode = args.mode
    levels = _parse_levels(args.levels, parser)
    if args.requests_per_level is not None and args.requests_per_level < 1:
        parser.error("--requests-per-level 必须大于等于 1。")
    if "text" in _selected_modes(mode) and not args.text_model:
        parser.error("mode 含 text 时必须提供 --text-model。")
    if "image" in _selected_modes(mode) and not args.image_model:
        parser.error("mode 含 image 时必须提供 --image-model。")
    if args.text_timeout <= 0:
        parser.error("--text-timeout 必须大于 0。")
    if args.image_timeout <= 0:
        parser.error("--image-timeout 必须大于 0。")

    return BenchmarkConfig(
        base_url=args.base_url,
        api_key=args.api_key,
        mode=mode,
        text_model=args.text_model,
        image_model=args.image_model,
        image_size=args.image_size,
        levels=levels,
        requests_per_level=args.requests_per_level,
        text_timeout=args.text_timeout,
        image_timeout=args.image_timeout,
    )


def _parse_levels(raw_levels: str, parser: argparse.ArgumentParser) -> list[int]:
    levels: list[int] = []
    for raw_level in raw_levels.split(","):
        value = raw_level.strip()
        if not value:
            continue
        try:
            level = int(value)
        except ValueError:
            parser.error("--levels 只能包含逗号分隔的正整数。")
        if level < 1:
            parser.error("--levels 中的并发档必须大于等于 1。")
        levels.append(level)
    if not levels:
        parser.error("--levels 至少需要一个并发档。")
    return levels


async def main() -> None:
    config = parse_args()
    modes = _selected_modes(config.mode)
    if "image" in modes:
        print("警告：当前模式会真实调用生图接口，会消耗号池账号额度，请先确认并发档和请求数。")

    summaries: list[LevelSummary] = []
    for mode in modes:
        for level in config.levels:
            summary = await run_level(config, mode, level)
            summaries.append(summary)
            print_progress(summary)

    print_summary_table(summaries)
    print("\nJSON 汇总：")
    print(
        json.dumps(
            {
                "base_url": config.base_url,
                "mode": config.mode,
                "levels": config.levels,
                "requests_per_level": config.requests_per_level,
                "results": [asdict(summary) for summary in summaries],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
