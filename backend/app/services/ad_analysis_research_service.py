from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.db.models.ad_analysis_reference_ad import AdAnalysisReferenceAd
from backend.app.db.models.ad_performance_analysis import AdPerformanceAnalysis


@dataclass(frozen=True)
class PublicResearchResult:
    summary: dict[str, Any]


class PublicAdSearchProvider(Protocol):
    async def search(self, query_profile: dict[str, Any]) -> list[dict[str, Any]]: ...


class DuckDuckGoHTMLPublicAdSearchProvider:
    """First public-source provider for similar Facebook ad discovery.

    This provider queries public web search only. It does not log in to Meta,
    scrape private surfaces, bypass access controls, or treat public snippets as
    verified delivery metrics. Results are normalized as creative/proxy signals.
    """

    provider_name = "duckduckgo_html"

    def __init__(
        self,
        *,
        timeout_seconds: float = 6.0,
        max_results: int = 3,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_results = max(1, min(int(max_results), 10))

    async def search(self, query_profile: dict[str, Any]) -> list[dict[str, Any]]:
        queries = _build_public_search_queries(query_profile)
        if not queries:
            return []

        timeout = httpx.Timeout(self.timeout_seconds, connect=min(self.timeout_seconds, 3.0))
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; AdvertisingAutomationAdResearch/1.0; "
                "+https://ai.ggcss.xyz)"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        collected: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            trust_env=False,
        ) as client:
            for query in queries:
                response = await client.get(
                    "https://duckduckgo.com/html/",
                    params={"q": query},
                    headers=headers,
                )
                response.raise_for_status()
                for raw in _extract_duckduckgo_results(response.text):
                    source_url = _public_result_url(str(raw.get("source_url") or ""))
                    if not source_url or source_url in seen_urls:
                        continue
                    seen_urls.add(source_url)
                    collected.append(
                        _reference_from_search_result(
                            raw,
                            source_url=source_url,
                            query=query,
                            query_profile=query_profile,
                            index=len(collected) + 1,
                        )
                    )
                    if len(collected) >= self.max_results:
                        return collected
        return collected


