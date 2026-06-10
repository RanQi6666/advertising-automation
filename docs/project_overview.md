# 广告自动化投放项目说明文档

## 1. 项目目标

本项目用于广告投放公司的 Facebook 广告运营自动化。

系统目标不是直接替代运营人员，而是把重复性的内容生产、素材生成、发布准备和数据沉淀流程自动化，同时保留人工审核环节。

核心目标：

- 根据工单内容自动理解投放需求
- 根据投放链接抓取落地页信息
- 根据工单和落地页生成广告选题
- 根据选题生成宣传文案
- 根据文案生成广告图片
- 根据图片预留视频生成流程
- 经过人工审核后创建发布任务
- 后续接入 Facebook Pages API 和 Meta Marketing API
- 回收投放数据，用于下一轮内容优化

## 2. 当前项目状态

当前项目已经完成后端 MVP 骨架。

已完成：

- FastAPI 后端服务
- PostgreSQL 数据模型
- Alembic 数据库迁移
- Swagger API 文档
- 工单 WorkOrder 入口
- 工单字段解析
- 从工单创建广告项目 Campaign
- 投放链接落地页抓取
- AI 生成选题接口
- AI 生成文案接口
- 图片素材记录生成
- 图片生成视频任务预留
- 人工审核接口
- 发布任务接口
- Facebook 发布 dry-run
- 数据回流 Insights 表预留

暂未完成：

- 前端后台页面
- 真实 LLM API 接入
- 真实图片生成模型接入
- 真实视频生成模型接入
- 真实 Facebook Pages API 发布
- 真实 Meta Marketing API 创建广告
- 登录、权限、角色管理
- Meta Insights 数据自动回收
- AI 工单解析增强

## 3. 核心业务流程

当前主流程以工单为入口。

```text
运营人员提交工单
  -> 保存工单原文
  -> 解析工单字段
  -> 从工单创建广告项目
  -> 抓取投放链接落地页
  -> AI 根据工单 + 落地页生成 1-3 个选题
  -> 人工选择选题
  -> AI 生成文案
  -> 人工审核文案
  -> 根据文案生成图片记录
  -> 人工审核图片
  -> 可选：根据图片创建视频任务
  -> 人工审核视频
  -> 创建发布任务
  -> dry-run 发布
  -> 后续接入真实 Facebook / Meta API
  -> 回收投放数据
```

## 4. 工单 WorkOrder

真实业务中，运营人员拿到的通常是一段工单文本，而不是结构化的客户、品牌、广告项目。

示例：

```text
工单

项目名称：印度tv8%
投放国家：印度
投放时间：待定
日报时区：+7
投放媒体：fb
投放事件：购物
投放人群；男。年龄25-45

产品名称：印度tv
打款金额：216（广告过审打款）
服务费：8%
商务：西伯
投放链接：https://www.mensparadise.store/TV.html
```

系统会保存完整原文：

```text
work_orders.raw_content
```

同时尽力解析常见字段：

```text
project_name
country
media
event_name
product_name
audience_description
landing_url
report_timezone
parsed_fields
```

其中 `parsed_fields` 用于保存所有解析出来的字段，包括不固定字段。

## 5. 广告项目 Campaign

Campaign 是后续 AI 生成、素材生产、审核、发布的核心业务对象。

目前 Campaign 可以直接从 WorkOrder 创建，不再强制要求 Client / Brand。

Client / Brand 保留为可选能力，适合未来多客户、多品牌管理场景。

Campaign 会保存：

```text
name                  项目名称
objective             投放事件或广告目标
product_name          产品名称
audience_description  投放人群
budget_notes          金额、服务费等信息
work_order_id         关联工单
metadata.work_order   工单上下文快照
metadata.landing_page 落地页上下文快照
```

## 6. 投放链接落地页分析

投放链接是 AI 生成内容的重要依据。

系统会根据工单中的 `投放链接` 抓取落地页，并提取：

```text
url
http_status
title
description
text_content
headings
links
text_excerpt
```

结果保存到：

```text
landing_page_snapshots
```

并写入：

```text
campaigns.metadata.landing_page
```

后续生成选题、文案、图片 brief、视频任务时，都可以读取这份上下文。

## 7. AI 生成选题

选题生成接口会读取：

- Campaign 信息
- WorkOrder 工单上下文
- LandingPageSnapshot 落地页上下文
- 调用方传入的额外 signals

当前生成数量限制为：

```text
最少 1 个
最多 3 个
默认 3 个
```

选题会保存到：

```text
content_topics
```

每个选题包含：

```text
title
angle
audience
selling_points
risk_notes
rationale
score
status
source_data
```

当前 LLM 是 mock provider，不会调用真实大模型。

## 8. AI 生成文案

人工选择选题后，可以生成文案。

文案保存到：

```text
copy_drafts
```

主要字段：

```text
body           完整宣传文案
primary_text   Facebook 主文案
headline       广告标题
description    广告描述
cta            按钮建议
status         审核状态
version        版本号
model_name     使用模型
prompt_version Prompt 版本
metadata       生成上下文
```

如果人工审核不通过，可以根据反馈生成新版本。

## 9. 图片生成

文案审核通过后，可以生成图片素材记录。

当前流程：

```text
文案
  -> 生成图片 brief
  -> 创建 creative_assets 记录
```

