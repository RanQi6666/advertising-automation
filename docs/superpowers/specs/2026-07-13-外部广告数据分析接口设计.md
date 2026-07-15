# 外部 Facebook 广告投放分析异步 API 设计

- 日期：2026-07-13
- 状态：已确认，等待实施计划
- 适用项目：`Advertising Automation`
- API 版本：v1
- 结果协议：`facebook_ad_analysis_v1`

## 1. 背景

现有同步接口：

```http
POST /api/v1/integrations/ad-performance/analyses
```

已经能够接收 `campaign`、`adset`、`creative`、`insight`、`siblings` 和
`metadata_json`，并生成通用广告表现分析。但是，新的外部系统对接需要：

1. 一次提交完整 JSON 后立即获得任务标识，不等待大模型、媒体处理和联网研究完成；
2. 由外部系统主动查询分析状态和最终结果，不需要回调；
3. 图片或视频只提供原始 URL，缩略图和视频关键帧由本系统生成；
4. 结果围绕 Facebook/Meta 投放漏斗、投放目标和投手决策组织；
5. 除分析提交的真实投放数据外，系统需要实时联网研究公开相似广告；
6. 联网公开广告只能作为市场研究、创意参考和公开表现代理信号，不得伪装成真实 CTR、CPA 或 ROAS；
7. 使用现有 PostgreSQL、Redis 和 Celery，并将广告分析工作放入独立队列。

本设计新增异步任务接口，同时保留现有同步接口，避免影响已接入调用方。

## 2. 目标

### 2.1 产品目标

外部系统只需提交一份 Facebook 广告 JSON。系统自动完成：

```text
校验和幂等处理
→ 下载广告素材
→ 图片分析或视频抽帧
→ 分析当前广告真实投放成效
→ 自动识别产品、行业、国家、语言和创意角度
→ 联网采集公开相似广告
→ 筛选具有较强公开表现信号的参考广告
→ 生成有证据、有优先级、可执行、可验证的优化建议
→ 持久化结果
→ 供外部系统轮询查询
```

### 2.2 技术目标

- API 请求快速返回，避免 HTTP 长连接和网关超时；
- `external_request_id` 提供可靠幂等能力；
- `analysis_id` 作为唯一查询键；
- PostgreSQL 是任务状态和结果的权威数据源；
- Redis/Celery 只负责调度和执行；
- 媒体、联网研究或大模型部分失败时，尽可能降级并产生有意义结果；
- 所有重要结论均可追溯到输入数据、计算公式、媒体证据或公开来源；
- 结果结构可以被外部系统直接展示，也能被程序用于预警和生成优化任务。

## 3. 非目标

v1 不包含：

- 分析完成后主动回调外部系统；
- 外部账号与本系统租户、账号或用户的绑定；
- `external_account_id + external_request_id` 联合唯一；
- 要求外部系统提供搜索词、竞品名称或研究上下文；
- 要求外部系统提供 `thumbnail_url` 或 `video_keyframes`；
- 通过 Meta Marketing API 获取其他广告主的私有成效指标；
- 接入付费广告情报数据库；
- 绕过登录、验证码、访问控制或反自动化机制；
- 对视频音频、完整运动连续性或语音内容进行分析；
- 自动修改、暂停、扩量或发布 Facebook 广告；
- 以联网公开信号推算竞品 CTR、CPC、CPA、购买量、收入或 ROAS。

## 4. 已确认的核心决策

### 4.1 标识符职责

| 字段 | 生成方 | 用途 |
| --- | --- | --- |
| `external_request_id` | 外部系统 | POST 创建和重试时的全局幂等键 |
| `analysis_id` | 本系统 | GET 查询状态和结果的唯一键 |

v1 不引入调用方身份或 `external_account_id`。共享 Bearer Token 继续负责接口鉴权，但不参与幂等键计算。

### 4.2 接口

新增：

```http
POST /api/v1/integrations/ad-performance/analysis-jobs
GET  /api/v1/integrations/ad-performance/analysis-jobs/{analysis_id}
```

保留：

```http
POST /api/v1/integrations/ad-performance/analyses
```

不新增按 `external_request_id` 查询的 GET 接口。若 POST 响应丢失，调用方使用相同
`external_request_id` 和相同 JSON 重新 POST，系统返回原 `analysis_id`。

### 4.3 异步处理

复用现有 PostgreSQL、Redis、Celery，新增：

```text
队列：ad_analysis_queue
Worker：worker_ad_analysis
```

广告分析任务不得运行在 API 进程内，也不得挤占视频生成队列。

### 4.4 联网研究

v1 只研究公开来源，不接入付费广告情报数据库。研究条件由系统从请求数据、素材和公开落地页自动推断，调用方不传 `research_context`。

## 5. 外部 API 契约

### 5.1 鉴权

沿用现有集成接口鉴权：

```http
Authorization: Bearer <shared-access-token>
Content-Type: application/json
```

页面查询参数鉴权兼容能力不作为本接口文档的推荐调用方式。服务间调用统一推荐 Bearer Token。

### 5.2 创建分析任务

```http
POST /api/v1/integrations/ad-performance/analysis-jobs
```

#### 请求体

```json
{
  "external_request_id": "ad-analysis-20260713-000001",
  "source_type": "external",
  "external_user_id": "5",
  "date_preset": "last_7d",
  "date_start": "2026-06-15",
  "date_stop": "2026-06-21",
  "campaign": {
    "id": "23",
    "name": "new1",
    "fb_id": "120247498350000238",
    "status": "ACTIVE",
    "objective": "OUTCOME_TRAFFIC"
  },
  "adset": {
    "id": "19",
    "name": "new1",
    "fb_id": "120247498370850238",
    "status": "ACTIVE",
    "daily_budget": "100",
    "billing_event": "IMPRESSIONS",
    "optimization_goal": "LINK_CLICKS",
    "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
    "countries": "US",
    "age_min": 18,
    "age_max": 65
  },
  "creative": {
    "id": "19",
    "name": "new12",
    "facebook_ad_id": "120247505013060238",
    "ad_status": "PAUSED",
    "message": "广告正文文案",
    "description": "",
    "link": "https://example.com/landing-page",
    "btn_type": "LEARN_MORE",
    "creative_type": "image",
    "image_url": "https://newpixel.messrocts.com/uploads/example.jpg",
    "video_url": null
  },
  "insight": {
    "spend": "0.24",
    "impressions": "1079",
    "reach": "937",
    "frequency": "1.151547",
    "clicks": "83",
    "inline_link_clicks": "86",
    "ctr": "7.692308",
    "inline_link_click_ctr": "7.970343",
    "cpc": "0.002892",
    "cpm": "0.222428",
    "actions": [
      {"action_type": "link_click", "value": "86"},
      {"action_type": "landing_page_view", "value": "23"}
    ],
    "cost_per_action_type": [
      {"action_type": "landing_page_view", "value": "0.010435"}
    ],
    "date_start": "2026-06-15",
    "date_stop": "2026-06-21"
  },
  "siblings": [],
  "metadata_json": {
    "source": "external_ad_system"
  }
}
```