class AdAnalysisResearchService:
    """Public similar-ad research boundary for external Facebook analysis.

    The first production version is intentionally provider-agnostic and safe:
    it infers a query profile automatically, persists selected references when a
    future provider supplies them, and otherwise degrades to disabled/skipped
    mode. Public sources are never treated as verified Meta delivery metrics.
    """

    def __init__(self, search_provider: PublicAdSearchProvider | None = None) -> None:
        self._search_provider = search_provider

    async def research_and_persist(
        self,
        session: AsyncSession,
        analysis: AdPerformanceAnalysis,
        payload: dict[str, Any],
        *,
        media_summary: dict[str, Any] | None = None,
    ) -> PublicResearchResult:
        summary = await self.research(payload, media_summary=media_summary)
        await self.persist_selected_references(session, analysis, summary)
        return PublicResearchResult(summary=summary)

    async def research(
        self,
        payload: dict[str, Any],
        *,
        media_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        settings = get_settings()
        query_profile = _query_profile(payload, media_summary or {})
        enabled = bool(
            getattr(settings, "ad_analysis_public_research_enabled", True)
            and getattr(settings, "public_research_enabled", True)
        )
        if not enabled:
            return {
                "status": "disabled",
                "provider": None,
                "query_profile": query_profile,
                "selected_reference_ads": [],
                "warnings": [],
                "limitations": [
                    "Public research is disabled by configuration; no live similar-ad "
                    "sources were queried.",
                    "Only caller-submitted Facebook delivery metrics are treated as "
                    "verified performance facts.",
                ],
            }

        provider = self._search_provider or DuckDuckGoHTMLPublicAdSearchProvider(
            timeout_seconds=float(
                getattr(settings, "ad_analysis_public_research_timeout_seconds", 6.0)
            ),
            max_results=int(getattr(settings, "ad_analysis_public_research_max_results", 3)),
        )
        provider_name = str(getattr(provider, "provider_name", "duckduckgo_html"))
        try:
            raw_references = await provider.search(query_profile)
        except Exception as exc:  # noqa: BLE001 - public research must not fail the job.
            return {
                "status": "unavailable",
                "provider": provider_name,
                "query_profile": query_profile,
                "selected_reference_ads": [],
                "warnings": [
                    "Public similar-ad research was unavailable and the analysis "
                    f"continued without it: {str(exc) or exc.__class__.__name__}"
                ],
                "limitations": [
                    "Public sources are creative/proxy references only and cannot "
                    "verify CTR, CPC, CPA, purchases, revenue, or ROAS.",
                    "Only caller-submitted Facebook delivery metrics are treated as "
                    "verified performance facts.",
                ],
            }

        references: list[dict[str, Any]] = []
        for reference in raw_references:
            if not isinstance(reference, dict):
                continue
            source_url = _public_result_url(str(reference.get("source_url") or ""))
            if not source_url:
                continue
            clean_reference = dict(reference)
            clean_reference["source_url"] = source_url
            references.append(_normalize_reference(clean_reference, len(references) + 1))
            if len(references) >= 3:
                break
        warnings = [] if references else ["Public search returned no usable similar-ad references."]
        return {
            "status": "succeeded",
            "provider": provider_name,
            "query_profile": query_profile,
            "selected_reference_ads": references,
            "warnings": warnings,
            "limitations": [
                "Public sources are used only for similar creative patterns and "
                "market proxy signals.",
                "Public sources cannot verify CTR, CPC, CPA, purchases, revenue, or ROAS; "
                "those facts must come from the submitted Facebook delivery data.",
            ],
        }

    async def persist_selected_references(
        self,
        session: AsyncSession,
        analysis: AdPerformanceAnalysis,
        summary: dict[str, Any],
    ) -> None:
        await session.execute(
            delete(AdAnalysisReferenceAd).where(
                AdAnalysisReferenceAd.analysis_record_id == analysis.id
            )
        )
        references = summary.get("selected_reference_ads")
        if not isinstance(references, list):
            return
        for index, reference in enumerate(references, start=1):
            if not isinstance(reference, dict):
                continue
            source_url = _public_result_url(str(reference.get("source_url") or ""))
            if not source_url:
                continue
            clean_reference = dict(reference)
            clean_reference["source_url"] = source_url
            normalized = _normalize_reference(clean_reference, index)
            session.add(
                AdAnalysisReferenceAd(
                    analysis_record_id=analysis.id,
                    reference_id=normalized["reference_id"],
                    source_type=normalized["source_type"],
                    source_url=normalized["source_url"],
                    source_domain=_domain(normalized["source_url"]),
                    content_hash=_content_hash(normalized),
                    similarity_score=normalized.get("similarity_score"),
                    performance_evidence_json=normalized["performance_evidence"],
                    creative_analysis_json=normalized.get("creative_patterns") or {},
                    raw_excerpt_json=normalized,
                )
            )


def _query_profile(payload: dict[str, Any], media_summary: dict[str, Any]) -> dict[str, Any]:
    campaign = _dict(payload.get("campaign"))
    adset = _dict(payload.get("adset"))
    creative = _dict(payload.get("creative"))
    metadata = _dict(payload.get("metadata_json"))
    candidates = [
        campaign.get("name"),
        campaign.get("objective"),
        adset.get("name"),
        adset.get("optimization_goal"),
        adset.get("countries"),
        creative.get("name"),
        creative.get("headline"),
        creative.get("message"),
        metadata.get("product_name") or metadata.get("brand_name"),
    ]
    terms = _dedupe_terms(candidates)
    creative_type = str(creative.get("creative_type") or "unknown").lower()
    source_url = (
        creative.get("image_url")
        if creative_type == "image"
        else creative.get("video_url")
    )
    if creative_type == "carousel":
        image_urls = creative.get("image_urls")
        source_url = image_urls[0] if isinstance(image_urls, list) and image_urls else None
    return {
        "platform": "facebook",
        "creative_type": creative_type,
        "objective": campaign.get("objective"),
        "optimization_goal": adset.get("optimization_goal"),
        "countries": adset.get("countries"),
        "source_domain": _domain(str(source_url or ""))
        or _domain(str(media_summary.get("final_url") or media_summary.get("source_url") or "")),
        "terms": terms[:12],
        "query": " ".join(terms[:8]),
    }


def _normalize_reference(reference: dict[str, Any], index: int) -> dict[str, Any]:
    item = _strip_forbidden_public_metric_keys(dict(reference))
    item.setdefault("reference_id", f"reference-{index}")
    item.setdefault("source_type", "public_source")
    item.setdefault("source_url", "about:blank")
    item.setdefault("collected_at", _iso_now())
    try:
        item["similarity_score"] = max(0.0, min(float(item.get("similarity_score") or 0.0), 1.0))
    except (TypeError, ValueError):
        item["similarity_score"] = 0.0
    evidence = _dict(item.get("performance_evidence"))
    evidence.setdefault("type", "public_proxy_signals")
    evidence["verified"] = False
    evidence.setdefault("confidence", "unknown")
    evidence.setdefault("signals", [])
    limitations = (
        evidence.get("limitations") if isinstance(evidence.get("limitations"), list) else []
    )
    if not limitations:
        limitations = [
            "Public sources are creative/proxy references only; Meta delivery metrics "
            "are not verified."
        ]
    evidence["limitations"] = limitations
    item["performance_evidence"] = evidence
    item.setdefault("creative_patterns", {})
    item.setdefault("applicable_learnings", [])
    return item


_FORBIDDEN_PUBLIC_METRIC_KEYS = {
    "ctr",
    "cpc",
    "cpa",
    "purchase",
    "purchases",
    "revenue",
    "roas",
    "spend",
    "cpm",
    "cost_per_result",
    "purchase_value",
}


def _strip_forbidden_public_metric_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_forbidden_public_metric_keys(child)
            for key, child in value.items()
            if str(key).casefold() not in _FORBIDDEN_PUBLIC_METRIC_KEYS
        }
    if isinstance(value, list):
        return [_strip_forbidden_public_metric_keys(item) for item in value]
    return value


