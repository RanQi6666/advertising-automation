from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import re
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
from redis import asyncio as redis_async

from backend.app.core.config import get_settings
from backend.app.core.errors import ProviderError
from backend.app.schemas.ad_research import CollectorAd
from backend.app.services.ad_research_media import PreparedAdMedia

QueryIntent = Literal[
    "game_gambling",
    "sports_betting",
    "local_exploration",
    "format_exploration",
]

QUERY_INTENTS = frozenset(
    {
        "game_gambling",
        "sports_betting",
        "local_exploration",
        "format_exploration",
    }
)
_QUERY_ID_PATTERN = re.compile(r"r[1-9]\d*_q\d{2}")


@dataclass(frozen=True)
class QueryPerformance:
    """Controlled retrieval facts from a completed research round."""

    query_id: str | None = None
    query: str | None = None
    collected_count: int = 0
    selected_count: int = 0
    rejected_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "collected_count": self.collected_count,
            "selected_count": self.selected_count,
            "rejected_count": self.rejected_count,
        }
        if self.query_id:
            payload["query_id"] = self.query_id
        if self.query:
            payload["query"] = self.query
        return payload


@dataclass(frozen=True)
class RoundReview:
    """Safe, structured facts used to change the next research round."""

    query_performance: tuple[QueryPerformance, ...] = ()
    priority_gaps: tuple[str, ...] = ()
    missing_signals: tuple[str, ...] = ()
    high_score_visible_elements: tuple[str, ...] = ()
    technical_rejection_summary: tuple[tuple[str, int], ...] = ()
    duplicate_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "query_performance": [item.as_dict() for item in self.query_performance],
            "priority_gaps": list(self.priority_gaps),
            "missing_signals": list(self.missing_signals),
            "high_score_visible_elements": list(self.high_score_visible_elements),
            "technical_rejection_summary": dict(self.technical_rejection_summary),
            "duplicate_count": self.duplicate_count,
        }


@dataclass(frozen=True)
class PlannedQuery:
    query_id: str
    query: str
    intent: QueryIntent
    rationale: str

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str)
            for value in (self.query_id, self.query, self.intent, self.rationale)
        ):
            raise TypeError("planned query fields must be strings")
        query_id = self.query_id.strip()
        query = self.query.strip()
        intent = self.intent.strip()
        rationale = self.rationale.strip()
        if not _QUERY_ID_PATTERN.fullmatch(query_id):
            raise ValueError("query_id must use the r1_q01 format")
        if not query or len(query) > 160:
            raise ValueError("query must be a non-empty string of at most 160 characters")
        if intent not in QUERY_INTENTS:
            raise ValueError("intent is not allowed")
        if not rationale or len(rationale) > 500:
            raise ValueError("rationale must be a non-empty string of at most 500 characters")
        object.__setattr__(self, "query_id", query_id)
        object.__setattr__(self, "query", query)
        object.__setattr__(self, "intent", intent)
        object.__setattr__(self, "rationale", rationale)

    def as_dict(self) -> dict[str, str]:
        return {
            "query_id": self.query_id,
            "query": self.query,
            "intent": self.intent,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class QueryPlan:
    queries: tuple[PlannedQuery, ...]
    round_review: RoundReview | None = None
    summary: str | None = None

    def __post_init__(self) -> None:
        if not self.queries:
            raise ValueError("QueryPlan requires at least one query")
        query_ids = [item.query_id.casefold() for item in self.queries]
        queries = [item.query.casefold() for item in self.queries]
        if len(query_ids) != len(set(query_ids)) or len(queries) != len(set(queries)):
            raise ValueError("QueryPlan queries and query_ids must be unique")
        if self.summary is not None:
            summary = self.summary.strip()
            object.__setattr__(self, "summary", summary or None)

    def __iter__(self) -> Iterator[PlannedQuery]:
        return iter(self.queries)

    def __len__(self) -> int:
        return len(self.queries)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"queries": [item.as_dict() for item in self.queries]}
        if self.round_review is not None:
            payload["round_review"] = self.round_review.as_dict()
        if self.summary:
            payload["summary"] = self.summary
        return payload


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
    """A Redis lease shared by all research workers, not a per-process semaphore."""

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
    ) -> QueryPlan:
        review = _round_review_from_summary(gap_summary) if gap_summary else None
        fallback = _fallback_query_plan(
            seed_keywords=seed_keywords,
            category=category,
            round_number=round_number,
            round_review=review,
        )
        if self.settings.llm_provider == "mock":
            return fallback
        try:
            data = await self._complete_json(
                system=(
                    "You plan lawful public-ad-library keyword research. Return one JSON object "
                    "only, with this exact schema: "
                    '{"queries":[{"query_id":"r1_q01","query":"short query",'
                    '"intent":"game_gambling","rationale":"why this query"}],'
                    '"summary":"optional short round summary"}. '
                    "queries must be an array of at most 12 independent objects. Every object "
                    "must include query_id, query, intent, and rationale. query_id must use the "
                    "r1_q01 form. intent must be exactly one of: game_gambling, sports_betting, "
                    "local_exploration, format_exploration. Create short public-library queries "
                    "for the requested country/category. Use only the controlled round review to "
                    "change retrieval direction. Do not repeat prior queries. If technical "
                    "rejections show many duration_over_30 results, favor natural short-form "
                    "creative terms such as short video, reel, or promo. If duplicates are high, "
                    "explore different product types, local language, spelling variants, emojis, "
                    "brands, advertisers, or app terms. Never provide instructions to evade "
                    "review, tracking, access controls, user targeting, or landing-page inspection."
                ),
                user={
                    "country": country,
                    "category": category,
                    "user_keywords": _unique_queries(seed_keywords)[:12],
                    "round_number": max(int(round_number), 1),
                    "round_review": (review or RoundReview()).as_dict(),
                },
            )
        except ProviderError:
            return fallback
        return _query_plan_from_response(
            data,
            fallback=fallback,
            round_review=review,
        )

    async def score_visual(
        self,
        *,
        category: str,
        candidate: CollectorAd,
        media: PreparedAdMedia,
    ) -> dict[str, Any]:
        if self.settings.llm_provider == "mock":
            return _mock_visual_score(media)
        user: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "category": category,
                        "media": {
                            "duration_seconds": candidate.duration_seconds,
                            "active_days": candidate.days_running,
                            "frame_count": len(media.local_frame_paths),
                        },
                    },
                    ensure_ascii=False,
                ),
            },
            *[_image_part(path) for path in media.local_frame_paths],
        ]
        data = await self._complete_json(
            system=_VISUAL_SCORING_SYSTEM_PROMPT,
            user=user,
            effort="none",
        )
        return _validated_visual_score(data, frame_count=len(media.local_frame_paths))

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