#### v1 必填规则

- `external_request_id`：非空字符串，去除首尾空白后长度 1–128；
- `campaign`、`adset`、`creative`、`insight`：必须为 JSON 对象；
- `creative.creative_type`：至少支持 `image` 和 `video`；
- 图片广告必须提供 `creative.image_url`；
- 视频广告必须提供 `creative.video_url`；
- URL 必须是完整 HTTPS URL；
- `siblings` 可省略或传空数组；
- `external_user_id` 只是业务上下文，不参与鉴权和幂等唯一性；
- 未知的 Meta 扩展字段允许保留在原始输入中，避免频繁修改接口协议。

#### 明确不接收

```text
callback_url
external_account_id
research_context
search_keywords
competitor_names
thumbnail_url
video_keyframes
```

即使旧数据包中包含 `thumbnail_url`，新异步接口也不依赖它；服务端忽略该字段并生成自己的内部缩略图或关键帧。

### 5.3 创建成功

新任务：

```http
HTTP/1.1 202 Accepted
```

```json
{
  "code": 1001,
  "message": "analysis job accepted",
  "data": {
    "analysis_id": "ana_01JZZ7M3P4Q5R6S7T8V9W0X1Y2",
    "external_request_id": "ad-analysis-20260713-000001",
    "status": "queued",
    "stage": "queued",
    "created_at": "2026-07-13T08:00:00Z",
    "poll_url": "/api/v1/integrations/ad-performance/analysis-jobs/ana_01JZZ7M3P4Q5R6S7T8V9W0X1Y2"
  }
}
```

### 5.4 幂等重试

相同 `external_request_id` 和相同规范化请求体：

```http
HTTP/1.1 200 OK
```

```json
{
  "code": 0,
  "message": "existing analysis job returned",
  "data": {
    "analysis_id": "ana_01JZZ7M3P4Q5R6S7T8V9W0X1Y2",
    "external_request_id": "ad-analysis-20260713-000001",
    "status": "processing",
    "stage": "searching_market_ads",
    "idempotent_replay": true,
    "created_at": "2026-07-13T08:00:00Z",
    "poll_url": "/api/v1/integrations/ad-performance/analysis-jobs/ana_01JZZ7M3P4Q5R6S7T8V9W0X1Y2"
  }
}
```

不得创建重复分析任务，不得重复下载素材或调用大模型。

### 5.5 幂等冲突

相同 `external_request_id` 但规范化请求体不同：

```http
HTTP/1.1 409 Conflict
```

```json
{
  "code": 4001,
  "message": "external_request_id already exists with a different payload",
  "data": {
    "error_code": "IDEMPOTENCY_CONFLICT",
    "external_request_id": "ad-analysis-20260713-000001",
    "analysis_id": "ana_01JZZ7M3P4Q5R6S7T8V9W0X1Y2"
  }
}
```

冲突响应返回原 `analysis_id` 仅用于定位问题，不代表服务器接受了新请求体。

### 5.6 请求规范化和摘要

系统保存原始请求 JSON，并生成用于幂等判断的 `payload_hash`：

1. `external_request_id` 单独作为唯一键，不进入摘要；
2. 通过请求模型完成类型校验和字段别名统一；
3. 去除可选字段中的 `null`，使“未提供”和“明确为 null”在协议允许时等价；
4. Decimal 类型使用无多余尾零的十进制字符串；
5. JSON 对象键按字典序排列；
6. `actions` 和 `cost_per_action_type` 按 `action_type + value` 稳定排序；
7. `siblings` 按 `facebook_ad_id`、`creative.fb_id` 或稳定回退摘要排序；
8. 其他数组保留调用方顺序；
9. 使用 UTF-8 编码的规范化 JSON 计算 SHA-256。

规范化算法必须有固定测试向量，防止以后重构导致同一请求生成不同摘要。

### 5.7 查询任务

```http
GET /api/v1/integrations/ad-performance/analysis-jobs/{analysis_id}
```

已知任务无论处于排队、处理中、成功还是业务失败状态，GET 均返回 HTTP 200，因为查询操作本身成功。

处理中：

```json
{
  "code": 0,
  "message": "analysis job retrieved",
  "data": {
    "analysis_id": "ana_01JZZ7M3P4Q5R6S7T8V9W0X1Y2",
    "external_request_id": "ad-analysis-20260713-000001",
    "status": "processing",
    "stage": "analyzing_reference_creatives",
    "progress": 78,
    "created_at": "2026-07-13T08:00:00Z",
    "started_at": "2026-07-13T08:00:02Z",
    "completed_at": null,
    "result": null,
    "error": null
  }
}
```

成功：

```json
{
  "code": 0,
  "message": "analysis job retrieved",
  "data": {
    "analysis_id": "ana_01JZZ7M3P4Q5R6S7T8V9W0X1Y2",
    "external_request_id": "ad-analysis-20260713-000001",
    "status": "succeeded",
    "stage": "completed",
    "progress": 100,
    "created_at": "2026-07-13T08:00:00Z",
    "started_at": "2026-07-13T08:00:02Z",
    "completed_at": "2026-07-13T08:01:18Z",
    "result": {
      "schema_version": "facebook_ad_analysis_v1"
    },
    "error": null
  }
}
```

业务失败：

```json
{
  "code": 0,
  "message": "analysis job retrieved",
  "data": {
    "analysis_id": "ana_01JZZ7M3P4Q5R6S7T8V9W0X1Y2",
    "external_request_id": "ad-analysis-20260713-000001",
    "status": "failed",
    "stage": "failed",
    "progress": 100,
    "result": null,
    "error": {
      "error_code": "ANALYSIS_UNAVAILABLE",
      "message": "No meaningful analysis could be produced",
      "retryable": false
    }
  }
}
```

未知 `analysis_id` 返回 HTTP 404 和 `code=4001`。

### 5.8 轮询建议

