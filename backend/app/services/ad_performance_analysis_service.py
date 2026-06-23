from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from statistics import median
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.db.models.ad_performance_analysis import AdPerformanceAnalysis
from backend.app.integrations.llm.factory import get_llm_provider
from backend.app.schemas.ad_performance import (
    AdPerformanceAIAnalysis,
    AdPerformanceAnalysisCreate,
    AdPerformanceOptimizationWorkOrder,
    AdPerformanceProblem,
    AdPerformanceRecommendation,
)
from backend.app.services.utils import get_required

LOW_SAMPLE_IMPRESSIONS = 100
ENOUGH_CLICKS_FOR_LANDING_RATE = 20
HIGH_CTR_PERCENT = 3.0
LOW_CTR_PERCENT = 1.0
LOW_LANDING_VIEW_RATE = 0.5
HIGH_FREQUENCY = 3.0
LLM_ERROR_PREFIX = "LLM analysis failed"

logger = logging.getLogger(__name__)


class AdPerformanceAnalysisService:
    async def create_analysis(
        self,
        session: AsyncSession,
        payload: AdPerformanceAnalysisCreate,
    ) -> AdPerformanceAnalysis:
        request_payload = payload.model_dump(mode="json")
        campaign = _record_from_payload(request_payload, "campaign", ("campaign_payload",))
        adset = _record_from_payload(request_payload, "adset", ("ad_set", "adset_payload"))
        creative = _record_from_payload(
            request_payload,
            "creative",
            ("ad", "creative_payload", "ad_payload"),
        )
        insight = _insight_record(
            _record_from_payload(request_payload, "insight", ("insights", "performance"))
        )
        metrics = _build_metrics(campaign, adset, creative, insight, request_payload)
        analysis_result = _llm_only_analysis_result(metrics)
        analysis_result["data_completeness"] = _build_data_completeness(
            metrics=metrics,
            campaign=campaign,
            adset=adset,
            creative=creative,
            insight=insight,
            request_payload=request_payload,
        )
        analysis_result, error_message = await _append_llm_analysis(
            analysis_result=analysis_result,
            metrics=metrics,
            campaign=campaign,
            adset=adset,
            creative=creative,
            insight=insight,
            request_payload=request_payload,
        )
        analysis_result["optimization_work_order"] = _optimization_work_order_from_result(
            metrics=metrics,
            campaign=campaign,
            adset=adset,
            creative=creative,
            request_payload=request_payload,
            analysis_result=analysis_result,
        )

        analysis = AdPerformanceAnalysis(
            external_user_id=_trim_text(request_payload.get("external_user_id"), 128),
            source_type=_trim_text(request_payload.get("source_type"), 32) or "unknown",
            status="completed",
            campaign_external_id=_first_text(
                campaign.get("fb_id"),
                insight.get("campaign_id"),
                campaign.get("campaign_id"),
                campaign.get("id"),
            ),
            campaign_name=_trim_text(
                _first_text(campaign.get("name"), insight.get("campaign_name")), 255
            ),
            adset_external_id=_first_text(
                adset.get("fb_id"),
                insight.get("adset_id"),
                adset.get("adset_id"),
                adset.get("id"),
            ),
            adset_name=_trim_text(_first_text(adset.get("name"), insight.get("adset_name")), 255),
            creative_external_id=_first_text(
                insight.get("ad_id"),
                creative.get("facebook_ad_id"),
                creative.get("fb_id"),
                creative.get("ad_id"),
                creative.get("id"),
            ),
            creative_name=_trim_text(
                _first_text(creative.get("name"), insight.get("ad_name"), creative.get("ad_name")),
                255,
            ),
            date_start=_trim_text(
                _first_text(request_payload.get("date_start"), insight.get("date_start")), 32
            ),
            date_stop=_trim_text(
                _first_text(request_payload.get("date_stop"), insight.get("date_stop")), 32
            ),
            request_payload=request_payload,
            metrics=metrics,
            analysis_result=analysis_result,
            error_message=error_message,
        )
        session.add(analysis)
        await session.commit()
        await session.refresh(analysis)
        return analysis

    async def list_analyses(
        self,
        session: AsyncSession,
        limit: int,
        offset: int,
        creative_external_id: str | None = None,
    ) -> list[AdPerformanceAnalysis]:
        statement = select(AdPerformanceAnalysis).order_by(AdPerformanceAnalysis.created_at.desc())
        if creative_external_id:
            statement = statement.where(
                AdPerformanceAnalysis.creative_external_id == creative_external_id
            )
        result = await session.execute(statement.limit(limit).offset(offset))
        return list(result.scalars().all())

    async def get_analysis(
        self,
        session: AsyncSession,
        analysis_id: str,
    ) -> AdPerformanceAnalysis:
        return await get_required(session, AdPerformanceAnalysis, analysis_id)  # type: ignore[return-value]

    async def delete_analysis(self, session: AsyncSession, analysis_id: str) -> None:
        analysis = await self.get_analysis(session, analysis_id)
        await session.delete(analysis)
        await session.commit()

    async def stream_ai_analysis(
        self,
        session: AsyncSession,
        analysis_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        analysis = await self.get_analysis(session, analysis_id)
        request_payload = analysis.request_payload or {}
        campaign = _record_from_payload(request_payload, "campaign", ("campaign_payload",))
        adset = _record_from_payload(request_payload, "adset", ("ad_set", "adset_payload"))
        creative = _record_from_payload(
            request_payload,
            "creative",
            ("ad", "creative_payload", "ad_payload"),
        )
        insight = _insight_record(
            _record_from_payload(request_payload, "insight", ("insights", "performance"))
        )
        metrics = analysis.metrics or _build_metrics(
            campaign, adset, creative, insight, request_payload
        )
        base_result = _analysis_result_for_llm_retry(analysis.analysis_result or {})
        context = _llm_analysis_context(
            metrics=metrics,
            campaign=campaign,
            adset=adset,
            creative=creative,
            insight=insight,
            analysis_context=base_result,
            request_payload=request_payload,
        )

        yield {"type": "start", "analysis_id": analysis.id}

        try:
            provider = get_llm_provider(get_settings())
            async for event in provider.stream_ad_performance_analysis(context):
                event_type = event.get("type")
                if event_type == "delta":
                    text = event.get("text")
                    if text:
                        yield {"type": "delta", "analysis_id": analysis.id, "text": text}
                elif event_type == "done":
                    ai_analysis = AdPerformanceAIAnalysis.model_validate(
                        event.get("analysis") or {}
                    ).model_dump(mode="json")
                    updated_result = {
                        **base_result,
                        "analysis_mode": "llm_only",
                        "ai_analysis": ai_analysis,
                        "summary": ai_analysis.get("summary") or base_result.get("summary"),
                        "llm_error": None,
                    }
                    updated_result["optimization_work_order"] = (
                        _optimization_work_order_from_result(
                            metrics=metrics,
                            campaign=campaign,
                            adset=adset,
                            creative=creative,
                            request_payload=request_payload,
                            analysis_result=updated_result,
                        )
                    )
                    analysis.metrics = metrics
                    analysis.analysis_result = updated_result
                    analysis.error_message = None
                    session.add(analysis)
                    await session.commit()
                    await session.refresh(analysis)
                    yield {
                        "type": "done",
                        "analysis_id": analysis.id,
                        "ai_analysis": ai_analysis,
                    }
                    return
            raise RuntimeError("LLM stream ended without a final analysis.")
        except Exception as exc:  # noqa: BLE001 - stream errors are sent to the browser.
            error = _trim_text(str(exc), 500) or exc.__class__.__name__
            logger.warning("%s: %s", LLM_ERROR_PREFIX, error, exc_info=True)
            failed_result = _analysis_result_with_llm_error(
                analysis.analysis_result or base_result,
                error,
            )
            analysis.analysis_result = failed_result
            analysis.error_message = f"{LLM_ERROR_PREFIX}: {error}"
            session.add(analysis)
            await session.commit()
            yield {"type": "error", "analysis_id": analysis.id, "message": error}


def _build_metrics(
    campaign: dict[str, Any],
    adset: dict[str, Any],
    creative: dict[str, Any],
    insight: dict[str, Any],
    request_payload: dict[str, Any],
) -> dict[str, Any]:
    impressions = _number(insight.get("impressions"))
    reach = _number(insight.get("reach"))
    spend = _number(insight.get("spend"))
    clicks = _number(insight.get("clicks"))
    inline_link_clicks = _number(insight.get("inline_link_clicks"))
    link_clicks = _action_value(insight, "link_click") or inline_link_clicks or clicks
    landing_page_views = (
        _action_value(insight, "landing_page_view")
        or _action_value(insight, "omni_landing_page_view")
        or 0
    )
    ctr = _number(insight.get("ctr"))
    if ctr is None and impressions and clicks is not None:
        ctr = _safe_rate(clicks, impressions) * 100
    inline_link_click_ctr = _number(insight.get("inline_link_click_ctr"))
    if inline_link_click_ctr is None and impressions and inline_link_clicks is not None:
        inline_link_click_ctr = _safe_rate(inline_link_clicks, impressions) * 100

    video_play = _action_array_value(insight, "video_play_actions") or _action_value(
        insight, "video_view"
    )
    video_p25 = _action_array_value(insight, "video_p25_watched_actions")
    video_p50 = _action_array_value(insight, "video_p50_watched_actions")
    video_p75 = _action_array_value(insight, "video_p75_watched_actions")
    video_p95 = _action_array_value(insight, "video_p95_watched_actions")
    video_p100 = _action_array_value(insight, "video_p100_watched_actions")
    conversion_actions = _conversion_actions(insight)

    sibling_metrics = _sibling_metrics(request_payload.get("siblings"))

    return {
        "campaign_objective": _text(campaign.get("objective")),
        "adset_optimization_goal": _text(adset.get("optimization_goal")),
        "adset_billing_event": _text(adset.get("billing_event")),
        "bid_strategy": _text(adset.get("bid_strategy")),
        "countries": _text(adset.get("countries")),
        "age_min": _number(adset.get("age_min")),
        "age_max": _number(adset.get("age_max")),
        "creative_type": _text(creative.get("creative_type") or creative.get("asset_type")),
        "ad_status": _text(creative.get("ad_status") or creative.get("status")),
        "spend": _round(spend),
        "impressions": _round(impressions),
        "reach": _round(reach),
        "frequency": _round(_number(insight.get("frequency"))),
        "clicks": _round(clicks),
        "inline_link_clicks": _round(inline_link_clicks),
        "link_clicks": _round(link_clicks),
        "landing_page_views": _round(landing_page_views),
        "ctr": _round(ctr),
        "inline_link_click_ctr": _round(inline_link_click_ctr),
        "cpc": _round(_number(insight.get("cpc"))),
        "cpm": _round(_number(insight.get("cpm"))),
        "landing_page_view_rate": _round(_safe_rate(landing_page_views, link_clicks)),
        "video_play": _round(video_play),
        "video_play_rate": _round(_safe_rate(video_play, impressions)),
        "video_p25": _round(video_p25),
        "video_p25_rate": _round(_safe_rate(video_p25, video_play)),
        "video_p50": _round(video_p50),
        "video_p50_rate": _round(_safe_rate(video_p50, video_play)),
        "video_p75": _round(video_p75),
        "video_p75_rate": _round(_safe_rate(video_p75, video_play)),
        "video_p95": _round(video_p95),
        "video_p95_rate": _round(_safe_rate(video_p95, video_play)),
        "video_p100": _round(video_p100),
        "video_p100_rate": _round(_safe_rate(video_p100, video_play)),
        "conversion_actions": conversion_actions,
        "sibling_metrics": sibling_metrics,
    }


def _llm_only_analysis_result(metrics: dict[str, Any]) -> dict[str, Any]:
    impressions = _as_float(metrics.get("impressions")) or 0
    spend = _as_float(metrics.get("spend")) or 0
    confidence = "low" if impressions < LOW_SAMPLE_IMPRESSIONS or spend <= 0 else "medium"
    return {
        "summary": "等待大模型生成投放分析。",
        "confidence": confidence,
        "analysis_mode": "llm_only",
        "data_completeness": {
            "level": "unknown",
            "score": 0,
            "available": [],
            "missing": [],
            "can_analyze": [],
            "cannot_analyze": [],
            "notes": [],
        },
        "optimization_work_order": {},
        "problems": [],
        "recommendations": [],
        "next_checks": [],
        "ai_analysis": None,
        "rule_summary": None,
        "llm_error": None,
    }


def _analyze(
    metrics: dict[str, Any],
    campaign: dict[str, Any],
    adset: dict[str, Any],
    creative: dict[str, Any],
    request_payload: dict[str, Any],
) -> dict[str, Any]:
    problems: list[AdPerformanceProblem] = []
    recommendations: list[AdPerformanceRecommendation] = []
    next_checks: list[str] = []

    impressions = _as_float(metrics.get("impressions")) or 0
    spend = _as_float(metrics.get("spend")) or 0
    ctr = _as_float(metrics.get("ctr"))
    link_clicks = _as_float(metrics.get("link_clicks")) or 0
    landing_page_rate = _as_float(metrics.get("landing_page_view_rate"))
    frequency = _as_float(metrics.get("frequency"))
    creative_type = (_text(metrics.get("creative_type")) or "").lower()
    ad_status = (_text(metrics.get("ad_status")) or "").upper()
    confidence = "medium"

    if impressions < LOW_SAMPLE_IMPRESSIONS or spend <= 0:
        confidence = "low"
        problems.append(
            _problem(
                "insufficient_data",
                "warning",
                "delivery",
                "数据量不足，暂时不能判断素材好坏",
                [
                    f"impressions={_display_number(impressions)}",
                    f"spend={_display_number(spend)}",
                    f"ad_status={ad_status or '-'}",
                ],
                "当前曝光或花费太少，模型只适合判断投放是否开始跑量，不能直接判定标题、文案、图片或视频质量。",
            )
        )
        recommendations.append(
            _recommendation(
                "check_delivery_before_creative_judgement",
                "high",
                "delivery",
                "先检查投放状态、预算、排期和受众规模",
                (
                    "至少等到单条广告有 500-1000 次曝光，或有稳定花费后，"
                    "再让 AI 判断素材本身是否需要重做。"
                ),
            )
        )
        next_checks.extend(
            ["广告是否 ACTIVE", "广告组预算是否足够", "受众是否过窄", "账户是否有审核或学习期限制"]
        )

    if ad_status == "PAUSED":
        problems.append(
            _problem(
                "ad_paused",
                "warning",
                "delivery",
                "广告当前处于暂停状态",
                [f"ad_status={ad_status}"],
                "暂停状态会导致后续数据停止增长，分析结果只能代表暂停前的历史表现。",
            )
        )
        recommendations.append(
            _recommendation(
                "confirm_pause_reason",
                "medium",
                "delivery",
                "确认广告是人工暂停还是系统投放受限",
                "如果是人工暂停，先确认是否因为测试结束；如果不是人工暂停，需要排查广告、广告组或账户层级状态。",
            )
        )

    if impressions >= LOW_SAMPLE_IMPRESSIONS:
        if ctr is not None and ctr < LOW_CTR_PERCENT:
            confidence = _raise_confidence(confidence)
            problems.append(
                _problem(
                    "low_ctr",
                    "critical",
                    "creative",
                    "点击率偏低，优先怀疑创意吸引力或受众匹配",
                    [f"ctr={_display_percent(ctr)}", f"impressions={_display_number(impressions)}"],
                    (
                        "用户看到了广告但不愿意点，常见原因是前 3 秒钩子弱、"
                        "标题利益点不清、图片/视频不够直观，或受众和素材不匹配。"
                    ),
                )
            )
            recommendations.append(
                _recommendation(
                    "improve_hook_and_visual",
                    "high",
                    "creative",
                    "重做首屏钩子、标题和主视觉",
                    (
                        "先做 2-3 个差异明显的版本：一个突出结果承诺，"
                        "一个突出痛点，一个突出玩法/产品演示；同时缩窄或重拆受众测试。"
                    ),
                )
            )
            next_checks.extend(
                ["标题是否直接给出利益点", "图片/视频首屏是否一眼看懂", "受众是否过宽或兴趣不匹配"]
            )

        if (
            ctr is not None
            and ctr >= HIGH_CTR_PERCENT
            and landing_page_rate is not None
            and landing_page_rate < LOW_LANDING_VIEW_RATE
            and link_clicks >= ENOUGH_CLICKS_FOR_LANDING_RATE
        ):
            confidence = _raise_confidence(confidence)
            problems.append(
                _problem(
                    "post_click_drop",
                    "critical",
                    "landing_page",
                    "点击率不错，但落地页到达率偏低",
                    [
                        f"ctr={_display_percent(ctr)}",
                        f"link_clicks={_display_number(link_clicks)}",
                        f"landing_page_view_rate={_display_ratio(landing_page_rate)}",
                    ],
                    "广告能吸引点击，问题更像发生在点击之后：落地页加载慢、链接跳转异常、像素/事件统计不完整，或广告承诺与页面内容不一致。",
                )
            )
            recommendations.append(
                _recommendation(
                    "fix_landing_page_path",
                    "high",
                    "landing_page",
                    "优先检查落地页速度、跳转链路和事件埋点",
                    (
                        "不要先急着否定标题、文案或图片；先用手机网络打开链接，"
                        "检查加载时间、重定向、地区访问、像素 landing_page_view 是否正常。"
                    ),
                )
            )
            next_checks.extend(
                [
                    "落地页移动端打开速度",
                    "URL 是否能在投放地区访问",
                    "Meta 像素 landing_page_view 是否触发",
                ]
            )

        if frequency is not None and frequency > HIGH_FREQUENCY:
            problems.append(
                _problem(
                    "frequency_fatigue",
                    "warning",
                    "audience",
                    "频次偏高，存在疲劳风险",
                    [f"frequency={_display_number(frequency)}"],
                    "同一批人反复看到广告后，点击和转化通常会下降，需要扩受众或换素材。",
                )
            )
            recommendations.append(
                _recommendation(
                    "refresh_audience_or_creative",
                    "medium",
                    "audience",
                    "扩展受众或上新素材",
                    (
                        "如果 CTR 已经开始下滑，优先换首图/前三秒；"
                        "如果 CTR 还稳但转化差，先拆受众和版位看差异。"
                    ),
                )
            )

        if "video" in creative_type:
            _append_video_analysis(metrics, problems, recommendations, next_checks)

    _append_objective_analysis(metrics, campaign, adset, problems, recommendations, next_checks)
    _append_sibling_comparison(metrics, problems, recommendations)

    if not problems:
        confidence = "medium"
        problems.append(
            _problem(
                "no_obvious_issue",
                "info",
                "overall",
                "暂未发现明显异常",
                [],
                "当前关键指标没有触发明显风险规则，可以继续观察并做小规模 A/B 测试。",
            )
        )
        recommendations.append(
            _recommendation(
                "continue_ab_testing",
                "medium",
                "overall",
                "继续累积数据并保持 A/B 测试",
                (
                    "建议同一广告组下保持 2-4 个素材方向，"
                    "观察 CTR、落地页到达率和最终转化指标的共同变化。"
                ),
            )
        )

    summary = _summary(metrics, problems, confidence, request_payload)
    return {
        "summary": summary,
        "confidence": confidence,
        "analysis_mode": "rules_only",
        "data_completeness": {
            "level": "unknown",
            "score": 0,
            "available": [],
            "missing": [],
            "can_analyze": [],
            "cannot_analyze": [],
            "notes": [],
        },
        "problems": [problem.model_dump(mode="json") for problem in problems],
        "recommendations": [
            recommendation.model_dump(mode="json") for recommendation in recommendations
        ],
        "next_checks": _unique(next_checks),
        "ai_analysis": None,
        "rule_summary": summary,
        "llm_error": None,
    }


def _build_data_completeness(
    metrics: dict[str, Any],
    campaign: dict[str, Any],
    adset: dict[str, Any],
    creative: dict[str, Any],
    insight: dict[str, Any],
    request_payload: dict[str, Any],
) -> dict[str, Any]:
    available: list[str] = []
    missing: list[str] = []
    can_analyze: list[str] = []
    cannot_analyze: list[str] = []
    notes: list[str] = []

    def mark(
        has_data: bool,
        label: str,
        can_text: str | None = None,
        cannot_text: str | None = None,
    ) -> int:
        if has_data:
            available.append(label)
            if can_text:
                can_analyze.append(can_text)
            return 1
        missing.append(label)
        if cannot_text:
            cannot_analyze.append(cannot_text)
        return 0

    has_performance_metrics = any(
        _as_float(metrics.get(key)) is not None
        for key in ("spend", "impressions", "clicks", "ctr", "cpc", "cpm")
    )
    has_campaign_goal = bool(
        _first_text(campaign.get("objective"), metrics.get("campaign_objective"))
    )
    has_adset_settings = any(
        _has_value(adset.get(key))
        for key in (
            "optimization_goal",
            "billing_event",
            "bid_strategy",
            "countries",
            "age_min",
            "age_max",
        )
    )
    has_audience = any(_has_value(adset.get(key)) for key in ("countries", "age_min", "age_max"))
    has_copy = bool(
        _first_text(
            creative.get("message"),
            creative.get("primary_text"),
            creative.get("body"),
            creative.get("text"),
        )
    )
    has_title = bool(
        _first_text(
            creative.get("headline"),
            creative.get("title"),
            creative.get("ads_name"),
            creative.get("ad_title"),
        )
    )
    has_description = bool(_first_text(creative.get("description"), creative.get("caption")))
    has_landing_url = bool(
        _first_text(
            creative.get("link"),
            creative.get("landing_url"),
            request_payload.get("landing_url"),
        )
    )
    creative_type = (_text(metrics.get("creative_type")) or "").lower()
    has_media_type = creative_type in {"image", "video"} or bool(creative_type)
    has_image_url = _has_any_field(
        creative,
        request_payload,
        keys=(
            "image_url",
            "imageUrl",
            "asset_image_url",
            "assetImageUrl",
            "image",
            "thumbnail_url",
            "thumbnailUrl",
            "cover_url",
            "coverUrl",
            "preview_url",
            "previewUrl",
        ),
    )
    has_thumbnail = _has_any_field(
        creative,
        request_payload,
        keys=(
            "thumbnail_url",
            "thumbnailUrl",
            "cover_url",
            "coverUrl",
            "preview_url",
            "previewUrl",
        ),
    )
    has_video_url = _has_any_field(
        creative,
        request_payload,
        keys=("video_url", "videoUrl", "asset_video_url", "assetVideoUrl", "source_video_url"),
    )
    has_video_keyframes = _has_any_field(
        creative,
        request_payload,
        keys=("video_keyframes", "videoKeyframes", "keyframes", "frames"),
    )
    has_video_metrics = any(
        _as_float(metrics.get(key)) is not None
        for key in ("video_play", "video_p25_rate", "video_p50_rate", "video_p100_rate")
    )
    conversion_actions = metrics.get("conversion_actions")
    has_conversion = isinstance(conversion_actions, dict) and any(
        _as_float(value) for value in conversion_actions.values()
    )
    has_siblings = isinstance(metrics.get("sibling_metrics"), list) and bool(
        metrics.get("sibling_metrics")
    )

    score = 0
    score += 25 * mark(
        has_performance_metrics,
        "投放效果指标",
        "曝光、花费、点击、CTR、CPC/CPM 等基础表现",
        "投放表现是否异常",
    )
    score += 10 * mark(
        has_campaign_goal,
        "广告系列目标",
        "投放目标是否和业务目标一致",
        "广告系列目标是否设置合理",
    )
    score += 10 * mark(
        has_adset_settings,
        "广告组设置",
        "优化目标、计费方式、出价策略等投放设置",
        "广告组设置是否拖累效果",
    )
    score += 8 * mark(
        has_audience,
        "人群定向",
        "国家、年龄等基础人群范围",
        "人群是否精准或过窄",
    )
    score += 12 * mark(
        has_copy,
        "广告文案 message",
        "文案表达方向和卖点承接",
        "文案本身是否吸引",
    )
    score += 8 * mark(
        has_title,
        "广告标题 headline",
        "标题利益点和点击动机",
        "标题是否清楚有吸引力",
    )
    score += 4 * mark(
        has_description,
        "广告描述 description",
        "描述与标题、文案是否一致",
        "描述是否补充有效信息",
    )
    score += 5 * mark(
        has_landing_url,
        "落地页 URL",
        "广告承诺和落地页承接方向",
        "落地页链接和承接内容",
    )
    score += 8 * mark(
        has_media_type,
        "素材类型 image/video",
        "按图片或视频广告分别判断表现",
        "素材类型对应的问题方向",
    )

    if "video" in creative_type:
        score += 10 * mark(
            has_video_metrics,
            "视频播放指标",
            "视频播放率和 25%/50% 留存",
            "视频留存是否异常",
        )
        score += 10 * mark(
            has_video_url or has_video_keyframes,
            "视频 URL 或关键帧",
            "视频画面内容和具体秒点问题",
            "视频画面内容和第几秒出问题",
        )
        if has_thumbnail:
            available.append("视频封面 thumbnail_url")
            can_analyze.append("视频封面第一眼吸引力")
        else:
            missing.append("视频封面 thumbnail_url")
            cannot_analyze.append("视频封面第一眼吸引力")
    elif "image" in creative_type:
        score += 15 * mark(
            has_image_url,
            "图片 URL 或缩略图",
            "图片画面、构图、产品露出和第一眼吸引力",
            "图片画面具体哪里不好",
        )
    else:
        missing.append("图片/视频 URL")
        cannot_analyze.append("素材画面具体问题")

    score += 10 * mark(
        has_conversion,
        "转化事件 purchase/add_to_cart/lead",
        "真实业务转化质量",
        "真实业务转化质量",
    )
    if has_siblings:
        available.append("同组广告参考 siblings")
        can_analyze.append("同广告组素材间的辅助对比")
    else:
        missing.append("同组广告参考 siblings")
        cannot_analyze.append("同广告组素材横向对比")

    impressions = _as_float(metrics.get("impressions")) or 0
    spend = _as_float(metrics.get("spend")) or 0
    if impressions < LOW_SAMPLE_IMPRESSIONS or spend <= 0:
        notes.append("当前样本量或花费偏低，不能武断判断素材、标题或文案一定不好。")
    if not has_image_url and "image" in creative_type:
        notes.append("缺少图片 URL 或缩略图时，AI 只能根据投放数据推测图片问题。")
    if "video" in creative_type and not (has_video_url or has_video_keyframes):
        notes.append("缺少视频 URL 或关键帧时，AI 不能判断视频具体画面和秒点。")
    if not has_conversion:
        notes.append("缺少购买、加购或线索事件时，不能判断真实业务转化质量。")

    level = "high" if score >= 85 else "medium" if score >= 45 else "low"
    return {
        "level": level,
        "score": min(score, 100),
        "available": _unique(available),
        "missing": _unique(missing),
        "can_analyze": _unique(can_analyze),
        "cannot_analyze": _unique(cannot_analyze),
        "notes": _unique(notes),
    }


def _build_optimization_work_order(
    metrics: dict[str, Any],
    campaign: dict[str, Any],
    adset: dict[str, Any],
    creative: dict[str, Any],
    request_payload: dict[str, Any],
    analysis_result: dict[str, Any],
) -> dict[str, Any]:
    problems = (
        analysis_result.get("problems") if isinstance(analysis_result.get("problems"), list) else []
    )
    problem_codes = {
        str(problem.get("code"))
        for problem in problems
        if isinstance(problem, dict) and problem.get("code")
    }
    problem_codes.update(_performance_signal_codes(metrics, campaign, adset))
    has_critical = any(
        isinstance(problem, dict) and problem.get("severity") == "critical" for problem in problems
    )
    has_low_ctr = "low_ctr" in problem_codes
    has_post_click_drop = "post_click_drop" in problem_codes
    has_insufficient_data = "insufficient_data" in problem_codes
    has_traffic_warning = "traffic_optimization_warning" in problem_codes
    has_frequency_fatigue = "frequency_fatigue" in problem_codes
    creative_type = (
        _first_text(
            metrics.get("creative_type"), creative.get("creative_type"), creative.get("asset_type")
        )
        or ""
    ).lower()
    is_video = "video" in creative_type
    is_image = "image" in creative_type or not is_video
    ai_analysis = (
        analysis_result.get("ai_analysis")
        if isinstance(analysis_result.get("ai_analysis"), dict)
        else {}
    )
    creative_direction = _optimization_creative_direction(ai_analysis)

    priority = (
        "high"
        if has_critical or has_low_ctr or has_post_click_drop
        else "medium"
        if problem_codes
        else "low"
    )
    overall_action = "create_optimized_draft"
    next_step = "生成一份新版广告草稿，运营确认后再进入标题、文案、图片或视频生成流程。"
    if has_insufficient_data:
        overall_action = "check_delivery_first"
        next_step = "先检查投放状态、预算、排期和数据回传，样本稳定后再判断是否重做素材。"
    elif has_post_click_drop and not has_low_ctr:
        overall_action = "check_landing_page_first"
        next_step = "先检查落地页速度、跳转链路和像素事件，再决定是否生成新版素材。"
    elif not problem_codes or "no_obvious_issue" in problem_codes:
        overall_action = "continue_testing"
        next_step = "保留当前主设置，补充小预算 A/B 测试，不要直接覆盖线上广告。"

    campaign_fields = _campaign_optimization_fields(
        campaign=campaign,
        metrics=metrics,
        has_traffic_warning=has_traffic_warning,
    )
    adset_fields = _adset_optimization_fields(
        adset=adset,
        metrics=metrics,
        has_insufficient_data=has_insufficient_data,
        has_post_click_drop=has_post_click_drop,
        has_traffic_warning=has_traffic_warning,
        has_frequency_fatigue=has_frequency_fatigue,
    )
    creative_fields = _creative_optimization_fields(
        creative=creative,
        request_payload=request_payload,
        creative_type=creative_type,
        is_image=is_image,
        is_video=is_video,
        has_low_ctr=has_low_ctr,
        has_post_click_drop=has_post_click_drop,
        has_insufficient_data=has_insufficient_data,
        creative_direction=creative_direction,
    )
    all_fields = campaign_fields + adset_fields + creative_fields
    warnings = [
        "AI 只生成草稿建议，不直接修改线上广告。",
        "缺少外部系统字段时，需要运营在外部创建广告表单中选择。",
    ]
    data_completeness = (
        analysis_result.get("data_completeness")
        if isinstance(analysis_result.get("data_completeness"), dict)
        else {}
    )
    notes = (
        data_completeness.get("notes") if isinstance(data_completeness.get("notes"), list) else []
    )
    warnings.extend(str(note) for note in notes[:2] if note)

    return {
        "schema_version": "ad_performance_optimization_work_order_v1",
        "operator_summary": _optimization_operator_summary(
            analysis_result=analysis_result,
            problem_codes=problem_codes,
            metrics=metrics,
        ),
        "priority": priority,
        "overall_action": overall_action,
        "next_step": next_step,
        "modules_to_change": _optimization_module_labels(
            all_fields,
            {"regenerate", "rewrite", "check", "reduce", "increase", "pause", "missing"},
        ),
        "modules_to_keep": _optimization_module_labels(all_fields, {"keep", "create_draft"}),
        "modules_to_watch": _optimization_module_labels(all_fields, {"watch"}),
        "campaign": campaign_fields,
        "adset": adset_fields,
        "creative": creative_fields,
        "warnings": _unique(warnings),
    }


def _campaign_optimization_fields(
    campaign: dict[str, Any],
    metrics: dict[str, Any],
    has_traffic_warning: bool,
) -> list[dict[str, Any]]:
    objective = _first_text(metrics.get("campaign_objective"), campaign.get("objective"))
    return [
        _optimization_field(
            "name",
            "广告系列名称",
            _first_value(campaign.get("name"), campaign.get("campaign_name")),
            "keep",
            "low",
            "名称只影响识别，不建议因为效果波动而频繁改名。",
        ),
        _optimization_field(
            "objective",
            "广告目标",
            objective,
            "rewrite" if has_traffic_warning else "keep",
            "high" if has_traffic_warning else "medium",
            (
                "当前更偏点击或流量优化，如果最终看购买、注册或线索，建议改成与业务转化一致的目标。"
                if has_traffic_warning
                else "当前广告目标可以保留，暂不建议因为单条素材表现直接改广告系列目标。"
            ),
            suggested_value="转化 / PURCHASE / LEAD" if has_traffic_warning else objective,
        ),
        _optimization_field(
            "status",
            "状态",
            _first_text(campaign.get("status"), "PAUSED"),
            "create_draft",
            "medium",
            "优化建议先保存为草稿，由运营确认后再发布，不直接覆盖线上广告。",
            suggested_value="草稿",
        ),
    ]


def _adset_optimization_fields(
    adset: dict[str, Any],
    metrics: dict[str, Any],
    has_insufficient_data: bool,
    has_post_click_drop: bool,
    has_traffic_warning: bool,
    has_frequency_fatigue: bool,
) -> list[dict[str, Any]]:
    daily_budget = _first_value(
        adset.get("daily_budget"), adset.get("budget"), metrics.get("daily_budget")
    )
    optimization_goal = _first_text(
        metrics.get("adset_optimization_goal"), adset.get("optimization_goal")
    )
    billing_event = _first_text(metrics.get("adset_billing_event"), adset.get("billing_event"))
    countries = _first_value(adset.get("countries"), metrics.get("countries"))
    age_min = _first_value(adset.get("age_min"), metrics.get("age_min"))
    age_max = _first_value(adset.get("age_max"), metrics.get("age_max"))
    optimization_event = _adset_optimization_event(adset, metrics)

    budget_action = "watch"
    budget_reason = "预算先保持观察，等新版创意或落地页检查后再决定是否加量。"
    budget_priority = "medium"
    budget_suggestion: Any | None = daily_budget
    if has_insufficient_data:
        budget_reason = "当前样本太少，先确认预算是否足够跑出稳定曝光。"
    elif has_post_click_drop:
        budget_action = "reduce"
        budget_reason = "点击后流失明显，修复落地页前不建议继续加预算。"
        budget_suggestion = "保持或小幅降低"
        budget_priority = "high"

    return [
        _optimization_field(
            "name",
            "广告组名称",
            _first_value(adset.get("name"), adset.get("adset_name")),
            "keep",
            "low",
            "名称只用于管理识别，可以保留。",
        ),
        _optimization_field(
            "daily_budget",
            "日预算",
            daily_budget,
            budget_action,
            budget_priority,
            budget_reason,
            suggested_value=budget_suggestion,
        ),
        _optimization_field(
            "billing_event",
            "计费方式",
            billing_event,
            "keep" if billing_event else "missing",
            "medium",
            "已有计费方式可以保留；缺失时需要外部系统或运营补选。",
            suggested_value=billing_event or "IMPRESSIONS",
            missing=not bool(billing_event),
        ),
        _optimization_field(
            "optimization_goal",
            "优化目标",
            optimization_goal,
            "rewrite" if has_traffic_warning else "keep",
            "high" if has_traffic_warning else "medium",
            (
                "当前优化目标偏点击，如果业务目标是购买或线索，建议改成转化事件。"
                if has_traffic_warning
                else "当前优化目标暂时保留，先看创意和落地页调整后的数据。"
            ),
            suggested_value="PURCHASE / LEAD / OFFSITE_CONVERSIONS"
            if has_traffic_warning
            else optimization_goal,
        ),
        _optimization_field(
            "bid_strategy",
            "出价策略",
            _first_text(metrics.get("bid_strategy"), adset.get("bid_strategy")),
            "keep",
            "medium",
            "出价策略先保持，避免和创意/落地页改动同时变化导致无法判断原因。",
        ),
        _optimization_field(
            "countries",
            "投放国家",
            countries,
            "keep" if countries else "missing",
            "medium",
            "国家缺失会影响外部创建广告；已有国家建议先保持。",
            suggested_value=countries or "需要运营选择",
            missing=not _has_value(countries),
        ),
        _optimization_field(
            "age_range",
            "年龄范围",
            f"{age_min or '-'}-{age_max or '-'}",
            "watch" if has_frequency_fatigue else "keep",
            "medium",
            (
                "频次偏高时可以观察是否需要扩年龄或拆人群。"
                if has_frequency_fatigue
                else "缺少分年龄效果数据，暂不建议直接改年龄范围。"
            ),
            suggested_value=f"{age_min or 18}-{age_max or 65}",
        ),
        _optimization_field(
            "pixel_id",
            "Pixel ID",
            _first_value(adset.get("pixel_id"), adset.get("pixelId")),
            "keep"
            if _has_value(_first_value(adset.get("pixel_id"), adset.get("pixelId")))
            else "missing",
            "high" if has_traffic_warning else "medium",
            "转化优化需要 Pixel 或事件回传；缺失时需要外部系统补传或运营选择。",
            suggested_value=_first_value(adset.get("pixel_id"), adset.get("pixelId"))
            or "需要运营选择",
            missing=not _has_value(_first_value(adset.get("pixel_id"), adset.get("pixelId"))),
        ),
        _optimization_field(
            "optimization_event",
            "优化事件",
            optimization_event,
            "missing" if has_traffic_warning and not optimization_event else "keep",
            "high" if has_traffic_warning else "medium",
            (
                "建议回传 purchase、add_to_cart 或 lead 后再做转化优化。"
                if has_traffic_warning
                else "已有优化事件可以保留，后续用转化质量验证。"
            ),
            suggested_value=optimization_event or "PURCHASE",
            missing=not bool(optimization_event),
        ),
    ]


def _creative_optimization_fields(
    creative: dict[str, Any],
    request_payload: dict[str, Any],
    creative_type: str,
    is_image: bool,
    is_video: bool,
    has_low_ctr: bool,
    has_post_click_drop: bool,
    has_insufficient_data: bool,
    creative_direction: str | None,
) -> list[dict[str, Any]]:
    message = _first_text(
        creative.get("message"),
        creative.get("primary_text"),
        creative.get("body"),
        creative.get("text"),
    )
    headline = _first_text(
        creative.get("headline"),
        creative.get("title"),
        creative.get("ads_name"),
        creative.get("ad_title"),
    )
    description = _first_text(creative.get("description"), creative.get("caption"))
    landing_url = _first_text(
        creative.get("link"), creative.get("landing_url"), request_payload.get("landing_url")
    )
    button_type = _first_text(
        creative.get("btn_type"), creative.get("button_type"), creative.get("call_to_action")
    )
    image_url = _first_text(
        creative.get("image_url"),
        creative.get("imageUrl"),
        creative.get("asset_image_url"),
        creative.get("assetImageUrl"),
        creative.get("thumbnail_url"),
        creative.get("thumbnailUrl"),
        creative.get("cover_url"),
        creative.get("coverUrl"),
        creative.get("preview_url"),
        creative.get("previewUrl"),
    )
    video_url = _first_text(
        creative.get("video_url"),
        creative.get("videoUrl"),
        creative.get("asset_video_url"),
        creative.get("assetVideoUrl"),
        creative.get("source_video_url"),
    )
    should_regenerate_creative = has_low_ctr and not has_insufficient_data
    should_check_landing = has_post_click_drop and landing_url
    keep_creative_reason = (
        "点击率不低，问题更可能在点击后的落地页承接，先不要全量否定素材方向。"
        if has_post_click_drop and not has_low_ctr
        else "样本不足时先观察，不建议马上重做素材。"
        if has_insufficient_data
        else "当前没有触发必须重做素材的规则，建议作为对照版本保留。"
    )
    image_generation_prompt = (
        "生成一张 Facebook 信息流图片广告，主体清楚、高对比度，"
        "突出产品结果或痛点。"
    )
    video_generation_prompt = (
        "生成一个 Facebook 短视频脚本，前三秒给出强钩子，"
        "突出产品结果和行动理由。"
    )
    text_action = "regenerate" if should_regenerate_creative or not message else "keep"
    headline_action = "regenerate" if should_regenerate_creative or not headline else "keep"
    material_action = "regenerate" if should_regenerate_creative else "keep"

    fields = [
        _optimization_field(
            "name",
            "创意名称",
            _first_value(creative.get("name"), creative.get("creative_name")),
            "keep",
            "low",
            "名称只用于管理识别，可以保留或在生成新草稿时自动随机。",
        ),
        _optimization_field(
            "creative_type",
            "创意类型",
            creative_type or _first_text(creative.get("asset_type")),
            "keep",
            "medium",
            "建议先保持同一创意类型做对照，避免同时改变太多变量。",
            suggested_value=creative_type or "图片",
        ),
        _optimization_field(
            "primary_text",
            "广告正文",
            message,
            text_action,
            "high" if text_action == "regenerate" else "medium",
            (
                "正文缺失或点击率偏低，建议重新生成更直接的利益点和痛点表达。"
                if text_action == "regenerate"
                else keep_creative_reason
            ),
            suggested_direction=creative_direction
            or "突出核心收益、使用场景和行动理由，前三秒/首屏就让用户知道为什么要点。",
            generation_prompt="重写 Facebook 广告正文，突出核心收益、痛点和行动理由，语气直接。"
            if text_action == "regenerate"
            else None,
            can_apply_to_generation=text_action == "regenerate",
            missing=not bool(message),
        ),
        _optimization_field(
            "headline",
            "广告标题",
            headline,
            headline_action,
            "high" if headline_action == "regenerate" else "medium",
            (
                "标题缺失或点击吸引力不足，建议重新生成更强的结果感或挑战感标题。"
                if headline_action == "regenerate"
                else keep_creative_reason
            ),
            suggested_direction="突出结果、挑战、痛点或明确利益点，避免只描述产品本身。",
            generation_prompt="生成 5 个 Facebook 广告标题，突出结果感、挑战感或明确利益点。"
            if headline_action == "regenerate"
            else None,
            can_apply_to_generation=headline_action == "regenerate",
            missing=not bool(headline),
        ),
        _optimization_field(
            "description",
            "广告描述",
            description,
            "rewrite" if should_regenerate_creative or not description else "keep",
            "medium",
            (
                "描述缺失或需要配合新标题补充卖点，建议重写为一句承接说明。"
                if should_regenerate_creative or not description
                else "描述可以保留，先把主要变量集中在标题、正文或素材上。"
            ),
            suggested_direction="补充标题下方的核心价值，不重复标题。",
            can_apply_to_generation=should_regenerate_creative or not bool(description),
            missing=not bool(description),
        ),
        _optimization_field(
            "landing_page_url",
            "落地页链接",
            landing_url,
            "check" if should_check_landing else "missing" if not landing_url else "keep",
            "high" if should_check_landing or not landing_url else "medium",
            (
                "点击不错但落地页到达率低，优先检查加载速度、跳转链路和像素事件。"
                if should_check_landing
                else "缺少落地页链接时无法判断点击后的承接，需要外部系统补传。"
                if not landing_url
                else "落地页链接已收到，当前不建议直接更换链接。"
            ),
            suggested_value=landing_url or "需要运营填写",
            missing=not bool(landing_url),
        ),
        _optimization_field(
            "button_type",
            "按钮类型",
            button_type,
            "keep" if button_type else "missing",
            "low",
            "按钮类型不是本次优先变量；缺失时按外部系统默认或运营选择。",
            suggested_value=button_type or "了解更多",
            missing=not bool(button_type),
        ),
    ]

    if is_image:
        fields.append(
            _optimization_field(
                "image_material",
                "图片素材",
                image_url or _first_value(creative.get("asset_name"), creative.get("asset_id")),
                "missing" if not image_url else material_action,
                "high" if should_regenerate_creative or not image_url else "medium",
                (
                    "缺少 image_url 时，AI 不能真正看见图片，需要外部系统补传或运营选择素材。"
                    if not image_url
                    else "点击率偏低，建议重新生成更强首屏、产品露出和结果对比的图片。"
                    if material_action == "regenerate"
                    else keep_creative_reason
                ),
                suggested_direction=creative_direction
                or "高对比度首图，主体清楚，突出产品结果或前后对比。",
                generation_prompt=image_generation_prompt
                if material_action == "regenerate"
                else None,
                can_apply_to_generation=material_action == "regenerate",
                missing=not bool(image_url),
            )
        )
    if is_video:
        fields.append(
            _optimization_field(
                "video_material",
                "视频素材",
                video_url
                or _first_value(creative.get("asset_name"), creative.get("asset_video_id")),
                "missing" if not video_url else material_action,
                "high" if should_regenerate_creative or not video_url else "medium",
                (
                    "缺少 video_url 或关键帧时，AI 不能判断具体画面和秒点。"
                    if not video_url
                    else "点击或视频留存偏低时，优先重做前三秒钩子和首屏信息。"
                    if material_action == "regenerate"
                    else keep_creative_reason
                ),
                suggested_direction=creative_direction
                or "前三秒直接展示结果、冲突或产品使用场景。",
                generation_prompt=video_generation_prompt
                if material_action == "regenerate"
                else None,
                can_apply_to_generation=material_action == "regenerate",
                missing=not bool(video_url),
            )
        )
    return fields


def _optimization_field(
    field: str,
    label: str,
    current_value: Any,
    action: str,
    priority: str,
    reason: str,
    suggested_value: Any | None = None,
    suggested_direction: str | None = None,
    generation_prompt: str | None = None,
    source: str = "ai",
    can_apply_to_generation: bool = False,
    missing: bool = False,
) -> dict[str, Any]:
    return {
        "field": field,
        "label": label,
        "current_value": current_value if _has_value(current_value) else None,
        "action": action,
        "priority": priority,
        "suggested_value": suggested_value,
        "suggested_direction": suggested_direction,
        "generation_prompt": generation_prompt,
        "reason": reason,
        "source": source,
        "can_apply_to_generation": can_apply_to_generation,
        "missing": missing,
    }


def _performance_signal_codes(
    metrics: dict[str, Any],
    campaign: dict[str, Any],
    adset: dict[str, Any],
) -> set[str]:
    impressions = _as_float(metrics.get("impressions")) or 0
    spend = _as_float(metrics.get("spend")) or 0
    ctr = _as_float(metrics.get("ctr"))
    link_clicks = _as_float(metrics.get("link_clicks")) or 0
    landing_page_rate = _as_float(metrics.get("landing_page_view_rate"))
    frequency = _as_float(metrics.get("frequency"))
    objective = (
        _text(metrics.get("campaign_objective")) or _text(campaign.get("objective")) or ""
    ).upper()
    optimization_goal = (
        _text(metrics.get("adset_optimization_goal")) or _text(adset.get("optimization_goal")) or ""
    ).upper()
    signals: set[str] = set()

    if impressions < LOW_SAMPLE_IMPRESSIONS or spend <= 0:
        signals.add("insufficient_data")
    if impressions >= LOW_SAMPLE_IMPRESSIONS and ctr is not None and ctr < LOW_CTR_PERCENT:
        signals.add("low_ctr")
    if (
        impressions >= LOW_SAMPLE_IMPRESSIONS
        and ctr is not None
        and ctr >= HIGH_CTR_PERCENT
        and landing_page_rate is not None
        and landing_page_rate < LOW_LANDING_VIEW_RATE
        and link_clicks >= ENOUGH_CLICKS_FOR_LANDING_RATE
    ):
        signals.add("post_click_drop")
    if frequency is not None and frequency > HIGH_FREQUENCY:
        signals.add("frequency_fatigue")
    if objective == "OUTCOME_TRAFFIC" or optimization_goal == "LINK_CLICKS":
        signals.add("traffic_optimization_warning")
    return signals


def _optimization_operator_summary(
    analysis_result: dict[str, Any],
    problem_codes: set[str],
    metrics: dict[str, Any],
) -> str:
    ai_analysis = (
        analysis_result.get("ai_analysis")
        if isinstance(analysis_result.get("ai_analysis"), dict)
        else {}
    )
    ai_summary = _trim_text(ai_analysis.get("summary"), 180) if ai_analysis else None
    if ai_summary:
        return ai_summary
    if "insufficient_data" in problem_codes:
        return "当前样本量不足，先检查投放是否正常跑量，不建议马上重做标题、文案或素材。"
    if "post_click_drop" in problem_codes and "low_ctr" not in problem_codes:
        return "广告能吸引点击，但落地页承接明显掉队，优先检查页面速度、跳转链路和像素事件。"
    if "low_ctr" in problem_codes:
        return "曝光后点击不足，优先重写标题、正文，并重新生成更强首屏的图片或视频开头。"
    if "traffic_optimization_warning" in problem_codes:
        return "当前更偏流量或点击优化，如果目标是购买/线索，需要补转化事件并调整优化目标。"
    ctr = _display_percent(metrics.get("ctr"))
    return f"当前没有必须立刻大改的信号，保留主设置，继续用小预算测试新创意方向。CTR={ctr}。"


def _optimization_creative_direction(ai_analysis: dict[str, Any]) -> str | None:
    if not ai_analysis:
        return None
    parts: list[str] = []
    for key in ("creative_feedback", "recommended_actions"):
        values = ai_analysis.get(key) if isinstance(ai_analysis.get(key), list) else []
        parts.extend(str(value) for value in values[:2] if value)
    visual = (
        ai_analysis.get("visual_analysis")
        if isinstance(ai_analysis.get("visual_analysis"), dict)
        else {}
    )
    visual_recommendations = (
        visual.get("recommendations") if isinstance(visual.get("recommendations"), list) else []
    )
    parts.extend(str(value) for value in visual_recommendations[:2] if value)
    if not parts:
        return None
    return "；".join(parts)[:500]


def _optimization_module_labels(
    fields: list[dict[str, Any]],
    actions: set[str],
) -> list[str]:
    return _unique(
        [
            str(field.get("label"))
            for field in fields
            if field.get("action") in actions and field.get("label")
        ]
    )


def _adset_optimization_event(adset: dict[str, Any], metrics: dict[str, Any]) -> str | None:
    promoted_object = (
        adset.get("promoted_object") if isinstance(adset.get("promoted_object"), dict) else {}
    )
    conversion_actions = (
        metrics.get("conversion_actions")
        if isinstance(metrics.get("conversion_actions"), dict)
        else {}
    )
    conversion_event = next(
        (str(key) for key, value in conversion_actions.items() if _as_float(value)),
        None,
    )
    return _first_text(
        adset.get("custom_event_type"),
        adset.get("optimization_event"),
        adset.get("event_name"),
        promoted_object.get("custom_event_type"),
        promoted_object.get("pixel_event_name"),
        conversion_event,
    )


def _first_value(*values: Any) -> Any:
    for value in values:
        if _has_value(value):
            return value
    return None


def _optimization_work_order_from_result(
    metrics: dict[str, Any],
    campaign: dict[str, Any],
    adset: dict[str, Any],
    creative: dict[str, Any],
    request_payload: dict[str, Any],
    analysis_result: dict[str, Any],
) -> dict[str, Any]:
    llm_work_order = _llm_optimization_work_order(analysis_result.get("ai_analysis"))
    if llm_work_order:
        return llm_work_order
    return _build_optimization_work_order(
        metrics=metrics,
        campaign=campaign,
        adset=adset,
        creative=creative,
        request_payload=request_payload,
        analysis_result=analysis_result,
    )


def _llm_optimization_work_order(ai_analysis: Any) -> dict[str, Any] | None:
    if not isinstance(ai_analysis, dict):
        return None
    raw_work_order = ai_analysis.get("optimization_work_order")
    if not isinstance(raw_work_order, dict):
        return None
    try:
        work_order = AdPerformanceOptimizationWorkOrder.model_validate(raw_work_order).model_dump(
            mode="json"
        )
    except ValidationError:
        return None
    if not (
        work_order.get("operator_summary")
        or work_order.get("campaign")
        or work_order.get("adset")
        or work_order.get("creative")
    ):
        return None
    return work_order


async def _append_llm_analysis(
    analysis_result: dict[str, Any],
    metrics: dict[str, Any],
    campaign: dict[str, Any],
    adset: dict[str, Any],
    creative: dict[str, Any],
    insight: dict[str, Any],
    request_payload: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    result = {
        **analysis_result,
        "analysis_mode": "llm_only",
        "rule_summary": None,
        "llm_error": None,
    }
    settings = get_settings()
    try:
        provider = get_llm_provider(settings)
        ai_data = await asyncio.wait_for(
            provider.analyze_ad_performance(
                _llm_analysis_context(
                    metrics=metrics,
                    campaign=campaign,
                    adset=adset,
                    creative=creative,
                    insight=insight,
                    analysis_context=result,
                    request_payload=request_payload,
                )
            ),
            timeout=settings.ad_performance_llm_timeout_seconds,
        )
        ai_analysis = AdPerformanceAIAnalysis.model_validate(ai_data).model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001 - provider failures should not block ingestion.
        error = _trim_text(str(exc), 500) or exc.__class__.__name__
        logger.warning("%s: %s", LLM_ERROR_PREFIX, error, exc_info=True)
        result["analysis_mode"] = "llm_failed"
        result["summary"] = "大模型分析暂时失败，请检查模型配置或稍后重试。"
        result["ai_analysis"] = None
        result["llm_error"] = error
        return result, f"{LLM_ERROR_PREFIX}: {error}"

    result["analysis_mode"] = "llm_only"
    result["ai_analysis"] = ai_analysis
    result["summary"] = ai_analysis.get("summary") or result["summary"]
    return result, None


def _analysis_result_for_llm_retry(analysis_result: dict[str, Any]) -> dict[str, Any]:
    result = {
        **analysis_result,
        "analysis_mode": "llm_only",
        "rule_summary": None,
        "problems": [],
        "recommendations": [],
        "next_checks": [],
        "ai_analysis": None,
        "llm_error": None,
    }
    result["summary"] = "等待大模型重新生成投放分析。"
    return result


def _analysis_result_with_llm_error(
    analysis_result: dict[str, Any],
    error: str,
) -> dict[str, Any]:
    if analysis_result.get("ai_analysis"):
        return {**analysis_result, "llm_error": error}
    return {
        **analysis_result,
        "analysis_mode": "llm_failed",
        "summary": "大模型分析暂时失败，请检查模型配置或稍后重试。",
        "rule_summary": None,
        "problems": [],
        "recommendations": [],
        "next_checks": [],
        "ai_analysis": None,
        "llm_error": error,
    }


def _llm_analysis_context(
    metrics: dict[str, Any],
    campaign: dict[str, Any],
    adset: dict[str, Any],
    creative: dict[str, Any],
    insight: dict[str, Any],
    analysis_context: dict[str, Any],
    request_payload: dict[str, Any],
) -> dict[str, Any]:
    metadata = (
        request_payload.get("metadata_json")
        if isinstance(request_payload.get("metadata_json"), dict)
        else {}
    )
    return {
        "schema_version": "ad_performance_llm_context_v1",
        "instructions": [
            "先独立分析该广告，不要只和同组广告对比。",
            "样本量不足时，只给排查方向，不要武断判断标题、文案、图片或视频差。",
            "建议必须能落到可执行操作，例如换钩子、换人群、查落地页、改优化目标。",
        ],
        "campaign": _compact_record_for_llm(
            campaign,
            (
                "id",
                "fb_id",
                "name",
                "status",
                "objective",
                "daily_budget",
                "created_at",
            ),
        ),
        "adset": _compact_record_for_llm(
            adset,
            (
                "id",
                "fb_id",
                "name",
                "status",
                "daily_budget",
                "billing_event",
                "optimization_goal",
                "bid_strategy",
                "pixel_id",
                "custom_event_type",
                "countries",
                "age_min",
                "age_max",
                "start_type",
                "start_time",
            ),
        ),
        "creative": _compact_record_for_llm(
            creative,
            (
                "id",
                "fb_id",
                "facebook_ad_id",
                "name",
                "ad_status",
                "message",
                "headline",
                "primary_text",
                "description",
                "btn_type",
                "link",
                "asset_id",
                "asset_name",
                "asset_type",
                "creative_type",
                "image_url",
                "imageUrl",
                "asset_image_url",
                "assetImageUrl",
                "thumbnail_url",
                "thumbnailUrl",
                "cover_url",
                "coverUrl",
                "preview_url",
                "previewUrl",
                "video_url",
                "videoUrl",
                "asset_video_url",
                "assetVideoUrl",
                "source_video_url",
                "video_keyframes",
                "videoKeyframes",
                "keyframes",
                "frames",
                "asset_image_hash",
                "asset_video_id",
            ),
        ),
        "insight": _compact_record_for_llm(
            insight,
            (
                "campaign_id",
                "campaign_name",
                "adset_id",
                "adset_name",
                "ad_id",
                "ad_name",
                "spend",
                "impressions",
                "reach",
                "frequency",
                "clicks",
                "inline_link_clicks",
                "ctr",
                "inline_link_click_ctr",
                "cpc",
                "cpm",
                "actions",
                "cost_per_action_type",
                "video_play_actions",
                "video_p25_watched_actions",
                "video_p50_watched_actions",
                "video_p75_watched_actions",
                "video_p95_watched_actions",
                "video_p100_watched_actions",
                "date_start",
                "date_stop",
            ),
        ),
        "metrics": metrics,
        "data_completeness": analysis_context.get("data_completeness") or {},
        "previous_analysis": {
            "summary": analysis_context.get("summary"),
            "ai_analysis": analysis_context.get("ai_analysis"),
        },
        "siblings": metrics.get("sibling_metrics") or [],
        "metadata": _compact_value_for_llm(metadata, max_text=1200),
    }


def _compact_record_for_llm(record: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in keys:
        if key not in record:
            continue
        value = record.get(key)
        if value in (None, "", []):
            continue
        compact[key] = _compact_value_for_llm(value)
    return compact


def _compact_value_for_llm(value: Any, max_text: int = 1600) -> Any:
    if isinstance(value, str):
        return value[:max_text]
    if isinstance(value, list):
        return [_compact_value_for_llm(item, max_text=max_text) for item in value[:30]]
    if isinstance(value, dict):
        return {
            str(key)[:80]: _compact_value_for_llm(item, max_text=max_text)
            for key, item in list(value.items())[:60]
        }
    return value


def _append_video_analysis(
    metrics: dict[str, Any],
    problems: list[AdPerformanceProblem],
    recommendations: list[AdPerformanceRecommendation],
    next_checks: list[str],
) -> None:
    video_play = _as_float(metrics.get("video_play")) or 0
    p25_rate = _as_float(metrics.get("video_p25_rate"))
    p50_rate = _as_float(metrics.get("video_p50_rate"))
    if video_play < 20:
        return

    if p25_rate is not None and p25_rate < 0.5:
        problems.append(
            _problem(
                "weak_video_opening",
                "warning",
                "video",
                "视频前段留存偏弱",
                [
                    f"video_play={_display_number(video_play)}",
                    f"video_p25_rate={_display_ratio(p25_rate)}",
                ],
                "较多人播放后没有看完前 25%，通常说明前三秒不够直接，或开头没有展示核心冲突/结果。",
            )
        )
        recommendations.append(
            _recommendation(
                "rewrite_first_three_seconds",
                "high",
                "video",
                "重做视频前三秒",
                "开头直接展示结果、冲突或最强画面，减少铺垫；首屏字幕要短，并和画面动作同步。",
            )
        )
        next_checks.append("视频前三秒是否出现核心卖点或关键玩法")

    if p50_rate is not None and p50_rate < 0.25:
        problems.append(
            _problem(
                "weak_video_retention",
                "warning",
                "video",
                "视频中段留存偏弱",
                [f"video_p50_rate={_display_ratio(p50_rate)}"],
                "用户愿意开始看，但中段流失较多，可能是节奏慢、信息重复，或卖点没有递进。",
            )
        )
        recommendations.append(
            _recommendation(
                "tighten_video_pacing",
                "medium",
                "video",
                "压缩中段节奏并增加变化",
                "把重复镜头删掉，每 2-3 秒给一次画面变化或新的利益点，结尾保留明确 CTA。",
            )
        )


def _append_objective_analysis(
    metrics: dict[str, Any],
    campaign: dict[str, Any],
    adset: dict[str, Any],
    problems: list[AdPerformanceProblem],
    recommendations: list[AdPerformanceRecommendation],
    next_checks: list[str],
) -> None:
    objective = (
        _text(metrics.get("campaign_objective")) or _text(campaign.get("objective")) or ""
    ).upper()
    optimization_goal = (
        _text(metrics.get("adset_optimization_goal")) or _text(adset.get("optimization_goal")) or ""
    ).upper()
    conversion_actions = (
        metrics.get("conversion_actions")
        if isinstance(metrics.get("conversion_actions"), dict)
        else {}
    )
    has_conversion = any(_as_float(value) for value in conversion_actions.values())

    if objective == "OUTCOME_TRAFFIC" or optimization_goal == "LINK_CLICKS":
        problems.append(
            _problem(
                "traffic_optimization_warning",
                "warning",
                "objective",
                "当前更偏向流量/点击优化",
                [f"objective={objective or '-'}", f"optimization_goal={optimization_goal or '-'}"],
                "如果这条广告最终目标是购物、加购、注册或付费，使用流量/点击优化可能会带来很多点击，但不一定带来高质量转化。",
            )
        )
        recommendations.append(
            _recommendation(
                "align_objective_with_business_goal",
                "medium",
                "objective",
                "让广告目标和最终业务目标一致",
                (
                    "如果要看销售或注册效果，建议让外部系统回传 purchase、"
                    "add_to_cart、lead 等转化数据，并优先测试转化优化。"
                ),
            )
        )
        if not has_conversion:
            next_checks.append("是否能回传 purchase/add_to_cart/lead 等转化数据")


def _append_sibling_comparison(
    metrics: dict[str, Any],
    problems: list[AdPerformanceProblem],
    recommendations: list[AdPerformanceRecommendation],
) -> None:
    siblings = metrics.get("sibling_metrics")
    if not isinstance(siblings, list) or not siblings:
        return
    sibling_ctrs = [
        _as_float(item.get("ctr"))
        for item in siblings
        if isinstance(item, dict)
        and (_as_float(item.get("impressions")) or 0) >= LOW_SAMPLE_IMPRESSIONS
    ]
    sibling_ctrs = [value for value in sibling_ctrs if value is not None]
    current_ctr = _as_float(metrics.get("ctr"))
    if not sibling_ctrs or current_ctr is None:
        return
    baseline = median(sibling_ctrs)
    if baseline > 0 and current_ctr < baseline * 0.7:
        problems.append(
            _problem(
                "underperforms_siblings",
                "warning",
                "comparison",
                "同组对比下点击率落后",
                [
                    f"current_ctr={_display_percent(current_ctr)}",
                    f"sibling_median_ctr={_display_percent(baseline)}",
                ],
                "同一广告组下，受众和预算环境接近，如果本素材明显低于同组中位数，更可能是素材表达本身不够强。",
            )
        )
        recommendations.append(
            _recommendation(
                "borrow_winning_sibling_pattern",
                "medium",
                "creative",
                "参考同组表现更好的素材方向重做",
                "保留同组优胜素材的首屏结构、利益点表达或画面节奏，再替换成新的标题和视觉版本测试。",
            )
        )


def _summary(
    metrics: dict[str, Any],
    problems: list[AdPerformanceProblem],
    confidence: str,
    request_payload: dict[str, Any],
) -> str:
    creative_name = _first_text(
        _record_from_payload(request_payload, "creative", ("ad",)).get("name"),
        _insight_record(_record_from_payload(request_payload, "insight", ("insights",))).get(
            "ad_name"
        ),
        "该广告",
    )
    lead = problems[0]
    return (
        f"{creative_name} 的分析置信度为 {confidence}。主要判断：{lead.title}。"
        f"当前曝光 {_display_number(metrics.get('impressions'))}，"
        f"CTR {_display_percent(metrics.get('ctr'))}，"
        f"落地页到达率 {_display_ratio(metrics.get('landing_page_view_rate'))}。"
    )


def _record_from_payload(
    payload: dict[str, Any],
    key: str,
    aliases: tuple[str, ...] = (),
) -> dict[str, Any]:
    for name in (key, *aliases):
        value = payload.get(name)
        if isinstance(value, dict):
            return value
    return payload


def _insight_record(value: dict[str, Any]) -> dict[str, Any]:
    data = value.get("data") if isinstance(value, dict) else None
    if isinstance(data, dict):
        nested = data.get("data")
        if isinstance(nested, list):
            return _first_record(nested) or value
        return data
    if isinstance(data, list):
        return _first_record(data) or value
    return value if isinstance(value, dict) else {}


def _first_record(value: list[Any]) -> dict[str, Any] | None:
    for item in value:
        if isinstance(item, dict):
            return item
    return None


def _sibling_metrics(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    metrics: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        creative = item.get("creative") if isinstance(item.get("creative"), dict) else item
        insight = _insight_record(
            item.get("insight") if isinstance(item.get("insight"), dict) else item
        )
        impressions = _number(insight.get("impressions"))
        clicks = _number(insight.get("clicks"))
        inline_link_clicks = _number(insight.get("inline_link_clicks"))
        link_clicks = _action_value(insight, "link_click") or inline_link_clicks or clicks
        landing_page_views = _action_value(insight, "landing_page_view") or 0
        ctr = _number(insight.get("ctr"))
        if ctr is None and impressions and clicks is not None:
            ctr = _safe_rate(clicks, impressions) * 100
        metrics.append(
            {
                "creative_name": _first_text(creative.get("name"), insight.get("ad_name")),
                "creative_type": _first_text(
                    creative.get("creative_type"), creative.get("asset_type")
                ),
                "impressions": _round(impressions),
                "spend": _round(_number(insight.get("spend"))),
                "ctr": _round(ctr),
                "link_clicks": _round(link_clicks),
                "landing_page_view_rate": _round(_safe_rate(landing_page_views, link_clicks)),
            }
        )
    return metrics


def _action_value(insight: dict[str, Any], action_type: str) -> float | None:
    actions = insight.get("actions")
    if not isinstance(actions, list):
        return None
    total = 0.0
    found = False
    for action in actions:
        if not isinstance(action, dict) or action.get("action_type") != action_type:
            continue
        value = _number(action.get("value"))
        if value is None:
            continue
        total += value
        found = True
    return total if found else None


def _action_array_value(insight: dict[str, Any], key: str) -> float | None:
    actions = insight.get(key)
    if not isinstance(actions, list):
        return None
    total = 0.0
    found = False
    for action in actions:
        if not isinstance(action, dict):
            continue
        value = _number(action.get("value"))
        if value is None:
            continue
        total += value
        found = True
    return total if found else None


def _conversion_actions(insight: dict[str, Any]) -> dict[str, float]:
    conversion_types = {
        "purchase",
        "offsite_conversion.fb_pixel_purchase",
        "add_to_cart",
        "offsite_conversion.fb_pixel_add_to_cart",
        "lead",
        "offsite_conversion.fb_pixel_lead",
        "complete_registration",
    }
    result: dict[str, float] = {}
    actions = insight.get("actions")
    if not isinstance(actions, list):
        return result
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_type = _text(action.get("action_type"))
        if not action_type or action_type not in conversion_types:
            continue
        result[action_type] = (result.get(action_type) or 0) + (_number(action.get("value")) or 0)
    return result


def _problem(
    code: str,
    severity: str,
    area: str,
    title: str,
    evidence: list[str],
    diagnosis: str,
) -> AdPerformanceProblem:
    return AdPerformanceProblem(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        area=area,
        title=title,
        evidence=evidence,
        diagnosis=diagnosis,
    )


def _recommendation(
    code: str,
    priority: str,
    area: str,
    action: str,
    detail: str,
) -> AdPerformanceRecommendation:
    return AdPerformanceRecommendation(
        code=code,
        priority=priority,  # type: ignore[arg-type]
        area=area,
        action=action,
        detail=detail,
    )


def _raise_confidence(current: str) -> str:
    if current == "low":
        return "medium"
    return "high"


def _has_any_field(*records: dict[str, Any], keys: tuple[str, ...]) -> bool:
    for record in records:
        for key in keys:
            if _has_value(record.get(key)):
                return True
    return False


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    return _number(value)


def _safe_rate(numerator: Any, denominator: Any) -> float | None:
    top = _number(numerator)
    bottom = _number(denominator)
    if top is None or bottom is None or bottom <= 0:
        return None
    return top / bottom


def _round(value: Any, digits: int = 8) -> float | None:
    number = _number(value)
    if number is None:
        return None
    return round(number, digits)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_text(*values: Any) -> str | None:
    for value in values:
        text = _text(value)
        if text:
            return text
    return None


def _trim_text(value: Any, length: int) -> str | None:
    text = _text(value)
    if not text:
        return None
    return text[:length]


def _display_number(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "-"
    if number == int(number):
        return str(int(number))
    return f"{number:.4f}".rstrip("0").rstrip(".")


def _display_percent(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "-"
    return f"{number:.2f}%"


def _display_ratio(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "-"
    return f"{number * 100:.1f}%"


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
