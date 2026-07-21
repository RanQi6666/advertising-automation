# 广告研究引擎设计

**日期：** 2026-07-21
**状态：** 已确认设计，待实施计划
**首期验收场景：** `country=IN`、`category=gambling`、`target_count=25`

## 1. 目标与边界

系统是通用的公开广告研究引擎，而不是针对单一国家、业务类别或关键词的采集脚本。外部调用方只提交 `external_user_id`、国家、类别、可选关键词和目标数量。系统自动规划检索、召回公开广告、做媒体技术校验、进行多模态分类与排序、按缺口补采，并提供最终结果给外部系统轮询。

系统只分析公开可见广告资料、公开媒体与公开字段。系统不模拟审核员或真实用户、不按 IP/设备指纹探测落地页、不复现或确认 Cloaking，不维护规避策略。风险相关输出只能是公开可见的不一致或疑似风险信号。

## 2. 职责划分

### 2.1 大模型职责

`gpt-5.4-mini` 负责：

- 理解自由文本 `category` 与国家语境；
- 根据可选种子词、历史召回、噪声和缺口规划每轮检索 query；
- 判断候选广告的类别匹配度、业务类型、创意相关性和明显无关性；
- 阅读广告文本、CTA、封面和视频代表帧，输出文字与视觉证据；
- 依据实际可见字段生成 `public_performance_signal_score`；
- 对临界候选复核、决定补采方向、产生排序建议。

默认调用使用 `reasoning.effort=none`；仅低置信度、证据冲突或最终排名边缘候选可使用 `reasoning.effort=medium`。模型不能虚构成本、CPA、ROAS、真实转化或未采集到的投放字段。

### 2.2 后台职责

后台不固化博彩或各国家的业务词典。后台只处理通用、可验证的技术事实和工作流：任务生命周期、权限、`external_user_id` 幂等、country 参数透传、广告 ID 去重、媒体可访问性、视频时长、ACTIVE 和投放天数校验、重试、轮询、清理和上限控制。

首期短视频研究的硬条件：

- 广告 ACTIVE；
- 存在可用视频；
- 视频时长小于或等于 30 秒；
- 投放时长大于 2 天；
- 广告 ID 去重；
- 最终广告可提供视频来源或可用预览资料。

这些是首期研究产品的媒体与数据质量约束，而非博彩类别判断规则。

## 3. 外部接口

### 3.1 创建任务

`POST /api/v1/integrations/ad-research/jobs`

```json
{
  "external_user_id": "external-task-20260721-001",
  "country": "IN",
  "category": "gambling",
  "keywords": ["casino", "rummy"],
  "target_count": 25
}
```

返回 `202 Accepted` 和内部生成的 `task_id`、`status=queued`、轮询地址及建议轮询间隔。

`external_user_id` 是本接口的幂等键。它必须是外部系统为**一次逻辑广告研究请求**生成的唯一标识；它不能是长期不变的普通用户账号 ID。相同 `external_user_id` 与相同规范化请求体返回同一任务；相同标识但请求体不同返回 `409 external_user_id_payload_conflict`。任务结果过期并清理后，可释放该幂等占用，以便外部系统再次使用该标识。

### 3.2 轮询任务

`GET /api/v1/integrations/ad-research/jobs/{task_id}`

处理中返回状态、阶段、轮次和汇总进度。完成后返回最终结果、研究汇总和 `result_expires_at`。结果可在 24 小时有效窗口内重复读取，不需要 callback，也不需要 acknowledge。

过期后返回 `410 Gone` 与 `status=expired`，且不返回已经清理的广告内容。

## 4. 异步架构

```text
API
→ PostgreSQL 建立 AdResearchJob 和通用 GenerationTask
→ Redis Broker 投递到 ad_research_queue
→ worker_ad_research 执行研究编排
→ PostgreSQL 写入临时进度和最终结果
→ 外部系统轮询读取
→ Celery Beat 投递过期清理任务
```

新增独立 `ad_research_queue` 和 `worker_ad_research`，不占用普通文本、图片或视频生成队列。

配置：

```env
AD_RESEARCH_WORKER_CONCURRENCY=2
AD_RESEARCH_MODEL_CONCURRENCY=6
```

前者表示最多两个完整研究任务同时运行。后者表示跨全部研究 Worker 的全局模型并发上限为六，必须通过 Redis 分布式限流实现，不能按每个 Worker 分别放大为六。

## 5. 状态机

```text
queued
→ collecting
→ media_qualifying
→ model_scoring
→ supplementing
→ completed
```