- 创建后第 1 分钟：每 5 秒查询一次；
- 1–5 分钟：每 10–15 秒查询一次；
- 超过 5 分钟：停止高频轮询，可改为每 30–60 秒；
- 调用方停止轮询不会取消服务端任务；
- v1 不提供取消接口。

## 6. 状态机

### 6.1 主状态

```text
queued
processing
succeeded
failed
```

主状态只表达任务生命周期。局部能力失败通过结果中的 `analysis_scope`、子模块状态和 warning 表达。

### 6.2 阶段

```text
queued
validating
downloading_media
inspecting_media
extracting_frames
analyzing_current_ad
building_search_queries
searching_market_ads
fetching_reference_ads
ranking_reference_ads
analyzing_reference_creatives
generating_recommendations
finalizing
completed
failed
```

图片广告跳过 `extracting_frames`。联网研究不可用时可跳过公开研究阶段，但需要在结果中解释。

### 6.3 建议进度映射

| 阶段 | 进度 |
| --- | ---: |
| `queued` | 0 |
| `validating` | 5 |
| `downloading_media` | 12 |
| `inspecting_media` | 20 |
| `extracting_frames` | 28 |
| `analyzing_current_ad` | 40 |
| `building_search_queries` | 50 |
| `searching_market_ads` | 58 |
| `fetching_reference_ads` | 66 |
| `ranking_reference_ads` | 72 |
| `analyzing_reference_creatives` | 80 |
| `generating_recommendations` | 90 |
| `finalizing` | 96 |
| `completed` / `failed` | 100 |

进度代表处理阶段，不代表精确剩余时间；同一阶段内不得倒退。

## 7. 数据持久化

### 7.1 权威数据源

PostgreSQL 保存：

- `analysis_id`；
- `external_request_id`；
- `payload_hash`；
- 原始请求；
- 规范化请求；
- 状态、阶段、进度；
- 当前尝试次数；
- 媒体处理摘要；
- 联网研究摘要和来源；
- 最终 `facebook_ad_analysis_v1` 结果；
- 错误信息；
- 创建、开始、完成和更新时间。

Redis/Celery 不作为任务查询真相源。

### 7.2 建议表边界

新增或扩展一张广告分析任务表，其逻辑字段包括：

```text
id
analysis_id                    unique
external_request_id            unique
payload_hash
request_payload_json
normalized_payload_json
status
stage
progress
analysis_scope
result_schema_version
result_json
error_code
error_message
error_retryable
attempt_count
max_attempts
generation_task_id
created_at
started_at
completed_at
updated_at
```

详细公开参考广告可以存入独立子表或 JSON。v1 推荐独立子表，以便去重、审计、缓存和后续复用：

```text
analysis_id
reference_id
source_type
source_url
source_domain
advertiser_name
ad_library_id
first_seen_at
last_seen_at
collected_at
content_hash
similarity_score
performance_evidence_json
creative_analysis_json
raw_excerpt_json
```

### 7.3 与 GenerationTask 的一致性

创建广告分析记录和内部 `GenerationTask` 必须在同一个数据库事务中完成。事务提交后再向 Celery 发布任务。

若数据库事务成功、Celery 发布失败：

- 分析记录保持 `queued`；
- API 不创建第二条记录；
- 记录调度错误；
- 由现有 stale-task 恢复机制或专用恢复任务重新入队。

恢复任务必须使用数据库锁或原子状态更新，避免多个恢复实例重复派发。

## 8. 媒体处理

### 8.1 输入职责

图片广告：

```text
creative.image_url
```

视频广告：

```text
creative.video_url
```

`thumbnail_url` 和 `video_keyframes` 是内部生成物，不属于新接口的调用方契约。

### 8.2 视频约束

- 常见时长：12 秒；
- 最大时长：20 秒；
- 最大文件大小：100 MB；
- 格式：MP4、MOV、WebM；
- 推荐：MP4/H.264。

服务端使用 `ffprobe` 检查容器、编解码器、时长和视频流，使用 `ffmpeg` 生成缩略图和关键帧。生产 Backend/Worker 镜像必须安装 `ffmpeg` 和 `ffprobe`。

### 8.3 视频抽帧

目标约 8 张代表帧，重点覆盖前 3 秒：

```text
0.2s
1.0s
2.0s
3.0s
40%
60%
80%
95%
```

对于短于这些固定时间点的视频，时间点钳制在有效范围内并去重。至少成功生成 3 张有效帧才执行视觉分析；否则降级为指标和文案分析。

v1 不分析音频、配音、背景音乐或完整运动连续性。结果必须明确视觉分析范围。

### 8.4 私有临时目录

```text
/data/ad-analysis-media/{analysis_id}/
```

该目录：

- 使用独立私有 volume；
- 不映射到 nginx 公共静态目录；
- 不直接返回给外部调用方；
- 在最终结果持久化后立即删除源媒体和帧；
- 由 Celery Beat 清理超过 2 小时的遗留目录。

清理失败只记录告警，不覆盖已经成功保存的分析结果。

### 8.5 媒体下载安全

媒体下载器必须：

- 仅接受 HTTPS；
- 使用可配置允许域名，初始至少允许 `newpixel.messrocts.com`；
- `trust_env=false`，不使用宿主机代理环境变量；
- 解析 DNS 后阻止 loopback、私网、链路本地、组播、保留地址和云元数据地址；
- 每次重定向重新校验 scheme、host、端口和解析地址；
- 限制重定向次数；
- 流式下载，并在超过大小限制时立即停止；
- 同时验证响应 MIME、文件头和实际媒体内容；
- 拒绝 HTML、登录页、错误页和空文件；
- 设置连接、读取和总耗时限制；
- 不把鉴权 Token 转发到素材域名。

### 8.6 媒体失败降级

媒体下载、探测或抽帧失败，但指标和文案足以生成有效分析时：

```text
任务 status = succeeded
analysis_scope = metrics_and_copy_only
media_analysis.status = unavailable
```

媒体失败不自动等于任务失败。

## 9. 当前广告成效分析

### 9.1 Meta 目标感知

系统必须先识别：

- `campaign.objective`；
- `adset.optimization_goal`；
- 请求是否明确包含真实业务目标；
- 当前可用的转化和价值字段。

不同目标使用不同核心指标：

| 场景 | 首要指标 |
| --- | --- |
| `OUTCOME_TRAFFIC` / `LINK_CLICKS` | 链接点击率、CPC、落地页到达率、单次落地页浏览成本 |
| `OUTCOME_SALES` / `OFFSITE_CONVERSIONS` | Purchase、CPA、购买价值、ROAS、漏斗转化 |
| Lead | Lead、Complete Registration、CPL、后续线索质量 |
| Video | 播放率、25/50/75/95/100% 留存及点击或转化衔接 |

