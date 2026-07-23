# 公开广告研究接口（V1）

## 用途、采集器与边界

外部投放系统提交国家、广告类别和可选关键词，AI 系统异步检索 **Meta Ad Library 中公开可见** 的广告资料。新任务在 `completed` 时会返回**严格等于**请求 `target_count` 的、已完成技术检查和视觉评分的短视频广告；若无法取得足量候选，则以 `failed` 结束且不返回部分结果。

首期采集器固定使用 [`athm793/meta-ads-scraper`](https://github.com/athm793/meta-ads-scraper)，由内部 `meta_ads_collector` Bridge 调用。外部系统不直接访问采集器，也不需要维护关键词词包、轮次预算或视觉排序阈值。

本接口只研究公开广告资料，并明确不做以下事项：

- 不访问、不跟踪、不模拟广告落地页用户行为；不跟随落地页跳转，也不尝试识别、确认或复现 Cloaking。
- 不返回或推断真实花费、投放成本、CPC、CPA、ROAS、转化量或利润。
- 不将文案、标题、CTA、主页名称、落地页 URL、域名或广告主作为视觉类别的硬淘汰条件；视觉模型本身不会接收这些字段。
- 不保证素材实际业务合规性，也不承诺公开持续投放信号等同于真实业务效果。

### 技术硬条件

最终返回的每条广告均满足以下条件：

1. Meta 广告状态为 `ACTIVE`；
2. 公开可见投放时间 `active_days >= 1`；
3. 存在可访问且可下载的公开视频；
4. 最终确认的视频时长 `<= 30` 秒；
5. `ad_library_id` 已去重；
6. 至少有可分析的封面或关键帧：可用原始缩略图，或从下载视频生成封面和关键帧；
7. 视觉模型已返回通过服务端结构校验的评分。

时长确认依次使用 Collector 已报时长、远程 `ffprobe`（含重试）、下载后本地 `ffprobe`。视频下载、时长探测和抽帧均为并发处理；无法确认时长、下载失败或没有任何可分析视觉图时，候选会被技术淘汰。

- 初次导出 20%、50%、80% 三张关键帧；原封面不存在或不可解码时，以关键帧生成 `cover.jpg`。
- 首次视觉评分置信度低于 `0.60` 时，补充 35%、65% 两张关键帧后重评，最多使用 5 张帧。
- 补帧失败不会使已经技术合格的素材失效；但模型评分始终失败的候选不会计入可完成数量。

## 鉴权

所有接口沿用 AI 系统鉴权。调用方应通过部署方安全分发的凭据传递认证信息；示例仅使用占位符：

```http
Authorization: Bearer <AI_ADS_ACCESS_TOKEN>
Content-Type: application/json
```

基础地址由部署方提供。本文不包含任何真实 Token、密钥或服务器连接信息。

## 1. 创建研究任务

```http
POST /api/v1/integrations/ad-research/jobs
```

请求体（以下示例使用 `target_count=1`，使完成响应中的数组长度可完整展示；常规请求可使用 1–25）：

```json
{
  "external_user_id": "external-research-example-0001",
  "country": "IN",
  "category": "gambling",
  "keywords": ["rummy", "casino"],
  "target_count": 1
}
```

字段说明：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `external_user_id` | 是 | 本次**逻辑研究请求**的外部唯一 ID，并作为幂等键；不是长期用户账号 ID。长度 1–128。 |
| `country` | 是 | 国家代码，例如 `IN`；服务端统一为大写。 |
| `category` | 是 | 业务类别，例如 `ecommerce`、`game`、`gambling`、`weight_loss`。后台以此规划检索词和解释视觉评分方向，但不把广告文本作为类别硬淘汰规则。 |
| `keywords` | 否 | 业务补充词，最多 24 个。模型可结合每轮公开召回、查询级统计和高分视觉元素调整后续检索词。 |
| `target_count` | 否 | 目标返回数量，默认 `25`，范围 `1–25`。`completed` 时 `ads.length` 必须严格等于该值。 |

首次创建成功返回 `202 Accepted`：

```json
{
  "task_id": "adr_01...",
  "external_user_id": "external-research-example-0001",
  "status": "queued",
  "idempotent_replay": false,
  "poll_url": "/api/v1/integrations/ad-research/jobs/adr_01...",
  "poll_after_seconds": 3
}
```

### 幂等规则

服务端把 `country`（统一大写）、`category`、去重排序后的 `keywords` 和 `target_count` 规范化后计算请求指纹：

- 相同 `external_user_id` 且规范化请求内容相同：若既有任务未失败、未过期，返回同一个任务，响应为 `200 OK` 且 `idempotent_replay: true`。
- 相同 `external_user_id` 但规范化请求内容不同：返回 `409 Conflict`。
- 相同规范化请求此前为 `failed`：服务端会复用同一逻辑任务并重新入队；调用方可使用**同一个** `external_user_id` 重试。
- 已过期结果会释放该幂等键；之后可使用原 `external_user_id` 创建新的逻辑请求。

冲突响应：

```json
{
  "detail": {
    "code": "external_user_id_payload_conflict",
    "message": "..."
  }
}
```

## 2. 轮询任务结果

```http
GET /api/v1/integrations/ad-research/jobs/{task_id}
```

新任务只会处于 `queued`、`processing`、`completed` 或 `failed` 状态。仅在 `queued` 或 `processing` 时按响应中的 `poll_after_seconds` 轮询（建议至少间隔 3 秒）；不使用 callback，也不需要“确认已获取结果”的接口。

成功结果与系统保存的最终封面/关键帧保留 24 小时。保留期结束后，任务记录会标记为 `expired`，本 GET 返回 `410 Gone`，不返回已清理的结果或媒体。

处理中示例：

```json
{
  "task_id": "adr_01...",
  "external_user_id": "external-research-example-0001",
  "status": "processing",
  "stage": "model_visual_scoring",
  "round": 2,
  "progress": {
    "raw_collected": 100,
    "deduplicated": 92,
    "technical_qualified": 34,
    "model_scored": 18,
    "model_scoring_failed": 1,
    "model_relevant": 18,
    "selected_count": 1,
    "score_distribution": {"0_19": 0, "20_39": 1, "40_54": 4, "55_69": 8, "70_89": 5, "90_100": 0}
  },
  "poll_after_seconds": 3,
  "research_summary": {},
  "ads": null,
  "result_expires_at": null,
  "error": null
}
```

完成示例（对应上方 `target_count=1` 的请求；分数和内容仅为示意）：

```json
{
  "task_id": "adr_01...",
  "external_user_id": "external-research-example-0001",
  "status": "completed",
  "stage": "completed",
  "round": 6,
  "progress": {
    "raw_collected": 150,
    "deduplicated": 132,
    "technical_qualified": 42,
    "model_scored": 31,
    "model_scoring_failed": 1,
    "model_relevant": 31,
    "selected_count": 1
  },
  "poll_after_seconds": null,
  "research_summary": {
    "raw_collected": 150,
    "deduplicated": 132,
    "technical_qualified": 42,
    "model_scored": 31,
    "model_scoring_failed": 1,
    "model_relevant": 31,
    "selected_count": 1,
    "minimum_active_days": 1,
    "maximum_video_seconds": 30.0,
    "priority_counts": {
      "game_gambling": 12,
      "sports_betting": 6,
      "gambling_adjacent": 4,
      "unrelated": 9
    },
    "quality_summary": {
      "game_gambling_count": 1,
      "sports_betting_count": 0,
      "gambling_adjacent_count": 0,
      "fallback_count": 0,
      "fallback_used": false
    },
    "round_budget": {
      "standard_rounds": 4,
      "max_rounds": 6,
      "current_round": 6,
      "remaining_rounds": 0,
      "max_raw_candidates": 650,
      "remaining_raw_candidates": 500,
      "quality_supplement_mode": true
    },
    "technical_rejection_summary": {"duration_over_30": 42, "no_analyzable_visual": 3},
    "query_metrics": [
      {
        "query_id": "r1_q01",
        "query": "slot jackpot bonus",
        "intent": "game_gambling",
        "raw_collected": 50,
        "new_unique_count": 38,
        "duplicate_count": 12,
        "duration_le_30_count": 22,
        "technical_qualified": 17,
        "model_scored": 16,
        "game_gambling_count": 10,
        "sports_betting_count": 2,
        "gambling_adjacent_count": 3,
        "unrelated_count": 1,
        "score_above_55": 13,
        "average_visual_score": 63.4,
        "best_visual_score": 88.0
      }
    ]
  },
  "ads": [
    {
      "ad_library_id": "...",
      "first_source_query_id": "r1_q01",
      "source_query_ids": ["r1_q01", "r2_q03"],
      "advertiser_name": "...",
      "ad_snapshot_url": "https://www.facebook.com/ads/library/...",
      "text": "...",
      "headline": "...",
      "cta_text": "...",
      "video_url": "https://...",
      "thumbnail_url": "https://...",
      "duration_seconds": 18.4,
      "active_days": 12,
      "platforms": ["facebook", "instagram"],
      "final_score": 76.0,
      "visual_total": 76.0,
      "visual_priority": "game_gambling",
      "gameplay_gambling_points": 36.0,
      "multi_signal_style_points": 16.0,
      "betting_mechanism_points": 10.0,
      "gambling_visual_style_points": 7.0,
      "visual_clarity_points": 4.0,
      "media_quality_points": 3.0,
      "analysis_confidence": 0.87,
      "gambling_signals": ["slot reels", "gold coins", "bonus UI"],
      "game_visual_present": true,
      "visual_evidence": [{"frame_index": 1, "detail": "gold coin reward animation"}],
      "retrieval_hints": ["slot reels", "jackpot UI"],
      "uncertain": false,
      "is_fallback": false,
      "fallback_reason": null,
      "media": {
        "cover_url": "/storage/ad-research/adr_01.../.../cover.jpg",
        "cover_source": "generated_frame",
        "frame_urls": ["/storage/ad-research/adr_01.../.../frame_20.jpg"],
        "frame_count": 3,
        "duration_source": "downloaded_ffprobe",
        "duration_probe_attempts": 3
      }
    }
  ],
  "result_expires_at": "2026-07-24T08:00:00Z",
  "error": null
}
```

`cover_url` 和 `frame_urls` 是系统保存的最终媒体，可用于展示或人工复核，并与结果一同只保留 24 小时。原始 `thumbnail_url` 只是采集器提供的来源 URL，不等同于系统最终保存的封面。

### 关键词闭环、评分与排序

外部系统只传国家、类别、可选关键词和目标数量。后台每轮生成带 `query_id`、查询词、意图和预期视觉的结构化查询计划，并记录广告的 `first_source_query_id` 与全部 `source_query_ids`；同一 `ad_library_id` 即使被多个查询召回，也只进行一次媒体处理和一次最终视觉评分。

每个查询及每轮摘要包含召回量、去重/技术通过/模型评分数量、P1–P4 数量、`score_above_55`、平均视觉分和最佳视觉分等诊断。`score_above_55` 仅是查询质量统计，不是完成门槛，也不会决定任务是否 `completed`。

视觉模型配置为 `gpt-5.4-mini`，Responses 请求固定为：

```json
{"reasoning": {"effort": "none"}}
```

模型仅接收：类别、技术阶段确认的视频时长、帧数以及封面/关键帧图像。模型不会接收或使用文案、标题、CTA、广告主、主页、落地页 URL、来源关键词、`active_days` 或公开持续投放分数。

服务端校验六个视觉分项并计算总分：

| 分项 | 最高分 |
| --- | ---: |
| `gameplay_gambling_points` | 40 |
| `multi_signal_style_points` | 20 |
| `betting_mechanism_points` | 15 |
| `gambling_visual_style_points` | 10 |
| `visual_clarity_points` | 10 |
| `media_quality_points` | 5 |

```text
final_score = visual_total
```

`visual_priority` 从高到低为 P1 `game_gambling`、P2 `sports_betting`、P3 `gambling_adjacent`、P4 `unrelated`。稳定排序依次为：视觉优先级、`visual_total`、`analysis_confidence`、`active_days`、关键帧数量、`ad_library_id`。`active_days` 只在同优先级、同分附近稳定排序，不代表花费、CPA、ROAS 或转化，也不会给弱视觉相关素材加分。

### 质量补采与透明 P4 补位

后台采用 1–4 轮标准采集和 5–6 轮定向质量补采，最多 6 轮；整个任务最多处理 650 条原始候选，每个查询最多召回 50 条。第 5、6 轮根据前序查询级统计、缺失优先级和高分视觉信号补采，调用方无需提供内部关键词计划。

完成规则如下：

1. 若 P1 已达到 `target_count`，可立即以 `completed` 返回；
2. 在最终轮次或原始候选预算耗尽时，若已取得至少 `target_count` 条技术合格且模型评分成功的候选，按 P1 → P2 → P3 → P4 选取前 `target_count` 条并 `completed`；
3. 若总已评分技术合格候选仍少于 `target_count`，任务为 `failed`，`ads` 必须为 `null`，绝不返回部分结果；
4. P4 只在 P1–P3 数量不足、但总数量已足够时用于最终补位。每条 P4 结果明确标记 `is_fallback: true` 与 `fallback_reason: "insufficient_high_relevance_candidates"`，并在 `quality_summary` 中反映 `fallback_count` 和 `fallback_used`。

## 任务状态与错误

| HTTP / `status` | 含义 | 外部系统动作 |
| --- | --- | --- |
| `202` / `queued` | 已创建或失败重试后重新入队，等待 Worker。 | 按建议时间轮询。 |
| `processing` | 正在规划、采集、技术准备或视觉评分。 | 继续轮询。 |
| `completed` | 已取得严格等于 `target_count` 条技术合格且模型评分成功的结果。 | 读取 `ads`，停止轮询。 |
| `insufficient` | `legacy insufficient` 历史只读兼容状态，可能带旧 `ads`；新任务绝不会产生该状态。 | 仅按历史结果处理，不应作为新任务终态。 |
| `failed` | 达到轮次/原始候选预算仍无法得到严格等于 `target_count` 条已评分技术合格广告，或发生队列、采集器、媒体或未捕获系统异常；`ads` 为 `null`。 | 查看 `error.code` 后可用同一 `external_user_id` 重试，或联系系统维护方；停止轮询。 |
| `410 Gone` / `expired` | 成功结果已超过 24 小时；GET 返回 `410 Gone`，结果和系统保存的封面/关键帧均已清除。 | 使用新的请求重新创建任务；原 `external_user_id` 已可复用。 |
| `404 Not Found` | `task_id` 不存在。 | 检查请求 ID。 |

## 后台处理与固定资源配置

1. `gpt-5.4-mini` 结合国家、类别、外部补充词、受控查询级统计和高分可见视觉元素规划/调整检索词；不向调用方暴露模型 prompt 或原始响应。
2. 内部采集 Bridge 每个查询最多召回 50 条公开视频广告；服务端跨查询去重后并发进行媒体准备、时长确认、封面生成和关键帧导出。
3. `gpt-5.4-mini` 仅依据合规的视觉输入评分；低置信度候选会补帧并复评。模型调用使用 Redis 全局租约，避免并发超限。
4. 完成时只保留已选 Top N 的媒体目录；未选中候选、失败任务和 stale 任务的媒体会清理。

本版本仅说明、**不通过本接口变更**以下固定运行配置：

```env
AD_RESEARCH_WORKER_CONCURRENCY=2
AD_RESEARCH_MODEL_CONCURRENCY=6
AD_RESEARCH_MEDIA_CONCURRENCY=6
AD_RESEARCH_FRAME_CONCURRENCY=4
AD_RESEARCH_MODEL=gpt-5.4-mini
```

其中 Worker 并发固定为 2，模型全局并发固定为 6；媒体探测和抽帧继续由独立并发配置控制。调用方不应在请求体中传入这些配置，也不需要传入任何服务器、队列或模型连接信息。