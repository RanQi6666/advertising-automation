# 公开广告研究接口（V1）

## 用途与边界

外部投放系统提交一个国家、广告类别和可选关键词，AI 系统异步检索 **Meta Ad Library 中公开可见** 的广告资料，并返回最多 25 条符合条件的视频广告候选。

首期采集器为固定版本的 `athm793/meta-ads-scraper`，由内部 Bridge 调用；外部系统不直接访问采集器，也不需要提供关键词词包或模型筛选参数。

本接口只研究公开广告资料，且有以下明确边界：

- 不访问、不跟踪或不模拟广告落地页用户行为；不尝试识别、确认或复现 Cloaking。
- 不返回真实花费、CPC、CPA、ROAS、转化量或真实投放成本；公开可见的持续投放信号仅作为排序代理信号。
- 只保留 `ACTIVE`、具有公开视频与封面、投放时间大于等于 1 天、且视频时长不超过 30 秒的候选。
- 最多检索 4 轮、最多 500 条原始候选；达到 `target_count` 前会继续补采。最终不足目标数量时不会用低质量结果凑满。

## 鉴权

所有接口沿用 AI 系统鉴权。建议使用：

```http
Authorization: Bearer <AI_ADS_ACCESS_TOKEN>
Content-Type: application/json
```

基础地址：`https://ai.ggcss.xyz`

## 1. 创建研究任务

```http
POST /api/v1/integrations/ad-research/jobs
```

请求体：

```json
{
  "external_user_id": "external-research-20260721-0001",
  "country": "IN",
  "category": "gambling",
  "keywords": ["rummy", "casino"],
  "target_count": 25
}
```

字段说明：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `external_user_id` | 是 | 本次研究请求的外部唯一 ID，用作幂等键；不是长期用户账号 ID。长度 1–128。 |
| `country` | 是 | 国家代码，例如 `IN`；服务端会统一转为大写。 |
| `category` | 是 | 业务类别，例如 `ecommerce`、`game`、`gambling`、`weight_loss`。服务端模型负责规划词、识别类别与排序，不要求外部系统提供固定规则。 |
| `keywords` | 否 | 业务补充词，最多 24 个。服务端会去重，并可根据每轮缺口由模型自动调整后续查询词。 |
| `target_count` | 否 | 最多返回的数量，默认 `25`，范围 `1–25`。 |

首次创建成功返回 `202 Accepted`：

```json
{
  "task_id": "adr_01...",
  "external_user_id": "external-research-20260721-0001",
  "status": "queued",
  "idempotent_replay": false,
  "poll_url": "/api/v1/integrations/ad-research/jobs/adr_01...",
  "poll_after_seconds": 3
}
```

### 幂等规则

相同 `external_user_id` 加上语义相同的请求（国家、类别、去重排序后的关键词、目标数量相同）会返回同一个任务，响应为 `200 OK` 且 `idempotent_replay: true`。

相同 `external_user_id` 但请求内容不同，返回 `409 Conflict`：

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

建议按响应中的 `poll_after_seconds` 轮询；任务排队或采集中建议至少间隔 3 秒，不需要 callback，也不需要“确认已获取结果”的接口。

处理中示例：

```json
{
  "task_id": "adr_01...",
  "external_user_id": "external-research-20260721-0001",
  "status": "processing",
  "stage": "classifying",
  "round": 2,
  "progress": {
    "raw_collected": 100,
    "technical_qualified": 34,
    "model_relevant": 18
  },
  "poll_after_seconds": 3,
  "research_summary": {},
  "ads": null,
  "result_expires_at": null,
  "error": null
}
```

完成示例：