Meta 配置内部对齐和真实业务目标对齐必须分开表达。例如
`OUTCOME_TRAFFIC + LINK_CLICKS` 在 Meta 配置层面是对齐的，但如果真实业务目标是购买，则业务目标层面的对齐情况可能未知或不匹配。

### 9.2 Facebook 漏斗

分析顺序：

1. Delivery：花费、曝光、覆盖、频次、CPM、投放稳定性；
2. Click：链接点击、链接点击率、CPC、点击吸引力；
3. Landing Page：落地页浏览、点击到落地页到达率、页面流失；
4. Conversion：加购、注册、线索、购买、CPA、价值和 ROAS；
5. Video：播放率和各阶段观看留存；
6. Creative：素材钩子、文案、画面、CTA、素材和落地页一致性；
7. Audience：受众范围、频次及可用分层数据；
8. Objective：优化目标和真实业务目标的匹配程度。

系统必须识别首要瓶颈，不能只罗列指标。

### 9.3 已有和新增派生指标

至少支持：

```text
landing_page_view_rate = landing_page_views / inline_link_clicks
video_play_rate = video_plays / impressions
video_p25_rate = video_p25_views / video_plays
video_p50_rate = video_p50_views / video_plays
video_p75_rate = video_p75_views / video_plays
video_p95_rate = video_p95_views / video_plays
video_p100_rate = video_p100_views / video_plays
conversion_actions
sibling_metrics
```

分母为零或缺失时返回 `null` 和明确原因，不返回无限值或伪造的 0。

### 9.4 缺失不等于零

以下两种情况必须区分：

```json
{"purchase": {"value": 0, "source": "meta", "assessment": "zero"}}
```

表示请求明确提供购买数为 0。

```json
{"purchase": {"value": null, "source": "missing", "assessment": "unavailable"}}
```

表示请求没有提供购买数据。系统不得把缺失解释为零转化。

金额指标缺少 `currency` 时统一使用 `account_currency`，不得擅自写成 USD。缺少购买价值或 ROAS 时不得判断广告是否盈利。

## 10. 公开联网研究

### 10.1 核心定位

联网研究用于：

- 发现公开相似 Facebook/Meta 广告；
- 总结市场创意趋势和常见表达；
- 分析竞品公开素材、文案、CTA 和落地页；
- 提供有来源的创意参考；
- 识别公开表现代理信号。

联网研究不用于伪造其他广告主的真实私有成效。

### 10.2 第一版研究来源

公开联网研究按优先级使用：

1. Meta Ad Library 可公开访问的广告资料；
2. 广告主公开 Facebook 页面、品牌站点和公开落地页；
3. 普通公开网络搜索结果；
4. 应用商店、游戏官网和公开产品介绍页。

本系统已保存且允许比较的历史广告属于内部历史基准，不是公开联网来源；
`siblings` 属于调用方提交的真实同组数据，也不归入联网研究来源。两者都在
`benchmark_comparison` 中单独表达，不能混入 `market_intelligence.sources`。

Meta 官方公开接口和页面对普通商业广告可能存在地区、类别、权限、登录和字段限制。因此系统不得依赖单一 Meta 页面或非公开接口。v1 使用可替换的 `PublicResearchProvider` 边界：

```text
search(query, locale, country) -> SearchHit[]
fetch_public_page(url) -> PublicPageSnapshot
normalize_candidate(snapshot) -> PublicAdCandidate
```

生产环境通过服务端配置选择具备公开网络搜索能力的 provider。该 provider 只负责发现公开 URL；页面下载、SSRF 防护、内容大小限制、去重、证据分级和结果规范化由本系统负责。v1 不接入付费广告情报数据库，也不调用非公开、逆向或绕过访问控制的 Meta 接口。

如果部署环境未配置可用的公开搜索 provider，当前广告分析仍成功，`market_intelligence.status` 返回 `unavailable`。

### 10.3 自动研究画像

调用方不传 `research_context`。系统从以下信息自动生成 `research_profile`：

1. `campaign`、`adset` 和 `creative` 的结构化字段；
2. 广告正文、描述、CTA 和名称；
3. 图片内容或视频关键帧；
4. `creative.link` 指向的公开落地页；
5. `siblings` 中反复出现的产品词、卖点和创意方向。

画像至少尝试生成：

```text
product_name
product_category
country
language
creative_type
campaign_objective
optimization_goal
message_angles
user_motivations
visual_patterns
landing_page_goal
```

每个推断字段带置信度。无法识别具体品类时退化到较宽分类，并在结果中返回 warning。

### 10.4 搜索策略

系统生成 8–12 组查询，覆盖：

- 精确产品或品牌；
- 产品品类；
- 创意角度；
- 用户动机；
- 相似产品和可能竞品；
- 国家和语言限定；
- Facebook/Meta 广告意图词。

默认上限：

```text
联网研究总超时：90 秒
搜索候选：最多 30 条
去重后高相关候选：最多 10 条
深度分析参考广告：最多 5 条
单页面抓取超时：10 秒
搜索重试：最多 2 次
```

所有上限由服务端配置控制，不由请求体覆盖。

### 10.5 候选规范化

统一候选结构：

```json
{
  "reference_id": "ref_001",
  "source_type": "meta_ad_library",
  "source_url": "https://www.facebook.com/ads/library/?id=1234567890",
  "source_domain": "facebook.com",
  "advertiser_name": "Example Studio",
  "ad_library_id": "1234567890",
  "first_seen_at": null,
  "last_seen_at": null,
  "is_active": null,
  "countries": ["US"],
  "languages": ["en"],
  "creative_type": "video",
  "primary_text": "Can you survive the next wave?",
  "headline": "Beat the survival record",
  "cta": "PLAY_NOW",
  "landing_page_url": "https://example.com/game",
  "image_urls": [],
  "video_url": null,
  "collected_at": "2026-07-13T08:00:40Z"
}
```

无法从公开页面确认的字段必须为 `null`，不得猜测。

### 10.6 去重和相似度

候选通过以下信息去重：

- 规范化来源 URL；
- Meta Ad Library ID；
- 广告主和正文摘要；
- 素材感知哈希或内容哈希；
- 落地页规范化 URL。

相似度考虑：

```text
产品或行业
目标国家
语言
素材类型
Campaign Objective
用户诉求
创意钩子
CTA
落地页目标
```

系统保存总分和维度分，不只保存大模型文字判断。相似度低于配置阈值的候选不能进入最终参考广告列表。