_VISUAL_SCORING_SYSTEM_PROMPT = """
Score this public advertisement only from the supplied video-frame images. Return JSON only with:
core_gambling_points, reward_ui_points, gambling_style_points, casino_context_points,
media_quality_points, analysis_confidence, visible_elements, visual_evidence, uncertain.
Use these maximums: core_gambling_points 40, reward_ui_points 20, gambling_style_points 15,
casino_context_points 10, media_quality_points 5. Direct gambling gameplay or UI gets the
strongest score: slots, 777, roulette, cards, live dealers, dice, Aviator/Crash, fishing-game
gambling UI, bets, odds, or balances. Coins, crystals, reward chests, Jackpot, Bonus,
multipliers, big-win effects, WIN, VIP, and reward UI/effects can also receive points as
casino-style evidence. Score only visible content in the provided images. Do not infer from
ad copy, audio, advertiser, page, URL, landing page, or facts not provided. visual_evidence
must be a list of objects with frame_index and detail. Do not return a recommendation,
category_match, category_confidence, or is_obviously_unrelated field.
""".strip()


def _image_part(path: Path) -> dict[str, Any]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ProviderError("ad research visual frame cannot be read") from exc
    if not payload:
        raise ProviderError("ad research visual frame is empty")
    mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(payload).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}}


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


def _round_review_from_summary(summary: dict[str, Any] | None) -> RoundReview:
    source = summary if isinstance(summary, dict) else {}
    query_performance: list[QueryPerformance] = []
    raw_performance = source.get("query_performance")
    if isinstance(raw_performance, list):
        for item in raw_performance[:12]:
            if not isinstance(item, dict):
                continue
            query = _short_text(item.get("query"))
            query_id = _short_text(item.get("query_id"))
            if query_id and not _QUERY_ID_PATTERN.fullmatch(query_id):
                query_id = None
            if not query and not query_id:
                continue
            query_performance.append(
                QueryPerformance(
                    query_id=query_id,
                    query=query,
                    collected_count=_non_negative_int(item.get("collected_count")),
                    selected_count=_non_negative_int(item.get("selected_count")),
                    rejected_count=_non_negative_int(item.get("rejected_count")),
                )
            )
    else:
        for query in _controlled_text_list(source.get("previous_queries"), limit=12):
            query_performance.append(QueryPerformance(query=query))
    technical = source.get("technical_rejection_summary")
    technical_rejections = tuple(
        (key, _non_negative_int(value))
        for key, value in (technical.items() if isinstance(technical, dict) else [])
        if isinstance(key, str)
        and key in {"duration_over_30", "missing_media", "invalid_media"}
        and _non_negative_int(value) > 0
    )
    return RoundReview(
        query_performance=tuple(query_performance),
        priority_gaps=tuple(_controlled_text_list(source.get("priority_gaps"), limit=12)),
        missing_signals=tuple(
            _controlled_text_list(
                source.get("missing_signals", source.get("missing_play_patterns")), limit=12
            )
        ),
        high_score_visible_elements=tuple(
            _controlled_text_list(source.get("high_score_visible_elements"), limit=12)
        ),
        technical_rejection_summary=technical_rejections,
        duplicate_count=_non_negative_int(source.get("duplicate_count")),
    )


