from __future__ import annotations

from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.ad_performance_analysis import AdPerformanceAnalysis
from backend.app.schemas.ad_performance import (
    AdPerformanceAnalysisCreate,
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
        analysis_result = _analyze(metrics, campaign, adset, creative, request_payload)

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
        "problems": [problem.model_dump(mode="json") for problem in problems],
        "recommendations": [
            recommendation.model_dump(mode="json") for recommendation in recommendations
        ],
        "next_checks": _unique(next_checks),
    }


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