### 10.7 表现证据分级

固定证据类型：

```text
submitted_performance
historical_internal_performance
verified_performance
third_party_estimate
public_proxy_signals
unknown
```

v1 公开联网候选通常只能使用 `public_proxy_signals` 或 `unknown`；`third_party_estimate` 保留给未来版本，v1 不产生该类型。

允许的公开代理信号包括：

```text
currently_active
long_running
multiple_creative_variants
cross_region_reuse
repeated_message_angle
consistent_landing_page
```

代理信号必须带可观察证据和采集时间。例如：

```json
{
  "type": "public_proxy_signals",
  "verified": false,
  "confidence": "medium",
  "signals": [
    {
      "type": "multiple_creative_variants",
      "observed_value": 6,
      "explanation": "公开页面中发现同一卖点的6个素材版本"
    }
  ],
  "limitations": [
    "真实CTR不可用",
    "真实CPA不可用",
    "真实购买量和ROAS不可用"
  ]
}
```

长期活跃或素材变体较多只能说明值得研究，不能证明盈利或成效优秀。

### 10.8 公开页面抓取安全和合规

公开网页抓取器必须：

- 只访问 HTTP/HTTPS，正式来源优先 HTTPS；
- 阻止本机、内网、链路本地、元数据和非公网地址；
- 校验每次重定向；
- 限制页面和媒体大小；
- 不提交登录表单；
- 不保存或使用用户浏览器 Cookie；
- 不绕过验证码、付费墙或访问控制；
- 不调用通过逆向发现的非公开接口；
- 遵守部署方批准的来源白名单、访问频率和使用条款；
- 只保存分析所需的最小公开内容、摘要和来源；
- 在返回结果中保留来源 URL 和采集时间。

### 10.9 联网研究降级

| 情况 | 任务结果 |
| --- | --- |
| 当前分析成功，联网成功 | `succeeded`, `current_ad_and_public_market_research` |
| 当前分析成功，候选不足 | `succeeded`, `current_ad_and_limited_market_research` |
| 当前分析成功，联网失败 | `succeeded`, `current_ad_only` |
| 当前数据不足，联网成功 | 可提供创意趋势，但不得判断真实成效 |
| 当前数据不足，联网失败 | 仅在无法产生任何有意义结果时 `failed` |

## 11. 大模型和规则引擎边界

### 11.1 先计算，后推理

规则引擎负责：

- 解析 Meta actions；
- 计算漏斗指标；
- 判断缺失字段和数据质量；
- 应用确定性的阈值规则；
- 建立 sibling 统计；
- 标记公开来源和证据类型；
- 校验最终 JSON。

大模型负责：

- 文案和视觉语义分析；
- 产品、品类和创意角度识别；
- 公开参考广告的创意模式总结；
- 在规则和证据范围内确定首要瓶颈；
- 生成具体、可验证的优化动作和实验计划。

大模型不得自行重新计算已有确定性指标，也不得覆盖来源事实。

### 11.2 分析模式

```text
rules_and_llm
rules_only
```

若 LLM 失败但规则仍可产生有意义结论：

```text
status = succeeded
analysis_scope = rules_only
analysis_metadata.analysis_mode = rules_only
```

只有规则、媒体和 LLM 均无法产生有意义分析时，任务才失败。

### 11.3 系统提示词核心任务

```text
你是一名资深的Facebook/Meta广告投放分析专家。

你的核心任务是：

分析外部系统提交的Facebook广告真实投放成效。系统会根据当前广告的
Campaign、Ad Set、Creative、Insights、广告素材和公开落地页自动识别产品、
行业、国家、语言、创意角度及搜索关键词，并通过公开网络实时采集相似广告和
市场创意信息。

你需要筛选与当前广告具有较高可比性、并具有较强公开表现信号的参考广告，
结合当前广告的投放漏斗、素材、文案、受众、落地页和投放目标，识别最主要的
问题，并给出有数据证据、明确优先级、可以实际执行且能够验证效果的优化建议。
```

### 11.4 系统提示词硬规则

提示词必须明确：

1. 当前广告真实成效只能来自请求 `insight` 和确定性计算；
2. 联网公开广告主要用于市场研究、创意参考、趋势和公开代理信号；
3. 不得为公开广告编造或推测 CTR、CPC、CPA、购买量、收入或 ROAS；
4. 缺失转化不等于零转化；
5. 缺少币种时不得擅自判断货币；
6. 缺少价值或 ROAS 时不得判断盈利；
7. 每条重要结论必须引用输入指标、公式、素材证据或公开来源；
8. 同组广告样本不足时返回 `insufficient_data`，不得强行排名；
9. 公开参考广告证据不足时返回 `insufficient_results`；
10. 数据不足时可以指出风险，但不得直接建议大幅放量或立即暂停；
11. 严格输出 `facebook_ad_analysis_v1` JSON，不输出 Markdown 或额外说明；
12. 不在多个字段中重复大段相同内容。

## 12. 最终结果协议：facebook_ad_analysis_v1

### 12.1 设计原则

结果采用混合结构：

- 顶部给投手可直接执行的结论；
- 中部按 Meta 漏斗组织指标和诊断；
- 后部提供公开市场研究、推荐动作和实验计划；
- 严格区分 Meta 原始值、系统计算值和 AI 推断；
- 不保留内容重复的大型 `ai_analysis` 对象。

### 12.2 顶层结构

```json
{
  "schema_version": "facebook_ad_analysis_v1",
  "platform": "facebook",
  "executive_summary": {},
  "objective_alignment": {},
  "performance_funnel": {},
  "diagnoses": [],
  "creative_analysis": {},
  "audience_and_delivery_analysis": {},
  "market_intelligence": {},
  "benchmark_comparison": {},
  "recommended_actions": [],
  "experiment_plan": [],
  "data_quality": {},
  "analysis_metadata": {}
}
```

### 12.3 操作结论枚举

`executive_summary.verdict`：

```text
scale
keep
watch
optimize
pause
insufficient_data
```

首要瓶颈：

```text
delivery
creative_hook
click_quality
landing_page
conversion
audience
objective_mismatch
creative_fatigue
insufficient_data
```

结论必须同时返回：

```text
verdict
priority
primary_bottleneck
confidence
scale_eligibility
pause_recommended
key_findings
```

### 12.4 指标来源

Meta 原始值：

```json
{
  "value": 7.970343,
  "unit": "percent",
  "source": "meta",
  "assessment": "strong"
}
```

系统计算值：

