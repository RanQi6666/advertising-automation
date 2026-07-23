from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass
from typing import Any

import httpx
from redis import asyncio as redis_async

from backend.app.core.config import get_settings
from backend.app.core.errors import ProviderError
from backend.app.schemas.ad_research import CollectorAd


@dataclass
class RedisLease:
    client: Any
    key: str
    token: str

    async def release(self) -> None:
        script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('del', KEYS[1])
        end
        return 0
        """
        await self.client.eval(script, 1, self.key, self.token)


class RedisGlobalLimiter:
    """A Redis lease, shared by all research workers rather than per-process semaphores."""

    def __init__(self, redis_url: str | None = None) -> None:
        settings = get_settings()
        self.redis_url = redis_url or settings.redis_url or settings.celery_broker_url
        self._client: Any = None

    async def acquire(self, name: str, *, limit: int, ttl_seconds: int) -> RedisLease | None:
        if not self.redis_url:
            raise ProviderError("REDIS_URL is required for ad research model limiting.")
        if self._client is None:
            self._client = redis_async.from_url(self.redis_url, decode_responses=True)
        token = secrets.token_urlsafe(18)
        for slot in range(max(int(limit), 1)):
            key = f"{name}:{slot}"
            if await self._client.set(key, token, nx=True, ex=max(int(ttl_seconds), 1)):
                return RedisLease(self._client, key, token)
        return None

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class AdResearchModel:
    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        limiter: RedisGlobalLimiter | None = None,
    ) -> None:
        self.settings = get_settings()
        self._http_client = http_client
        self.limiter = limiter or RedisGlobalLimiter()

    async def plan_queries(
        self,
        *,
        country: str,
        category: str,
        seed_keywords: list[str],
        round_number: int,
        gap_summary: dict[str, Any] | None = None,
    ) -> list[str]:
        fallback = _unique_queries(seed_keywords or [category])
        if self.settings.llm_provider == "mock":
            return fallback[:12]
        data = await self._complete_json(
            system=(
                "You plan lawful public-ad-library keyword research. Return JSON only: "
                '{"queries":["short query"]}. Create up to 12 short, independent '
                "public-library queries for the requested country/category. Use gap_summary "
                "to change retrieval direction after weak rounds. Do not repeat "
                "previous_queries. If technical_rejection_summary shows many "
                "duration_over_30 results, favor natural short-form creative terms such as "
                "short video, reel, or promo. If model exclusion signals show category "
                "mismatch, strengthen the intersection between the business category and "
                "observable product or conversion language. If duplicate_count is high, "
                "explore different product types, local language, brands, advertisers, or "
                "app terms. Keep this guidance generic across countries and categories. "
                "Never provide instructions to evade review, tracking, access controls, or "
                "landing-page inspection."
            ),
            user={
                "country": country,
                "category": category,
                "seed_keywords": seed_keywords,
                "round_number": round_number,
                "gap_summary": gap_summary or {},
            },
        )
        values = data.get("queries") if isinstance(data, dict) else []
        return _unique_queries(values if isinstance(values, list) else [])[:12] or fallback[:12]

    async def classify(self, *, category: str, candidate: CollectorAd) -> dict[str, Any]:
        if self.settings.llm_provider == "mock":
            return _mock_classification(category, candidate)
        user: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "category": category,
                        "candidate": candidate.model_dump(mode="json"),
                        "rules": {
                            "public_proxy_only": True,
                            "do_not_infer_cost_or_conversion": True,
                            "do_not_confirm_cloaking": True,
                        },
                    },
                    ensure_ascii=False,
                ),
            }
        ]
        if candidate.thumbnail_url:
            user.append({"type": "image_url", "image_url": {"url": candidate.thumbnail_url}})
        data = await self._complete_json(
            system=(
                "Classify a technically qualified public advertisement. Return JSON only. "
                "Keys: category_match, category_confidence, business_type, "
                "creative_relevance_score, public_performance_signal_score, "
                "real_money_signal_score, is_obviously_unrelated, text_evidence, "
                "visual_evidence, public_signal_evidence, public_risk_signals, recommendation. "
                "recommendation must be exactly the enum value keep or exclude; never prose. "
                "public_performance_signal_score is a public continuity proxy, not actual cost, "
                "CPA, ROAS, spend, or conversion. Do not confirm cloaking; "
                "use public mismatch signals."
            ),
            user=user,
        )
        return _validated_classification(data)

    async def _complete_json(
        self, *, system: str, user: Any, effort: str = "none"
    ) -> dict[str, Any]:
        lease = await self._wait_for_lease()
        client = self._http_client
        owns_client = client is None
        if client is None:
            base_url = (
                self.settings.model_gateway_base_url or self.settings.openai_base_url or ""
            ).rstrip("/")
            api_key = self.settings.model_gateway_api_key or self.settings.openai_api_key
            if not base_url or not api_key:
                await lease.release()
                raise ProviderError("model gateway URL and API key are required for ad research.")
            client = httpx.AsyncClient(base_url=f"{base_url}/", timeout=None)
        try:
            response = await client.post(
                "responses",
                headers={
                    "Authorization": (
                        "Bearer "
                        f"{self.settings.model_gateway_api_key or self.settings.openai_api_key}"
                    ),
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.settings.ad_research_model,
                    "reasoning": {"effort": effort},
                    "input": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": _responses_content(user)},
                    ],
                },
                timeout=httpx.Timeout(self.settings.ad_research_model_timeout_seconds),
            )
            response.raise_for_status()
            return json.loads(_strip_json_markdown(_extract_response_text(response.json())))
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise ProviderError(
                f"ad research model request failed: {exc.__class__.__name__}"
            ) from exc
        finally:
            await lease.release()
            if owns_client:
                await client.aclose()

    async def _wait_for_lease(self) -> RedisLease:
        # A globally shared slot can be held for a full model request. Wait long enough
        # for an in-flight request to finish instead of treating a saturated limiter as
        # an irrelevant candidate.
        wait_seconds = max(self.settings.ad_research_model_timeout_seconds + 5.0, 60.0)
        attempts = max(int(wait_seconds / 0.1), 1)
        lease_seconds = max(
            int(self.settings.ad_research_model_lease_seconds),
            int(self.settings.ad_research_model_timeout_seconds) + 5,
        )
        for _ in range(attempts):
            lease = await self.limiter.acquire(
                "ad-research:model",
                limit=self.settings.ad_research_model_concurrency,
                ttl_seconds=lease_seconds,
            )
            if lease is not None:
                return lease
            await asyncio.sleep(0.1)
        raise ProviderError("ad research model queue remained at capacity")


def _responses_content(user: Any) -> str | list[dict[str, Any]]:
    if not isinstance(user, list):
        return json.dumps(user, ensure_ascii=False) if isinstance(user, dict) else str(user)
    output: list[dict[str, Any]] = []
    for item in user:
        if item.get("type") == "image_url":
            image_url = str(item["image_url"].get("url") or "")
            output.append({"type": "input_image", "image_url": image_url})
        else:
            output.append({"type": "input_text", "text": str(item.get("text") or "")})
    return output


def _extract_response_text(data: dict[str, Any]) -> str:
    if isinstance(data.get("output_text"), str) and data["output_text"].strip():
        return data["output_text"]
    chunks: list[str] = []
    for output in data.get("output") or []:
        for part in output.get("content") or []:
            if isinstance(part.get("text"), str):
                chunks.append(part["text"])
    if not chunks:
        raise ProviderError("model returned no output text")
    return "".join(chunks)


def _strip_json_markdown(value: str) -> str:
    value = value.strip()
    if value.startswith("```") and value.endswith("```"):
        value = value[3:-3].strip()
        if value.lower().startswith("json"):
            value = value[4:].strip()
    return value


def _unique_queries(values: list[Any]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        query = str(value).strip()
        if not query or len(query) > 160 or query.casefold() in seen:
            continue
        output.append(query)
        seen.add(query.casefold())
    return output


def _mock_classification(category: str, candidate: CollectorAd) -> dict[str, Any]:
    haystack = " ".join(
        candidate.text_variants + [candidate.headline or "", candidate.cta_text or ""]
    ).casefold()
    matched = category.casefold() in haystack or bool(haystack)
    return {
        "category_match": matched,
        "category_confidence": 0.9 if matched else 0.0,
        "business_type": category,
        "creative_relevance_score": 80 if matched else 0,
        "public_performance_signal_score": min(100, 40 + (candidate.days_running or 0) * 5),
        "real_money_signal_score": 0.0,
        "is_obviously_unrelated": not matched,
        "text_evidence": candidate.text_variants[:2],
        "visual_evidence": [],
        "public_signal_evidence": [f"active_days={candidate.days_running or 0}"],
        "public_risk_signals": [],
        "recommendation": "keep" if matched else "exclude",
    }


def _validated_classification(data: Any) -> dict[str, Any]:
    data = data if isinstance(data, dict) else {}
    category_match = bool(data.get("category_match"))
    is_obviously_unrelated = bool(data.get("is_obviously_unrelated"))
    result = {
        "category_match": category_match,
        "category_confidence": _score(data.get("category_confidence"), fractional=True),
        "business_type": str(data.get("business_type") or "unknown")[:128],
        "creative_relevance_score": _score(data.get("creative_relevance_score")),
        "public_performance_signal_score": _score(data.get("public_performance_signal_score")),
        "real_money_signal_score": _score(data.get("real_money_signal_score"), fractional=True),
        "is_obviously_unrelated": is_obviously_unrelated,
        "text_evidence": _strings(data.get("text_evidence")),
        "visual_evidence": _strings(data.get("visual_evidence")),
        "public_signal_evidence": _strings(data.get("public_signal_evidence")),
        "public_risk_signals": _strings(data.get("public_risk_signals")),
        "recommendation": _normalize_recommendation(
            data.get("recommendation"),
            category_match=category_match,
            is_obviously_unrelated=is_obviously_unrelated,
        ),
    }
    return result


def _normalize_recommendation(
    value: Any, *, category_match: bool, is_obviously_unrelated: bool
) -> str:
    recommendation = str(value or "").strip().casefold()
    if recommendation in {"keep", "include", "retain"}:
        return "keep"
    if recommendation in {"exclude", "drop", "reject"}:
        return "exclude"
    if recommendation.startswith(("exclude", "drop", "reject")) or any(
        phrase in recommendation
        for phrase in ("do not keep", "don't keep", "do not classify", "not gambling", "unrelated")
    ):
        return "exclude"
    if any(
        phrase in recommendation
        for phrase in ("keep", "include", "retain", "high-confidence match", "gambling-related")
    ):
        return "keep"
    return "keep" if category_match and not is_obviously_unrelated else "exclude"


def _score(value: Any, *, fractional: bool = False) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0 if fractional else 100.0, parsed))


def _strings(value: Any) -> list[str]:
    return [str(item)[:300] for item in value] if isinstance(value, list) else []