def _query_plan_from_response(
    data: Any,
    *,
    fallback: QueryPlan,
    round_review: RoundReview | None,
) -> QueryPlan:
    if not isinstance(data, dict) or not isinstance(data.get("queries"), list):
        return fallback
    planned: list[PlannedQuery] = []
    seen_ids: set[str] = set()
    seen_queries: set[str] = set()
    for item in data["queries"][:12]:
        if not isinstance(item, dict):
            continue
        try:
            candidate = PlannedQuery(
                query_id=item.get("query_id"),
                query=item.get("query"),
                intent=item.get("intent"),
                rationale=item.get("rationale"),
            )
        except (TypeError, ValueError):
            continue
        normalized_id = candidate.query_id.casefold()
        normalized_query = candidate.query.casefold()
        if normalized_id in seen_ids or normalized_query in seen_queries:
            continue
        planned.append(candidate)
        seen_ids.add(normalized_id)
        seen_queries.add(normalized_query)
    if not planned:
        return fallback
    summary = _short_text(data.get("summary"), limit=500)
    return QueryPlan(
        queries=tuple(planned),
        round_review=round_review,
        summary=summary,
    )


def _fallback_query_plan(
    *,
    seed_keywords: list[str],
    category: str,
    round_number: int,
    round_review: RoundReview | None,
) -> QueryPlan:
    values = _unique_queries(seed_keywords or [category])[:12]
    if not values:
        values = ["public ads"]
    safe_round = max(int(round_number), 1)
    intent = _fallback_intent(category)
    return QueryPlan(
        queries=tuple(
            PlannedQuery(
                query_id=f"r{safe_round}_q{index:02d}",
                query=query,
                intent=intent,
                rationale="Deterministic fallback from the supplied seed keyword.",
            )
            for index, query in enumerate(values, start=1)
        ),
        round_review=round_review,
    )


def _fallback_intent(category: str) -> QueryIntent:
    normalized = category.casefold()
    if "sport" in normalized and ("bet" in normalized or "gambl" in normalized):
        return "sports_betting"
    if any(token in normalized for token in ("gambl", "casino", "rummy", "poker", "bet")):
        return "game_gambling"
    if any(token in normalized for token in ("local", "regional", "country")):
        return "local_exploration"
    return "format_exploration"


def _controlled_text_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _short_text(item)
        if not text or text.casefold() in seen:
            continue
        output.append(text)
        seen.add(text.casefold())
        if len(output) >= limit:
            break
    return output


def _short_text(value: Any, *, limit: int = 160) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if text and len(text) <= limit else None


def _non_negative_int(value: Any) -> int:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return 0
    return max(int(value), 0)


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


def _mock_visual_score(media: PreparedAdMedia) -> dict[str, Any]:
    media_quality_points = 5.0 if media.local_frame_paths else 0.0
    return {
        "core_gambling_points": 0.0,
        "reward_ui_points": 0.0,
        "gambling_style_points": 0.0,
        "casino_context_points": 0.0,
        "media_quality_points": media_quality_points,
        "analysis_confidence": 1.0 if media.local_frame_paths else 0.0,
        "visible_elements": [],
        "visual_evidence": [],
        "uncertain": not bool(media.local_frame_paths),
        "visual_total": media_quality_points,
    }


def _validated_visual_score(data: Any, *, frame_count: int) -> dict[str, Any]:
    payload = data if isinstance(data, dict) else {}
    result = {
        "core_gambling_points": _bounded_score(payload.get("core_gambling_points"), 40.0),
        "reward_ui_points": _bounded_score(payload.get("reward_ui_points"), 20.0),
        "gambling_style_points": _bounded_score(payload.get("gambling_style_points"), 15.0),
        "casino_context_points": _bounded_score(payload.get("casino_context_points"), 10.0),
        "media_quality_points": _bounded_score(payload.get("media_quality_points"), 5.0),
        "analysis_confidence": _bounded_score(payload.get("analysis_confidence"), 1.0),
        "visible_elements": _strings(payload.get("visible_elements"), limit=12, width=120),
        "visual_evidence": _visual_evidence(payload.get("visual_evidence"), frame_count),
        "uncertain": bool(payload.get("uncertain")),
    }
    result["visual_total"] = round(
        sum(
            float(result[key])
            for key in (
                "core_gambling_points",
                "reward_ui_points",
                "gambling_style_points",
                "casino_context_points",
                "media_quality_points",
            )
        ),
        2,
    )
    return result


def _bounded_score(value: Any, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(maximum, parsed))


def _strings(value: Any, *, limit: int, width: int) -> list[str]:
    if not isinstance(value, list):
        return []
    values: list[str] = []
    for item in value:
        rendered = str(item).strip()
        if rendered:
            values.append(rendered[:width])
        if len(values) >= limit:
            break
    return values


def _visual_evidence(value: Any, frame_count: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    evidence: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        frame_index = item.get("frame_index")
        detail = str(item.get("detail") or "").strip()
        if (
            isinstance(frame_index, bool)
            or not isinstance(frame_index, int)
            or frame_index < 0
            or frame_index >= frame_count
            or not detail
        ):
            continue
        evidence.append({"frame_index": frame_index, "detail": detail[:300]})
        if len(evidence) >= 12:
            break
    return evidence