```json
{
  "value": 26.744186,
  "unit": "percent",
  "source": "calculated",
  "formula": "landing_page_views / inline_link_clicks * 100",
  "assessment": "critical"
}
```

AI 推断：

```json
{
  "conclusion": "点击到落地页之间存在明显流失",
  "source": "inferred",
  "confidence": "high",
  "evidence": [
    "inline_link_clicks=86",
    "landing_page_views=23",
    "landing_page_view_rate=26.74%"
  ]
}
```

### 12.5 漏斗结构

```json
{
  "performance_funnel": {
    "delivery": {"status": "normal", "metrics": {}, "conclusion": "当前频次未显示明显疲劳"},
    "click": {"status": "strong", "metrics": {}, "conclusion": "链接点击吸引力较强"},
    "landing_page": {"status": "critical", "metrics": {}, "conclusion": "点击后的落地页到达损耗明显"},
    "conversion": {"status": "unavailable", "metrics": {}, "conclusion": "缺少转化价值数据"},
    "video": {"status": "not_applicable", "metrics": {}, "conclusion": null}
  }
}
```

### 12.6 诊断结构

```json
{
  "diagnosis_id": "landing_page_dropoff",
  "category": "landing_page",
  "severity": "high",
  "confidence": "high",
  "title": "点击到落地页之间存在明显流失",
  "conclusion": "86次链接点击只形成23次落地页浏览。",
  "evidence": [
    {"metric": "inline_link_clicks", "value": 86, "source": "meta"},
    {"metric": "landing_page_views", "value": 23, "source": "meta_actions"},
    {"metric": "landing_page_view_rate", "value": 26.744186, "source": "calculated"}
  ],
  "possible_causes": [
    "落地页加载速度过慢",
    "跳转链路不稳定",
    "Pixel事件回传异常"
  ]
}
```

`possible_causes` 必须保持为待验证原因，不能写成已确认事实。

### 12.7 公开市场研究结构

```json
{
  "market_intelligence": {
    "status": "succeeded",
    "research_profile": {
      "generation_method": "automatically_inferred",
      "product_name": "Slash the Hordes",
      "product_category": "horde survival game",
      "country": "US",
      "language": "en",
      "creative_type": "image",
      "message_angles": [
        "survival challenge",
        "beat my record",
        "increasing difficulty"
      ],
      "confidence": "high",
      "warnings": []
    },
    "search_summary": {
      "query_count": 9,
      "candidate_count": 26,
      "selected_reference_count": 5
    },
    "sources": [
      {
        "source_type": "meta_ad_library",
        "status": "succeeded",
        "candidate_count": 18
      },
      {
        "source_type": "web_search",
        "status": "succeeded",
        "candidate_count": 8
      }
    ],
    "selected_reference_ads": [
      {
        "reference_id": "ref_001",
        "source_type": "meta_ad_library",
        "source_url": "https://www.facebook.com/ads/library/?id=1234567890",
        "advertiser_name": "Example Studio",
        "collected_at": "2026-07-13T08:00:40Z",
        "similarity_score": 0.87,
        "performance_evidence": {
          "type": "public_proxy_signals",
          "verified": false,
          "confidence": "medium",
          "signals": ["currently_active", "multiple_creative_variants"],
          "limitations": [
            "真实CTR不可用",
            "真实CPA和ROAS不可用"
          ]
        },
        "creative_patterns": {
          "hook": "首秒展示生存压力和挑战目标",
          "message_angle": "挑战玩家突破纪录",
          "visual_structure": [
            "快速展示敌群",
            "展示升级或武器变化",
            "结尾引导立即试玩"
          ],
          "cta": "PLAY_NOW"
        },
        "applicable_learnings": [
          "把三分钟挑战直接视觉化",
          "测试PLAY_NOW与LEARN_MORE的差异"
        ]
      }
    ],
    "summary": "公开相似广告普遍在首段快速呈现敌群压力、计时器和失败风险。"
  }
}
```

联网状态枚举：

```text
succeeded
insufficient_results
unavailable
skipped
```

### 12.8 比较结构

```json
{
  "benchmark_comparison": {
    "submitted_siblings": {
      "status": "insufficient_data",
      "count": 1,
      "comparison_confidence": "low"
    },
    "historical_high_performers": {
      "status": "no_match",
      "count": 0
    },
    "web_researched_ads": {
      "status": "available",
      "candidate_count": 26,
      "selected_count": 5,
      "performance_is_verified": false,
      "comparison_confidence": "medium"
    }
  }
}
```

三类比较证据不得混为一谈：

- `submitted_siblings`：调用方提供，可能含真实 Meta 成效；
- `historical_high_performers`：系统内部历史数据，可能含已验证真实指标；
- `web_researched_ads`：公开联网研究，通常只有代理信号。

### 12.9 推荐动作结构

```json
{
  "action_id": "check_landing_page_speed",
  "priority": 1,
  "category": "landing_page",
  "action": "检查美国地区移动设备访问时的首屏加载速度和成功率。",
  "reason": "点击到落地页浏览的到达率只有约26.74%。",
  "evidence": [
    "inline_link_clicks=86",
    "landing_page_views=23"
  ],
  "expected_impact": "减少点击后的流失，提高有效落地页访问量。",
  "success_metric": "landing_page_view_rate",
  "target_direction": "increase",
  "success_criteria": null,
  "owner": "landing_page_team"
}
```

禁止只输出“优化素材”“调整受众”“提高转化”等无法执行的空泛建议。

### 12.10 实验计划结构

```json
{
  "experiment_id": "landing_page_health_check",
  "name": "落地页链路验证",
  "priority": "high",
  "hypothesis": "低到达率主要由加载、重定向或事件追踪问题造成。",
  "changes": [
    "使用目标地区移动节点测试页面加载",
    "检查Facebook内置浏览器兼容性",
    "核对Pixel事件触发"
  ],
  "primary_metric": "landing_page_view_rate",
  "secondary_metrics": ["cost_per_landing_page_view"],
  "success_criteria": {
    "landing_page_view_rate": {
      "operator": ">=",
      "value": 70,
      "unit": "percent"
    }
  },
  "decision_rule": "修复后到达率明显提升再考虑增加预算。"
}
```

固定成功阈值只能在规则有依据时产生；否则使用方向性标准并明确需要基线，避免模型随意编造行业基准。

### 12.11 数据质量

`data_quality` 至少包含：

```text
overall_status
score
sample_size
available_fields
missing_or_recommended_fields
warnings
```

建议优先支持以下 Meta 字段：

