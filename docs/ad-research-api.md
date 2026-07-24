# 公开广告研究接口（V1）

## 用途、采集器与边界

外部投放系统提交国家、广告类别及可选关键词，AI 系统异步研究 **Meta Ad Library 中公开可见** 的广告资料。首期采集器固定使用 [`athm793/meta-ads-scraper`](https://github.com/athm793/meta-ads-scraper)，由内部 `meta_ads_collector` Bridge 调用；外部系统不直接访问采集器。

任务完成（`completed`）时，接口返回**严格等于**请求 `target_count` 的唯一广告。若公开来源、技术合格候选或模型评分结果不足，任务以 `failed` 结束，`ads` 为 `null`，绝不返回部分结果。默认 `target_count=25`，范围为 1–25。

本接口研究公开广告素材，不做以下事项：

- 不访问、不跟踪、不模拟落地页用户行为；不跟随跳转，不尝试识别、确认或复现 Cloaking。
- 不返回或推断真实花费、投放成本、CPC、CPA、ROAS、转化量或利润；公开可见的投放时长仅是素材持续可见信号，不是成效证明。
- 不将广告正文、标题、CTA、主页、广告主、URL、域名作为视觉类别的硬淘汰条件；视觉评分模型不会接收这些字段。
- 不保证素材实际业务合规性，也不把公开持续投放信号解释为真实业务成效。

### 最终广告的技术硬条件

每条最终广告均满足：

1. Meta 广告状态为 `ACTIVE`；
2. 公开可见投放时间 `active_days >= 1`；
3. 有可访问、可下载的公开视频；
4. 最终确认的视频时长 `<= 30` 秒；
5. `ad_library_id` 全任务去重；
6. 有可分析封面或关键帧：优先使用可用原始缩略图，否则从下载视频生成封面与关键帧；
7. 视觉模型返回了可通过服务端结构校验的评分。

时长确认依次使用 Collector 已报时长、远程 `ffprobe`（含重试）、下载后本地 `ffprobe`。下载、时长探测、抽帧均为并发处理；下载失败、无法确认时长、超过 30 秒或没有任何可分析画面时，候选会被技术淘汰。

- 初次抽取 20%、50%、80% 三张关键帧；原封面不可用时，以关键帧生成 `cover.jpg`。
- 首次视觉评分置信度低于 `0.60` 时，补充 35%、65% 两张关键帧后复评，最多使用 5 张帧。
- 补帧失败不会推翻已通过技术条件的候选；但模型始终评分失败的候选不计入可完成数量。

## 鉴权

所有接口沿用 AI 系统鉴权。调用方通过部署方安全分发的凭据认证；以下仅使用占位符：

```http
Authorization: Bearer <AI_ADS_ACCESS_TOKEN>
Content-Type: application/json
```

基础地址由部署方提供。本文不包含真实 Token、密钥、服务器地址或连接信息。

## 1. 创建研究任务

```http
POST /api/v1/integrations/ad-research/jobs
```

```json
{
  "external_user_id": "external-research-example-0001",
  "country": "IN",
  "category": "gambling",
  "keywords": ["rummy", "casino"],
  "target_count": 25
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `external_user_id` | 是 | 本次**逻辑研究请求**的外部唯一 ID，同时作为幂等键；不是长期用户账号 ID。长度 1–128。 |
| `country` | 是 | 国家代码，例如 `IN`；服务端统一转为大写。 |
| `category` | 是 | 业务类别，例如 `ecommerce`、`game`、`gambling`、`weight_loss`。用于关键词规划和视觉评分语境，不会把广告文本作为类别硬淘汰条件。 |
| `keywords` | 否 | 业务补充词，最多 24 个、每个最多 160 字符。原始词是优先输入：系统会先保留/执行用户词家族，再让模型扩展或重构查询。 |
| `target_count` | 否 | 目标返回数，默认 `25`，范围 `1–25`。`completed` 时 `ads.length` 必须严格等于该值。 |

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

服务端以规范化后的 `country`、`category`、去重排序后的 `keywords` 与 `target_count` 计算请求指纹：

- 相同 `external_user_id` 且规范化请求相同：未失败、未过期任务返回同一 `task_id`，响应为 `200 OK` 且 `idempotent_replay: true`。
- 相同 `external_user_id` 但请求不同：返回 `409 Conflict`，错误码 `external_user_id_payload_conflict`。
- 相同规范化请求此前为 `failed`：服务端复用同一逻辑任务并重新入队；调用方可用**相同** `external_user_id` 重试。
- 成功结果超过 24 小时后过期，幂等键会释放；届时可用原 `external_user_id` 创建新任务。

## 2. 轮询任务结果

```http
GET /api/v1/integrations/ad-research/jobs/{task_id}
```

新任务只会处于 `queued`、`processing`、`completed` 或 `failed` 四种状态之一。仅在 `queued`、`processing` 时按 `poll_after_seconds` 轮询（建议至少 3 秒）；没有 callback，也没有“外部系统确认已读取”的接口。

成功结果及其最终系统封面/关键帧保留 **24 小时**。结果过期后 GET 返回 `410 Gone`，不返回已清理的结果或媒体。

### 处理中响应重点

`progress` 是任务当前快照，字段会随着轮次更新。常用字段如下：

| 字段 | 含义 |
| --- | --- |
| `raw_collected` / `deduplicated` | 原始召回量 / `ad_library_id` 去重后的候选数。 |
| `technical_qualified` | 已通过媒体、时长与封面/关键帧技术条件的候选数。 |
| `model_scored` / `model_scoring_failed` | 已完成视觉评分数 / 当前仍未评分成功的候选数。 |
| `model_scoring_states` | `pending`、`scoring`、`retryable_failed`、`scored`、`permanent_failed` 的状态计数。 |
| `selected_count` | 按当前排序暂时可选出的数量；仅 `completed` 时可读取最终 `ads`。 |
| `qualified_visual_count` | 当前 Top N 中视觉合格候选数。 |
| `quality_target_met` | 当前是否已取得 `target_count` 条视觉合格候选。 |
| `fallback_count` / `quality_grade` | 当前 Top N 中 P4 补位数，以及 `complete`、`fallback_used` 或 `insufficient_source_inventory`。 |
| `query_origin_counts` | 已执行查询的来源计数：`user_exact`、`user_expanded`、`model_exploration`、`model_recovery`。 |
| `round_budget` | 当前轮次、剩余轮次、当前原始候选上限和是否进入补采/保证数量模式。 |

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
    "model_scoring_states": {"pending": 0, "scoring": 0, "retryable_failed": 1, "scored": 18, "permanent_failed": 0},
    "selected_count": 18,
    "qualified_visual_count": 14,
    "quality_target_met": false,
    "fallback_count": 4,
    "quality_grade": "fallback_used",
    "query_origin_counts": {"user_exact": 2, "user_expanded": 4, "model_exploration": 2},
    "round_budget": {"standard_rounds": 4, "max_rounds": 10, "current_round": 2, "remaining_rounds": 8, "max_raw_candidates": 650, "remaining_raw_candidates": 550, "quality_supplement_mode": false, "guarantee_mode": false}
  },
  "poll_after_seconds": 3,
  "research_summary": {},
  "ads": null,
  "result_expires_at": null,
  "error": null
}
```

### 完成响应重点

`completed` 时：

- `ads` 长度严格等于请求的 `target_count`；
- 每条广告均已完成技术检查和视觉评分；
- `research_summary` 与最终 `progress` 同步质量、来源归因和模型评分状态；
- `quality_grade: "complete"` 表示全部结果都是视觉合格候选，`"fallback_used"` 表示来源总数足够但部分 P4 非视觉合格候选被透明补位；
- 所有返回广告均唯一，绝不通过复制结果凑数。

示例（为便于阅读，以下以 `target_count=1` 展示）：

```json
{
  "task_id": "adr_01...",
  "external_user_id": "external-research-example-0001",
  "status": "completed",
  "stage": "completed",
  "round": 3,
  "progress": {
    "selected_count": 1,
    "qualified_visual_count": 1,
    "quality_target_met": true,
    "fallback_count": 0,
    "quality_grade": "complete",
    "query_origin_counts": {"user_exact": 1, "user_expanded": 2},
    "model_scoring_states": {"pending": 0, "scoring": 0, "retryable_failed": 0, "scored": 8, "permanent_failed": 0},
    "rounds_used": 3
  },
  "poll_after_seconds": null,
  "research_summary": {
    "raw_collected": 78,
    "deduplicated": 65,
    "technical_qualified": 14,
    "model_scored": 8,
    "selected_count": 1,
    "qualified_visual_count": 1,
    "quality_target_met": true,
    "fallback_count": 0,
    "quality_grade": "complete",
    "rounds_used": 3,
    "termination_reason": "quality_target_met"
  },
  "ads": [
    {
      "ad_library_id": "...",
      "first_source_query_id": "r1_q01",
      "source_query_ids": ["r1_q01", "r2_q03"],
      "matched_query_origins": ["user_exact", "user_expanded"],
      "matched_user_keywords": ["rummy"],
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
      "game_context_present": true,
      "betting_context_present": true,
      "money_only_promo": false,
      "negative_visual_type": "none",
      "component_scores": {"gameplay_ui": 30.0, "betting_mechanism": 20.0, "in_game_value_ui": 12.0, "gambling_style": 6.0, "visual_clarity": 5.0, "media_quality": 3.0},
      "analysis_confidence": 0.87,
      "visual_evidence": [{"frame_index": 1, "detail": "gold coin reward animation"}],
      "retrieval_hints": ["slot reels", "jackpot UI"],
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
  "result_expires_at": "2026-07-25T08:00:00Z",
  "error": null
}
```

`cover_url` 与 `frame_urls` 是系统保存的最终媒体，可用于展示和人工复核，并与结果一同只保留 24 小时。`thumbnail_url` 是采集器提供的来源 URL，不等同于系统保存的封面。

## 关键词优先、查询闭环与视觉排序

外部系统只传国家、类别、可选关键词和目标数量；不传内部阈值、词包、轮次或模型配置。

1. 有 `keywords` 时，原始用户关键词优先：系统以 `user_exact` 记录原词执行，以 `user_expanded` 记录基于该原词的模型/确定性扩展，并保留 `parent_keyword`、`matched_user_keywords` 与 `matched_query_origins` 作为来源归因。
2. 查询规划模型会根据国家、类别、原始关键词、受控漏斗统计、缺口信号和剩余预算调整后续查询；查询级性能给模型的统计只包含查询 ID 与数字漏斗，不携带原始广告内容。
3. 同一 `ad_library_id` 即使被多个查询召回，也只做一次媒体处理和最终视觉评分；最终结果保留全部来源查询 ID。
4. 每个查询和每轮摘要可提供召回、去重、技术通过、模型评分、视觉质量、最终入选数及 P1–P4 数量。`score_above_55` 只是查询诊断，不是完成门槛。

视觉评分模型为 `gpt-5.4-mini`，Responses 调用固定使用：

```json
{"reasoning": {"effort": "none"}}
```

模型只接收：类别、技术阶段确认的视频时长、帧数，以及封面/关键帧图像。它不会接收或使用文案、标题、CTA、广告主、主页、落地页 URL、来源关键词、`active_days` 或公开持续投放分数。

服务端校验以下视觉分项并计算 `visual_total`：

| 分项 | 最高分 |
| --- | ---: |
| `gameplay_ui` | 35 |
| `betting_mechanism` | 25 |
| `in_game_value_ui` | 15 |
| `gambling_style` | 10 |
| `visual_clarity` | 10 |
| `media_quality` | 5 |

`visual_priority` 分为 `game_gambling`、`sports_betting`、`gambling_adjacent`、`unrelated`。排序优先保证视觉合格候选，再按视觉优先级、`visual_total`、分项、置信度、公开投放时长和稳定 ID 排序；用户关键词来源只在完全相同的视觉分数下作为稳定的末级破同分项。

最终排序严格使用 `final_score = visual_total`，不迭加公开持续投放信号分。常规采集最多 6 轮、650 条原始候选，且每个查询最多召回 50 条；为了保证数量，仅在常规模式未取得足数结果时进入第 7–10 轮的保证数量模式。

视觉优先级为 P1 `game_gambling`、P2 `sports_betting`、P3 `gambling_adjacent`、P4 `unrelated`。只有在高关联候选不足但足以完成目标数量时，P4 才可透明补位：`is_fallback: true`，`fallback_reason: "insufficient_high_relevance_candidates"`。

## 质量补采、保证数量与失败边界

内部采集每个查询最多召回 50 条。调用方无需维护查询轮次或候选预算：

- **第 1–4 轮：** 标准采集；
- **第 5–6 轮：** 视觉质量补采，针对当前缺口调整查询；
- **第 7–10 轮：** 仅在前 6 轮仍无法凑足 `target_count` 条结果或视觉合格候选时进入保证数量模式；
- **预算：** 常规模式最多 650 条原始候选；保证数量模式最多 1200 条原始候选。它们是服务端配置，不由外部请求覆盖。

完成和失败规则：

1. 当已取得 `target_count` 条视觉合格候选，且用户原始关键词覆盖条件满足时，可提前 `completed`；
2. 若到达最终轮/预算时，已评分技术合格候选总数达到 `target_count`，按视觉排序返回 Top N；必要时用 P4 `unrelated` 透明补位，并标记 `is_fallback: true`；
3. 若唯一广告来源、技术合格候选或评分成功候选不足 `target_count`，任务 `failed`，`ads: null`，不会重复广告或返回部分结果；
4. `research_summary.termination_reason` 说明结束原因，例如 `quality_target_met`、`fallback_target_met`、`insufficient_source_inventory`、`insufficient_technically_qualified_inventory`。

## 任务状态与错误

| HTTP / `status` | 含义 | 外部系统动作 |
| --- | --- | --- |
| `202` / `queued` | 已创建或失败重试后重新入队。 | 按建议时间轮询。 |
| `processing` | 正在规划、采集、媒体准备或视觉评分。 | 继续轮询。 |
| `completed` | 已取得严格等于 `target_count` 条技术合格且模型评分成功的结果。 | 读取 `ads`，停止轮询。 |
| `insufficient` | `legacy insufficient` 历史只读兼容状态，可能带旧 `ads`；新任务绝不会产生或出现该状态。 | 仅按历史结果处理，不应作为新任务终态。 |
| `failed` | 达到轮次/原始候选预算仍无法得到严格等于 `target_count` 条已评分技术合格广告，或发生队列、采集器、媒体或未捕获系统异常；`ads` 为 `null`。 | 查看 `error.code` 后可用同一 `external_user_id` 重试，或联系系统维护方；停止轮询。 |
| `410 Gone` / `expired` | 成功结果已超过 24 小时；GET 返回 `410 Gone`，结果和系统保存的封面/关键帧均已清除。 | 使用新的请求重新创建任务；原 `external_user_id` 已可复用。 |
| `404 Not Found` | `task_id` 不存在。 | 检查请求 ID。 |


## 固定运行时资源

以下是服务端固定配置，不通过请求体修改：

```env
AD_RESEARCH_WORKER_CONCURRENCY=2
AD_RESEARCH_MODEL_CONCURRENCY=6
AD_RESEARCH_MEDIA_CONCURRENCY=6
AD_RESEARCH_FRAME_CONCURRENCY=4
AD_RESEARCH_MODEL=gpt-5.4-mini
```

Worker 使用 Celery Worker 与 Redis 队列；模型调用使用 Redis 全局租约控制跨 Worker 并发。任务完成时仅保留 Top N 的媒体目录；未选中候选、失败任务与 stale 任务的媒体会被清理。