终态：

```text
insufficient
failed
expired
```

`insufficient` 表示已经达到最大补采轮数或最大采集量，仍没有足够的技术合格且模型相关广告；不得为凑数降低硬条件。

## 6. 采集与筛选流程

1. 模型读取目标、种子词、已用 query、当前缺口和上一轮统计，返回 6 至 12 个受控查询。
2. `AdSourceAdapter` 调用第一期采集实现 `athm793/meta-ads-scraper`，将公开广告字段标准化。采集器实现必须可替换，不能进入业务逻辑。
3. 后台按广告 ID 去重，并执行 ACTIVE、视频、时长、投放天数和媒体可用性校验。
4. 模型先使用文本、CTA、主页、公开日期和封面进行批量初筛；明显无关候选不下载或不分析更多视频帧。
5. 对高潜力、边界以及最终 Top 候选提取视频代表帧，进行多模态分类与证据确认。
6. 按模型输出和客观可见字段排序，保留符合条件的 Top 25。
7. 若不足目标数量，模型复盘噪声与缺口，生成下一轮查询；达到上限后返回 `insufficient`。

## 7. 模型契约与排序

每个合格候选的模型输出必须为固定 JSON，至少包括：

```json
{
  "category_match": true,
  "category_confidence": 0.91,
  "business_type": "normalized_business_type",
  "creative_relevance_score": 88,
  "public_performance_signal_score": 72,
  "real_money_signal_score": 0.84,
  "is_obviously_unrelated": false,
  "text_evidence": [],
  "visual_evidence": [],
  "public_signal_evidence": [],
  "public_risk_signals": [],
  "recommendation": "keep"
}
```

`public_performance_signal_score` 只代表公开可见的持续投放代理信号，不代表真实花费、成本、CPA、ROAS 或盈利。若公开资料不足，模型必须标记资料不足。

排序顺序：先确保技术硬条件和 `category_match`，再按类别置信度、创意相关性、公开持续投放信号、投放时长、证据完整度和媒体可见性排序。

## 8. 临时数据与清理

AI 系统不作为广告素材或最终结果的长期仓库。

- 最终完整结果 JSON：从 `completed_at` 起最多保留 24 小时；
- 临时视频、封面、关键帧：最多与结果同生命周期；
- 原始候选及中间模型细节：完成排序后尽快删除；
- 不含完整广告内容的任务摘要、错误摘要和指标：可保留 30 天用于排障与统计。

Celery Beat 扫描 `result_expires_at`，向 `ad_research_queue` 投递清理任务。清理任务幂等地删除 result JSON、临时媒体与中间候选，将任务更新为 `expired`。

## 9. 数据模型

`AdResearchJob` 关键字段：

```text
id
external_user_id
request_fingerprint
country
category
seed_keywords_json
target_count
status
stage
current_round
progress_json
summary_json
result_json
result_expires_at
error_code
error_message_summary
created_at
started_at
completed_at
expired_at
```

复用通用 `GenerationTask`：

```text
task_type = ad_research
queue_name = ad_research_queue
business_id = AdResearchJob.id
```

临时媒体记录只保留任务所需的来源 URL、本地临时路径、媒体类型、时长、缩略图路径和过期时间。

## 10. 首期验收

对于 `IN + gambling + 25`：

- 创建任务立即返回；
- 相同 `external_user_id` 幂等复用；参数冲突返回 409；
- 任务由 Celery/Redis 异步执行；
- 同时最多两个研究任务、全局最多六个模型调用；
- 轮询能看到阶段与进度；
- 成功时返回 25 条，或明确 `insufficient`；
- 每条最终广告均满足 ACTIVE、有视频、视频不超过 30 秒、投放超过 2 天、ID 无重复；
- 每条包含模型类别结论、分数、文字和视觉证据；
- 不足时自动补采；
- 结果 24 小时内可重复读取；
- 到期后清理数据并返回 410。

## 11. 实施阶段

1. 任务模型、外部创建/轮询接口与 `external_user_id` 幂等；
2. `ad_research_queue`、Worker、Beat 清理和 Redis 全局模型限流；
3. 采集适配器和标准化字段；
4. 媒体探测、视频时长、封面和代表帧；
5. GPT-5.4 mini 检索规划、多模态判别与结构化评分；
6. 自动补采、排序、Top 25 和 `insufficient`；
7. 临时结果 TTL、清理、恢复和监控；
8. 印度博彩实测、报告和调优。