```text
currency
attribution_setting
action_values
purchase_value
website_purchase_roas
cost_per_action_type
publisher_platform breakdown
platform_position breakdown
device_platform breakdown
age breakdown
gender breakdown
country breakdown
```

这些字段是增强分析质量的建议，不作为 v1 创建任务的强制字段。

### 12.12 分析元数据

```json
{
  "analysis_metadata": {
    "analysis_mode": "rules_and_llm",
    "analysis_scope": "current_ad_and_public_market_research",
    "media_analysis": {
      "status": "available",
      "source_field": "creative.image_url",
      "thumbnail_generated_internally": true,
      "video_keyframes_generated_internally": false,
      "keyframe_count": 0,
      "error": null
    },
    "public_research": {
      "status": "succeeded",
      "provider": "configured_public_search_provider",
      "started_at": "2026-07-13T08:00:30Z",
      "completed_at": "2026-07-13T08:01:00Z"
    },
    "rule_engine_version": "facebook_rules_v1",
    "prompt_version": "facebook_analysis_prompt_v1",
    "model": "configured-model",
    "generated_at": "2026-07-13T08:01:18Z",
    "warnings": [],
    "llm_error": null
  }
}
```

## 13. 错误处理和重试

### 13.1 局部重试

媒体下载：

- 瞬时网络错误最多 3 次；
- 4xx、域名不允许、文件过大、类型错误不重试；
- 使用指数退避和抖动。

FFmpeg：

- 允许 1 次重试；
- 至少 3 张有效帧即可继续视觉分析；
- 否则降级。

联网搜索：

- 瞬时网络错误、429 和临时 5xx 最多 2 次；
- 总时长不能超过联网研究预算；
- 失败后降级为当前广告分析。

大模型：

- timeout、429 和临时 5xx 最多 3 次；
- JSON 格式错误允许一次带验证错误的修复请求；
- 仍失败时尝试 `rules_only`。

### 13.2 任务级重试

```text
max_attempts = 2
stale_timeout ≈ 10 分钟
```

任务级重试只用于可重试的基础设施或工作进程故障。必须复用同一 `analysis_id`、同一请求记录和同一 `external_request_id`，不得创建新的逻辑任务。

### 13.3 任务失败条件

只有无法形成任何有意义结果时才返回任务级 `failed`，例如：

- 请求通过了 API 初始校验，但后续发现核心结构不可解析；
- 指标、文案和素材均不可用；
- 规则引擎和大模型都无法生成最低限度的分析；
- 持久化最终结果失败且重试耗尽。

以下情况单独出现时不应导致任务失败：

- 媒体下载失败；
- 视频抽帧不足；
- 落地页不可访问；
- Meta Ad Library 不可访问；
- 公开搜索 provider 不可用；
- 没有找到足够相似的公开广告；
- LLM 失败但规则结果有效。

## 14. 并发、资源和成本控制

目标流量：

```text
常态：1–10 次提交/分钟
峰值：最高 30 次提交/分钟
```

初始建议：

```text
媒体下载并发：4
FFmpeg并发：2
LLM并发：3–5
公开联网研究并发：3
```

控制原则：

- Worker 预取数量保持较低，避免一个 worker 囤积长任务；
- FFmpeg 使用进程级信号量；
- LLM 和联网搜索分别设置并发闸门；
- 单任务公开研究严格限制候选数、页面数、媒体数和总时间；
- 对相同规范化公开 URL 和内容哈希使用短期缓存；
- 缓存不得把旧公开状态描述为实时状态，必须返回 `collected_at`；
- 不缓存当前广告的私有成效结论来替代新分析。

## 15. 可观测性

结构化日志至少包含：

```text
analysis_id
external_request_id
generation_task_id
status
stage
attempt
media_host
media_bytes
media_duration
keyframe_count
search_provider
search_query_count
candidate_count
selected_reference_count
llm_provider
llm_model
latency_ms
error_code
```

不得记录：

- Bearer Token；
- 完整 API Key；
- 数据库密码；
- 大段原始网页正文；
- 不必要的个人信息；
- 可用于绕过访问控制的 Cookie 或请求头。

建议指标：

```text
ad_analysis_jobs_total{status}
ad_analysis_stage_duration_seconds{stage}
ad_analysis_media_failures_total{reason}
ad_analysis_public_research_total{status}
ad_analysis_reference_candidates_total
ad_analysis_llm_failures_total{reason}
ad_analysis_degraded_total{scope}
ad_analysis_queue_depth
```

## 16. 配置

新增配置均通过环境变量注入，真实值不得进入仓库。建议配置分组：

```text
AD_ANALYSIS_QUEUE
AD_ANALYSIS_MAX_ATTEMPTS
AD_ANALYSIS_STALE_TIMEOUT_SECONDS
AD_ANALYSIS_MEDIA_ROOT
AD_ANALYSIS_MEDIA_ALLOWED_HOSTS
AD_ANALYSIS_VIDEO_MAX_BYTES
AD_ANALYSIS_VIDEO_MAX_DURATION_SECONDS
AD_ANALYSIS_DOWNLOAD_CONCURRENCY
AD_ANALYSIS_FFMPEG_CONCURRENCY
AD_ANALYSIS_LLM_CONCURRENCY
PUBLIC_RESEARCH_ENABLED
PUBLIC_RESEARCH_PROVIDER
PUBLIC_RESEARCH_TOTAL_TIMEOUT_SECONDS
PUBLIC_RESEARCH_PAGE_TIMEOUT_SECONDS
PUBLIC_RESEARCH_MAX_QUERIES
PUBLIC_RESEARCH_MAX_CANDIDATES
PUBLIC_RESEARCH_MAX_SELECTED
PUBLIC_RESEARCH_ALLOWED_DOMAINS
PUBLIC_RESEARCH_BLOCKED_DOMAINS
```

默认值必须与本设计的 v1 上限一致。生产环境若未配置公开研究 provider，应明确降级，而不是阻止 Worker 启动。

## 17. 测试策略

### 17.1 请求和幂等单元测试

- 新 `external_request_id` 创建一次任务；
- 相同 ID、相同规范化请求返回原任务；
- 相同 ID、不同请求返回 409；
- 对象键顺序变化不影响摘要；
- actions 和 siblings 顺序变化不影响摘要；
- 数字等价形式按规范化规则生成同一摘要；
- 缺少图片或视频 URL 返回 422/4001；
- 新接口忽略调用方 `thumbnail_url` 和 `video_keyframes`；
- 未知扩展字段按协议保留。

### 17.2 状态和恢复测试

