# 公开广告研究接口（V1）

## 用途、采集器与边界

外部投放系统提交一个国家、广告类别和可选关键词，AI 系统异步检索 **Meta Ad Library 中公开可见** 的广告资料，并返回最多 25 条具备可访问短视频与视觉评分结果的候选广告。

首期采集器固定使用 [`athm793/meta-ads-scraper`](https://github.com/athm793/meta-ads-scraper)，由内部 `meta_ads_collector` Bridge 调用；外部系统不直接访问采集器，也不需要维护关键词词包或视觉评分阈值。

本接口只研究公开广告资料，明确不做以下事项：

- 不访问、不跟踪、不模拟广告落地页用户行为；不尝试识别、确认或复现 Cloaking。
- 不返回或推断真实花费、投放成本、CPC、CPA、ROAS、转化量、利润。
- 不把文案、标题、CTA、主页名称、落地页 URL、域名作为广告类别的硬淘汰条件。
- 不保证素材实际业务合规性，也不承诺公开持续投放信号等同于真实效果。

### 技术硬条件

候选只会在以下条件全部满足后进入视觉评分：

1. 广告状态为 `ACTIVE`；
2. `active_days >= 1`；
3. 存在且可安全访问的公开视频 URL；
4. 最终确认的视频时长 `<= 30` 秒；
5. 至少有一张可分析图片：可用原始封面，或从视频自动抽取关键帧生成封面。

时长确认按 Collector 已报时长、远程 `ffprobe`（含重试）、下载后本地 `ffprobe` 的顺序执行。视频优先按已知短时长处理；视频下载、时长探测和抽帧均为并发处理，不逐条串行执行。

- 初次导出 20%、50%、80% 三张关键帧；原封面不存在或不可解码时，使用关键帧生成 `cover.jpg`。
- 首次视觉评分置信度低于 `0.60` 时，补充 35%、65% 两张关键帧后再评分，最多使用 5 张帧。
- 无法确认时长、下载视频失败或无任何可分析视觉图时，才会被技术淘汰。

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
  "external_user_id": "external-research-20260723-0001",
  "country": "IN",
  "category": "gambling",
  "keywords": ["rummy", "casino"],
  "target_count": 25
}
```

字段说明：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `external_user_id` | 是 | 本次研究请求的外部唯一 ID，并作为幂等键；不是长期用户账号 ID。长度 1–128。 |
| `country` | 是 | 国家代码，例如 `IN`；服务端统一为大写。 |
| `category` | 是 | 业务类别，例如 `ecommerce`、`game`、`gambling`、`weight_loss`。模型据此规划检索词和解释视觉评分方向；不会成为基于文本的硬淘汰规则。 |
| `keywords` | 否 | 业务补充词，最多 24 个。模型可按每轮公开召回和高分视觉元素自动调整后续检索词。 |
| `target_count` | 否 | 最多返回数量，默认 `25`，范围 `1–25`。 |

首次创建成功返回 `202 Accepted`：

```json
{
  "task_id": "adr_01...",
  "external_user_id": "external-research-20260723-0001",
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

建议按响应中的 `poll_after_seconds` 轮询；任务排队或采集中建议至少间隔 3 秒。不使用 callback，也不需要“确认已获取结果”的接口。

处理中示例：

```json
{
  "task_id": "adr_01...",
  "external_user_id": "external-research-20260723-0001",
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
    "selected_count": 18,
    "score_distribution": {"0_19": 0, "20_39": 1, "40_54": 4, "55_69": 8, "70_89": 5, "90_100": 0},
    "twenty_fifth_score": null
  },
  "poll_after_seconds": 3,
  "research_summary": {},
  "ads": null,
  "result_expires_at": null,
  "error": null
}
```

完成示例（字段为示意，分数范围为 `0–100`）：

```json
{
  "task_id": "adr_01...",
  "external_user_id": "external-research-20260723-0001",
  "status": "completed",
  "stage": "completed",
  "round": 2,
  "poll_after_seconds": null,
  "research_summary": {
    "raw_collected": 100,
    "deduplicated": 92,
    "technical_qualified": 34,
    "model_scored": 30,
    "model_scoring_failed": 1,
    "model_relevant": 30,
    "selected_count": 25,
    "minimum_active_days": 1,
    "maximum_video_seconds": 30.0,
    "quality_supplement_threshold": 55.0,
    "twenty_fifth_score": 63.0,
    "score_distribution": {"0_19": 0, "20_39": 1, "40_54": 4, "55_69": 13, "70_89": 11, "90_100": 1},
    "high_score_visible_elements": ["slot reels", "gold coins", "VIP badge"],
    "technical_rejection_summary": {"duration_over_30": 42, "no_analyzable_visual": 3},
    "rounds": [
      {
        "round": 1,
        "queries": ["..."],
        "round_raw_collected": 50,
        "technical_qualified": 17,
        "model_scored": 16,
        "selected_count": 16,
        "twenty_fifth_score": null
      }
    ]
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
      "final_score": 76.0,
      "visual_total": 71.0,
      "public_continuity_points": 5.0,
      "analysis_confidence": 0.87,
      "visible_elements": ["slot reels", "gold coins", "bonus UI"],
      "visual_evidence": [{"frame_index": 1, "evidence": "gold coin reward animation"}],
      "media": {
        "cover_url": "https://ai.ggcss.xyz/storage/ad-research/adr_01.../.../cover.jpg",
        "cover_source": "generated_frame",
        "frame_urls": ["https://ai.ggcss.xyz/storage/ad-research/adr_01.../.../frame_20.jpg"],
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

`cover_url` 和 `frame_urls` 是系统存储的最终媒体，可直接用于展示或人工复核；与任务结果一样只保留 24 小时。返回中的原始 `thumbnail_url` 仅为采集器提供的来源 URL，不等同于系统最终保存的封面。

### 评分与排序

视觉模型固定使用 **GPT-5.4 mini**，调用参数固定为：

```json
{"reasoning": {"effort": "none"}}
```

模型视觉评分只接收 Worker 已准备的封面/关键帧及非文本媒体元数据（时长、投放天数、帧数量）。它不接收或依据文案、标题、CTA、广告主、主页、落地页 URL、缩略图 URL 或 Worker 本地路径做类别硬筛。

`visual_total` 最高 90 分，主要依据画面是否出现或呈现以下元素/风格：

- 直接博彩元素：老虎机、`777`、轮盘、扑克、荷官、Aviator/Crash、捕鱼、下注、赔率、余额；
- 博彩奖励和 UI：金币、水晶、Bonus、Jackpot、VIP、WIN、倍率、爆奖 UI、奖励特效；
- 赌场/博彩游戏整体视觉风格及画面可辨识度。

`public_continuity_points` 最高 10 分，是公开可见投放天数代理分：1–2 天为 1 分、3–6 天为 3 分、7–13 天为 5 分、14–29 天为 7 分、30 天及以上为 10 分。

最终按以下分数排序：

```text
final_score = visual_total + public_continuity_points
```

排序稳定规则依次为：`final_score`、`visual_total`、`active_days`、帧数、`ad_library_id`。不存在模型类别命中、`category_confidence`、`recommendation` 或“明显无关”等硬淘汰门槛。

当已获得 `target_count` 条候选、但第 `target_count` 条的 `final_score < 55` 时，系统继续补采以尝试提高结果质量。`55` 只是补采阈值，不是广告淘汰线；达到轮数或原始候选上限时，仍会按分数返回已有 Top N。

`model_relevant` 是兼容字段，始终等于 `model_scored`，不再表示文本类目硬命中。

## 任务状态与错误

| HTTP / `status` | 含义 | 外部系统动作 |
| --- | --- | --- |
| `202` / `queued` | 已创建，等待 Worker。 | 按建议时间轮询。 |
| `processing` | 正在规划、采集、技术准备或视觉评分。 | 继续轮询。 |
| `completed` | 已取得 `target_count` 条可评分候选；如果补采预算耗尽，可能仍带 `quality_supplement_exhausted` 原因。 | 读取 `ads`。 |
| `insufficient` | 达到采集边界前未取得 `target_count` 条可评分候选；仍返回已找到的 Top N。 | 读取已有 `ads` 与 `research_summary.reason`，可使用新的 `external_user_id` 重新发起任务。 |
| `failed` | 队列、采集器或未捕获的系统异常导致任务失败。 | 查看 `error.code` 后使用同一 `external_user_id` 重试，或联系系统维护方。 |
| `410 Gone` | 结果已超过 24 小时，结果和系统保存的封面/关键帧都已清除。 | 使用新的请求重新创建任务；原 `external_user_id` 已可复用。 |
| `404 Not Found` | `task_id` 不存在。 | 检查请求 ID。 |

## 后台处理与资源配置

1. GPT-5.4 mini 根据国家、类别、补充关键词、技术淘汰汇总和高分视觉元素规划/调整检索词。
2. 内部采集 Bridge 对每个查询最多召回 50 条公开视频广告。
3. 服务端去重后并发准备视频媒体，执行技术硬条件、时长确认、封面下载/生成和关键帧导出。
4. GPT-5.4 mini 对技术合格候选做纯视觉评分；低置信度候选补帧并复评。
5. 服务端计算 `final_score`，保留 Top N；在数量不足或第 N 名低于 55 分时继续下一轮补采。
6. 完成时仅保留 Top N 的媒体目录；任务结果及最终媒体保留 24 小时。过期、失败或 stale 任务会清理整个任务媒体目录。

模型调用受 Redis 全局租约限流，广告研究 Worker 建议并发为 2，单 Worker 模型并发为 6：

```env
AD_RESEARCH_WORKER_CONCURRENCY=2
AD_RESEARCH_MODEL_CONCURRENCY=6
AD_RESEARCH_MEDIA_CONCURRENCY=6
AD_RESEARCH_FRAME_CONCURRENCY=4
AD_RESEARCH_MEDIA_ROOT=/data/storage/ad-research
AD_RESEARCH_MEDIA_DOWNLOAD_TIMEOUT_SECONDS=60
AD_RESEARCH_MEDIA_DOWNLOAD_MAX_BYTES=83886080
AD_RESEARCH_MEDIA_RETRY_ATTEMPTS=2
AD_RESEARCH_FFPROBE_TIMEOUT_SECONDS=12
AD_RESEARCH_FFMPEG_FRAME_TIMEOUT_SECONDS=15
```

生产环境应显式设置共享模型租约 Redis：

```env
REDIS_URL=redis://redis:6379/2
```

若未设置，服务会回退到 `CELERY_BROKER_URL`；推荐使用单独 Redis DB，避免租约键与 Celery broker 命名空间混用。

## Collector 构建配置

`meta_ads_collector` 在构建时固定 `athm793/meta-ads-scraper` 的已知 commit，并下载 Playwright Chromium。为降低 Debian CDN 或网络代理的瞬时失败，构建会对 APT 下载和浏览器下载各重试最多三次。

默认 APT HTTPS 镜像主机是 `mirrors.aliyun.com`。Compose 从环境文件读取以下仅构建参数；外部 API 调用方不需要也不能提供该值：

```env
META_ADS_APT_MIRROR_HOST=mirrors.aliyun.com
```

值只能是镜像主机名，不能包含 `http://`、`https://` 或路径。测试服务器不能稳定访问默认镜像时，在服务器 `.env.production` 修改为可达 Debian 镜像主机，然后重建 Collector 和其依赖 Worker：

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build meta_ads_collector worker_ad_research
```

首次拉取 Docker Hub 基础镜像仍可能受短暂 Docker/proxy 网络中断影响；如果出现 OAuth `EOF`，重试构建。该问题与 Collector 代码、固定上游 scraper 或某次查询返回零广告无关。