def _build_public_search_queries(query_profile: dict[str, Any]) -> list[str]:
    terms = [str(term).strip() for term in _list(query_profile.get("terms")) if str(term).strip()]
    if not terms:
        fallback = str(query_profile.get("query") or "").strip()
        if fallback:
            terms = [fallback]
    compact_terms = " ".join(terms[:5])
    objective = str(query_profile.get("objective") or "").strip()
    creative_type = str(query_profile.get("creative_type") or "").strip()
    candidates = [
        f"site:facebook.com/ads/library {compact_terms}".strip(),
        f"facebook ads library {compact_terms}".strip(),
        f"facebook ad examples {objective} {creative_type} {compact_terms}".strip(),
    ]
    result: list[str] = []
    seen: set[str] = set()
    for query in candidates:
        query = re.sub(r"\s+", " ", query).strip()
        if not query or query.casefold() in seen:
            continue
        seen.add(query.casefold())
        result.append(query)
    return result[:3]


def _extract_duckduckgo_results(html_text: str) -> list[dict[str, str]]:
    link_pattern = re.compile(
        r'<a[^>]+class=["\'][^"\']*result__a[^"\']*["\'][^>]+href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<title>.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    snippet_pattern = re.compile(
        r'<a[^>]+class=["\'][^"\']*result__snippet[^"\']*["\'][^>]*>(?P<snippet>.*?)</a>|<div[^>]+class=["\'][^"\']*result__snippet[^"\']*["\'][^>]*>(?P<snippet_div>.*?)</div>',
        re.IGNORECASE | re.DOTALL,
    )
    snippets = [
        _clean_html(match.group("snippet") or match.group("snippet_div") or "")
        for match in snippet_pattern.finditer(html_text)
    ]
    results: list[dict[str, str]] = []
    for index, match in enumerate(link_pattern.finditer(html_text)):
        title = _clean_html(match.group("title"))
        href = html.unescape(match.group("href"))
        if not title or not href:
            continue
        results.append(
            {
                "source_url": href,
                "title": title,
                "snippet": snippets[index] if index < len(snippets) else "",
            }
        )
    return results