- 数据库和 GenerationTask 同事务创建；
- Celery 发布失败后记录保持 queued；
- stale 恢复只重新派发一次；
- Worker 重启后可以继续或安全重跑；
- 所有状态转换合法且进度不倒退；
- 已完成结果不会被旧重试覆盖。

### 17.3 媒体测试

- 合法 HTTPS 图片；
- 合法 MP4/H.264、MOV、WebM；
- 12 秒常规视频和 20 秒边界视频；
- 超过 20 秒、超过 100 MB；
- HTML 伪装媒体；
- 重定向到内网；
- DNS 解析到私网；
- 无视频流或损坏文件；
- 只生成 0、2、3、8 张有效帧时的降级边界；
- 临时目录最终清理。

### 17.4 Facebook 指标测试

- Traffic/Link Clicks；
- Sales/Offsite Conversions；
- Lead；
- 图片广告和视频广告；
- 缺失 purchase 与明确 purchase=0；
- 缺失 currency；
- 分母为零；
- clicks 与 inline_link_clicks 不同；
- sibling 样本不足；
- 高频次和创意疲劳；
- 高 CTR、低 landing-page-view rate；
- 目标配置内部对齐但业务目标未知。

### 17.5 公开联网研究测试

使用确定性 fake provider 和录制的公开页面快照，覆盖：

- 自动研究画像生成；
- 多查询生成；
- 候选去重；
- 相似度排序；
- 最多 5 条深度分析；
- 来源 URL 和采集时间保留；
- 私有地址和危险重定向拦截；
- 页面超时、429、5xx；
- Meta 来源不可用但普通公开搜索可用；
- 所有公开来源不可用时降级；
- 候选很多但无高相似结果；
- 禁止公开广告出现伪造的 CTR、CPA 和 ROAS；
- 公开代理信号不会被标记为 verified。

### 17.6 LLM 输出测试

- 输出严格通过 `facebook_ad_analysis_v1` schema；
- JSON 外没有 Markdown；
- 重要结论包含 evidence；
- 缺失字段不被补成 0；
- `possible_causes` 不被写成确认事实；
- 推荐动作有 priority、action、reason、evidence 和 success metric；
- 小样本不建议直接大幅放量或暂停；
- LLM JSON 修复失败后正确降级到 rules-only。

### 17.7 集成和生产形态验证

- PostgreSQL、Redis、Backend 和 `worker_ad_analysis` 联调；
- 本地 `docker-compose.prod.yml --env-file .env.production` 构建；
- Worker 镜像中 `ffmpeg` 和 `ffprobe` 可执行；
- nginx 后的新 POST/GET 路由可用；
- Bearer Token 鉴权正确；
- 现有同步 `/analyses` 行为不变；
- 真实样例图片和 12 秒视频完成全流程；
- 公开研究不可用时仍产生当前广告结果；
- GET 返回持久化结果而非依赖 Celery backend。

## 18. 部署和兼容策略

### 18.1 兼容性

- 不修改现有同步接口路径和请求语义；
- 新异步接口独立上线；
- 外部系统完成迁移后，再单独决定同步接口是否进入弃用周期；
- `facebook_ad_analysis_v1` 一旦上线，新增可选字段必须向后兼容；
- 删除字段、修改含义或枚举需要新的 schema version。

### 18.2 上线顺序

1. 数据库迁移和结果 schema；
2. 请求校验、幂等和 GET 查询；
3. 独立 Celery 队列和 Worker；
4. 媒体下载、FFmpeg 和降级；
5. Facebook 规则和结构化 LLM 输出；
6. 公开研究 provider、抓取、去重和证据分级；
7. 本地生产形态 Docker 验证；
8. 测试服务器部署；
9. 使用真实外部样例进行端到端验收；
10. 向外部系统发布最终对接文档和示例。

### 18.3 功能开关

联网研究由服务端 `PUBLIC_RESEARCH_ENABLED` 控制：

- 开启且 provider 可用：执行公开研究；
- 开启但 provider 不可用：降级并返回 warning；
- 关闭：`market_intelligence.status=skipped`。

关闭联网研究不改变外部请求协议。

## 19. 验收标准

满足以下条件即认为 v1 实施完成：

1. 外部系统可用一个 JSON 创建异步任务，并在 202 响应中获得 `analysis_id`；
2. 相同 `external_request_id` 和相同请求不会重复分析；
3. 相同 `external_request_id` 和不同请求返回 409；
4. 外部系统只通过 `analysis_id` 查询状态和最终结果；
5. 图片和视频无需调用方提供缩略图或关键帧；
6. 20 秒、100 MB 以内的受支持视频可以完成校验和抽帧；
7. 媒体失败时可以降级为指标和文案分析；
8. 系统自动识别公开研究画像，不要求调用方提供搜索参数；
9. 系统能够通过配置的公开搜索 provider 发现公开相似广告；
10. 每条联网参考广告带来源、采集时间、相似度、证据类型、置信度和限制；
11. 公开广告不会出现伪造的 CTR、CPA、购买量或 ROAS；
12. 联网研究失败不会阻断当前广告的有效分析；
13. 最终结果严格符合 `facebook_ad_analysis_v1`；
14. 结果顶部给出可执行 verdict、首要瓶颈、置信度、放量条件和暂停建议；
15. 结果按 Facebook 漏斗组织，并区分 Meta、calculated、inferred 和公开代理证据；
16. 现有同步接口和其他 Celery 队列无回归；
17. 本地生产形态 Docker 和测试服务器健康检查通过。

## 20. 示例数据的预期判断

针对当前样例：

```text
campaign.objective = OUTCOME_TRAFFIC
adset.optimization_goal = LINK_CLICKS
inline_link_clicks = 86
landing_page_views = 23
landing_page_view_rate ≈ 26.74%
spend = 0.24 account_currency
```

预期顶部结论：

```text
verdict = optimize
primary_bottleneck = landing_page
confidence = medium
scale_eligibility = not_ready
pause_recommended = false
```

理由：

- 链接点击率较强；
- 点击到落地页之间存在明显损耗；
- Meta Traffic 和 Link Clicks 在配置层面对齐；
- 真实业务目标未知；
- 缺少购买、价值和 ROAS，不能判断盈利；
- 当前花费和样本较小，不应直接暂停或放量；
- sibling 只有 19 次曝光，不足以作为可靠高表现对照；
- 联网公开广告只能提供创意和代理信号，不改变以上真实成效判断。

该示例是规则和协议验收基准，实施后的结果措辞可以不同，但数据事实、证据边界和操作方向不得相反。