当前还没有真正生成图片文件。

配置为：

```text
IMAGE_PROVIDER=placeholder
```

图片素材记录保存到：

```text
creative_assets
```

主要字段：

```text
campaign_id
draft_id
prompt
url
storage_key
alt_text
size
status
version
metadata
```

未来接入真实图片生成模型后，图片文件建议存放在 MinIO / S3 / R2，PostgreSQL 只保存图片 URL、storage key 和元数据。

## 10. 视频任务

视频生成目前是预留能力。

当前可以根据已生成的图片创建视频任务记录：

```text
creative_asset_ids
  -> video_assets
```

视频任务保存到：

```text
video_assets
```

主要字段：

```text
campaign_id
draft_id
source_asset_ids
prompt
duration_seconds
aspect_ratio
status
provider_job_id
url
storage_key
metadata
```

当前状态为：

```text
requested
```

metadata 中会标记：

```text
implementation_status = reserved
```

未来接入视频生成供应商后，可以扩展为：

```text
图片 + 文案 + 落地页上下文
  -> 调用视频生成 API
  -> 保存 provider_job_id
  -> 查询生成状态
  -> 保存视频 URL
```

## 11. 人工审核

人工审核是当前系统的重要控制点。

可审核对象：

```text
topic
copy_draft
creative_asset
video_asset
publish_job
```

审核结果保存到：

```text
review_tasks
```

审核决策：

```text
approved
rejected
needs_revision
```

审核会同步更新对应对象状态。

## 12. 发布任务

审核通过后，可以创建发布任务。

发布任务保存到：

```text
publish_jobs
```

支持 channel：

```text
facebook_page
facebook_ad
```

当前默认：

```text
FACEBOOK_DRY_RUN=true
```

因此当前不会真的发布到 Facebook，只会返回模拟 ID：

```text
dry_run_post_xxx
dry_run_photo_xxx
dry_run_ad_xxx
```

## 13. Facebook Pages API 和 Meta Marketing API 规划

当前只完成 adapter 骨架和 dry-run。

未来 Facebook Pages API 用于：

```text
发布主页文字帖
发布主页图片帖
发布主页视频帖
```

未来 Meta Marketing API 用于：

```text
上传图片 adimages
上传视频 advideos
创建 AdCreative
创建 Campaign
创建 AdSet
创建 Ad
回收 Ads Insights
```

## 14. 主要 API

工单：

```text
POST /api/v1/work-orders
GET  /api/v1/work-orders
POST /api/v1/work-orders/{work_order_id}/campaign
```

落地页：

```text
POST /api/v1/campaigns/{campaign_id}/landing-page/analyze
GET  /api/v1/campaigns/{campaign_id}/landing-page/snapshots
```

选题：

```text
POST /api/v1/topics/generate
GET  /api/v1/campaigns/{campaign_id}/topics
POST /api/v1/topics/{topic_id}/select
POST /api/v1/topics/{topic_id}/reject
```

文案：

```text
POST /api/v1/copywriting/generate
POST /api/v1/copywriting/{draft_id}/revise
GET  /api/v1/campaigns/{campaign_id}/drafts
```

图片：

```text
POST /api/v1/creatives/generate
GET  /api/v1/campaigns/{campaign_id}/creatives
```

视频：

```text
POST /api/v1/videos/from-images
GET  /api/v1/campaigns/{campaign_id}/videos
```

审核：

```text
POST /api/v1/reviews
GET  /api/v1/reviews/pending
```

发布：

```text
POST /api/v1/publishing/jobs
GET  /api/v1/publishing/jobs
POST /api/v1/publishing/jobs/{job_id}/publish
```

数据：

```text
POST /api/v1/insights/daily
GET  /api/v1/campaigns/{campaign_id}/insights/daily
```

健康检查：

```text
GET /api/v1/health/live
GET /api/v1/health/ready
```

## 15. 本地启动方式

安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

启动依赖服务：

```powershell
docker compose -f infra/docker-compose.yml up -d
```

执行迁移：

```powershell
alembic upgrade head
```

启动 API：

```powershell
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

Swagger 文档：

```text
http://127.0.0.1:8000/docs
```

## 16. 当前技术栈

后端：

```text
Python
FastAPI
SQLAlchemy Async
PostgreSQL
Alembic
LangGraph
Celery
Redis
```

AI：

```text
LLM Provider 抽象
Mock LLM Provider
OpenAI Provider 预留
Image Provider 抽象
Placeholder Image Provider
Video 任务预留
```

外部平台：

```text
Facebook Pages API adapter 预留
Meta Marketing API channel 预留
```

存储：

```text
PostgreSQL 保存结构化数据
MinIO / S3 / R2 规划保存图片和视频文件
```

## 17. 下一步建议

建议优先级：

1. 接入真实 LLM API，让选题和文案真正可用
2. 用 AI 解析工单，替代当前规则解析
3. 接入真实图片生成模型
4. 做前端审核后台
5. 接入真实 Facebook Pages API
6. 接入真实 Meta Marketing API 创建广告
7. 接入 Meta Insights 数据回流
8. 增加用户登录、角色权限和操作审计
9. 增加工单、文案、图片、视频的版本对比
10. 增加投放效果驱动的自动优化逻辑