def _reference_from_search_result(
    raw: dict[str, Any],
    *,
    source_url: str,
    query: str,
    query_profile: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    title = str(raw.get("title") or "").strip()
    snippet = str(raw.get("snippet") or "").strip()
    domain = _domain(source_url) or "public source"
    matched_terms = _matched_terms(f"{title} {snippet}", _list(query_profile.get("terms")))
    confidence = "medium" if len(matched_terms) >= 2 else "low"
    similarity_score = min(0.85, 0.35 + len(matched_terms) * 0.1)
    return {
        "reference_id": f"public-search-{index}",
        "source_type": "public_search",
        "source_url": source_url,
        "advertiser_name": domain,
        "collected_at": _iso_now(),
        "similarity_score": similarity_score,
        "performance_evidence": {
            "type": "public_proxy_signals",
            "verified": False,
            "confidence": confidence,
            "signals": [
                "Public search result matched the submitted ad context.",
                (
                    f"Matched terms: {', '.join(matched_terms[:5])}"
                    if matched_terms
                    else "No exact submitted terms matched the snippet."
                ),
                f"Source domain: {domain}",
            ],
            "limitations": [
                (
                    "This reference is a public creative/proxy signal, not verified Meta "
                    "delivery data."
                ),
                "CTR, CPC, CPA, purchases, revenue, and ROAS cannot be verified from this source.",
            ],
        },
        "creative_patterns": {
            "public_title": title,
            "public_excerpt": snippet,
            "search_query": query,
        },
        "applicable_learnings": [
            "Compare hook, offer, visual framing, and CTA against the submitted creative; "
            "do not treat public visibility as proof of delivery performance."
        ],
    }


def _matched_terms(text: str, terms: list[Any]) -> list[str]:
    haystack = text.casefold()
    result: list[str] = []
    for term in terms:
        candidate = str(term or "").strip()
        if len(candidate) < 3:
            continue
        if candidate.casefold() in haystack:
            result.append(candidate)
    return result


def _public_result_url(url: str) -> str | None:
    url = url.strip()
    if not url or any(character in url for character in {'"', "'", "<", ">", "(", ")"}):
        return None
    if url.startswith("//"):
        url = f"https:{url}"
    parsed = urlparse(url)
    if parsed.hostname and parsed.hostname.endswith("duckduckgo.com"):
        query = parse_qs(parsed.query)
        uddg = query.get("uddg") or query.get("u")
        if uddg:
            url = unquote(str(uddg[0]))
            parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    if parsed.hostname.lower() in {"facebook.com", "www.facebook.com"}:
        path = re.sub(r"/+", "/", parsed.path)
        if path not in {"/ads/library", "/ads/library/"}:
            return None
    return url


def _clean_html(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()


def _dedupe_terms(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if isinstance(value, list):
            parts = value
        else:
            parts = [value]
        for part in parts:
            text = str(part or "").strip()
            if not text:
                continue
            if len(text) > 160:
                text = text[:160]
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(text)
    return result


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _domain(url: str) -> str | None:
    if not url:
        return None
    host = urlparse(url).hostname
    return host.lower() if host else None


def _content_hash(value: dict[str, Any]) -> str:
    import json

    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _iso_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