```json
{
  "task_id": "adr_01...",
  "external_user_id": "external-research-20260721-0001",
  "status": "completed",
  "stage": "completed",
  "round": 2,
  "progress": {},
  "poll_after_seconds": null,
  "research_summary": {
    "raw_collected": 100,
    "deduplicated": 92,
    "technical_qualified": 34,
    "model_relevant": 25,
    "selected_count": 25,
    "minimum_active_days": 1,
    "maximum_video_seconds": 30.0,
    "technical_rejection_summary": {
      "duration_over_30": 42,
      "active_days_below_minimum": 8
    },
    "model_exclusion_summary": {
      "category_not_matched": 5,
      "category_confidence_below_threshold": 3
    },
    "rounds": [
      {
        "round": 1,
        "queries": ["..."],
        "round_raw_collected": 50,
        "technical_qualified": 17,
        "selected_count": 12
      }
    ],
    "performance_signal_notice": "public_performance_signal_score is a public continuity proxy, not actual spend, CPC, CPA, ROAS, or conversion data."
  },
  "ads": [
    {
      "ad_library_id": "...",
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
      "category_confidence": 0.94,
      "creative_relevance_score": 91,
      "public_performance_signal_score": 72,
      "real_money_signal_score": 0.88,
      "business_type": "...",
      "evidence": {
        "text": ["..."],
        "visual": ["..."],
        "public_signals": ["..."],
        "public_risk_signals": ["..."]
      }
    }
  ],
  "result_expires_at": "2026-07-22T08:00:00Z",
  "error": null
}
```

## 任务状态与错误

| HTTP / `status` | 含义 | 外部系统动作 |
| --- | --- | --- |
| `202` / `queued` | 已创建，等待 Worker。 | 按建议时间轮询。 |
| `processing` | 正在采集、技术过滤或模型评分。 | 继续轮询。 |
| `completed` | 已选出目标数量的合格广告。 | 读取 `ads`。 |
| `insufficient` | 已达到采集上限，但合格数量不足目标；仍会返回已找到的合格广告。 | 读取已有 `ads` 与 `research_summary.reason`，可使用新的 `external_user_id` 重新发起任务。 |
| `failed` | 队列、采集器或模型出现不可恢复错误。 | 查看 `error.code` 后使用同一 `external_user_id` 重试，或联系系统维护方。 |
| `410 Gone` | 任务结果已超过 24 小时有效期，完整结果已经清除。 | 使用新的请求重新创建任务；原 `external_user_id` 已可复用。 |
| `404 Not Found` | `task_id` 不存在。 | 检查请求 ID。 |

## 后台处理简述

1. GPT-5.4 mini（`reasoning.effort: none`）根据国家、类别、补充关键词和前轮缺口规划查询词。
2. 内部采集 Bridge 对每个查询最多召回 50 条公开视频广告。
3. 服务端按广告 ID 去重，并硬过滤投放状态、媒体可访问性、投放天数和视频时长。
4. GPT-5.4 mini 对技术合格候选结合文案、封面及公开持续投放信号评分、归类与排序。
5. 每轮结束后，若合格结果不足目标数量，系统使用新的查询词继续补采；达到目标或上限后返回结果。

模型调用采用 Redis 全局租约限流：广告研究 Worker 并发为 2，模型并发总上限为 6。最终结果仅在任务库中临时保留 24 小时供轮询，过期后自动清除完整结果。

每轮补采前，系统会把 `previous_queries`、`technical_rejection_summary`、`model_exclusion_summary` 和 `duplicate_count` 传给 GPT-5.4 mini。模型据此调整下一轮公开广告库查询方向；编排层会对模型返回的 query 做大小写无关的跨轮去重，避免重复采集。`research_summary.rounds` 记录每轮实际使用的查询、原始召回、新增去重候选和最终保留进度，但不会返回全部技术淘汰广告的完整内容。

Production: set `REDIS_URL=redis://redis:6379/2` explicitly for the shared model-lease store. If omitted, the service falls back to `CELERY_BROKER_URL`; a separate Redis DB is recommended so lease keys do not share the Celery broker namespace.

## Collector build configuration

`meta_ads_collector` pins `athm793/meta-ads-scraper` to a known commit at build time and downloads Playwright Chromium. To reduce transient Debian CDN or network-proxy failures, the build retries APT downloads and browser downloads up to three times each.

The default APT HTTPS mirror host is `mirrors.aliyun.com`. Compose reads the following build-only setting from its environment file. External API callers do not need, and cannot provide, this value:

```env
META_ADS_APT_MIRROR_HOST=mirrors.aliyun.com
```

The value must be a mirror hostname only: do not include `http://`, `https://`, or a path. If the test server cannot reliably access the default mirror, change this value in that server's `.env.production` to a reachable Debian mirror hostname, then rebuild the Collector and its dependent Worker:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build meta_ads_collector worker_ad_research
```

The first Docker Hub base-image pull can still be affected by a short-lived Docker Desktop/proxy network interruption. If it fails with an OAuth `EOF`, retry the build. That condition is separate from the Collector code, the pinned upstream scraper, and a query returning zero ads.
