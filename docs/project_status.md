# 广告自动化项目当前思路框架

## 1. 项目定位

这个项目的目标，是把 Facebook 广告投放里一条重复度很高的链路自动化：

`工单 -> 解析 -> Campaign -> 落地页分析 -> 选题 -> 文案 -> 素材 -> 视频 -> 审核 -> 发布 -> 数据回流`

核心原则不是完全替代运营，而是把高频、机械、可标准化的部分自动化，把关键判断保留给人工审核。

## 2. 当前主流程

目前系统已经形成一条比较完整的 MVP 主流程：

1. 运营提交工单原文
2. 系统解析工单字段
3. 从工单创建 Campaign
4. 抓取并分析落地页
5. 根据工单 + 落地页 + 额外 signals 生成广告选题
6. 人工选择选题
7. 基于选题生成广告文案
8. 人工审核文案并可修订
9. 生成图片素材
10. 基于图片生成视频脚本
11. 创建视频任务并同步生成状态
12. 创建发布任务
13. 先 dry-run，再进入真实 Facebook / Meta 发布
14. 手工录入或后续自动回流投放数据

## 3. 已实现的部分

### 3.1 后端基础框架

- FastAPI 服务入口已搭好
- CORS、异常处理、静态文件挂载已配置
- PostgreSQL async + SQLAlchemy 模型已接好
- Alembic 迁移已建立并且已有初始表结构
- 健康检查接口已实现

### 3.2 工单与 Campaign

- 支持提交工单原文
- 支持从工单解析常见字段
- 支持从工单直接创建 Campaign
- 支持保存工单原文和解析结果
- 支持把工单上下文写入 Campaign metadata，供后续流程复用

### 3.3 落地页分析

- 支持根据 Campaign 或工单里的落地页链接抓取页面
- 支持解析：
  - title
  - description
  - headings
  - links
  - text excerpt
- 支持把落地页快照保存到数据库
- 支持把快照结果回写到 Campaign metadata

### 3.4 选题生成

- 已有 LLM provider 抽象
- 已有 mock provider
- 已有 OpenAI provider
- 已有 Volcengine 兼容 provider
- 选题生成会结合：
  - Campaign 信息
  - 工单上下文
  - 落地页上下文
  - 额外 signals
- 支持选题列表查询、选中、拒绝

### 3.5 文案生成

- 已实现广告文案生成
- 已实现基于人反馈的文案 revision
- 文案会保存版本号、模型名、prompt 版本、metadata
- 支持按 Campaign 查看草稿列表

### 3.6 图片素材

- 已实现图片 brief 生成
- 已实现图片素材记录创建
- 已有 image provider 抽象
- 已有 placeholder provider
- 已有 Volcengine image provider

### 3.7 视频任务

- 已实现视频 storyboard 生成
- 已实现视频任务创建
- 已实现视频任务状态查询和刷新
- 已有 video provider 抽象
- 已有 placeholder video provider
- 已有 Volcengine video provider
- 已实现视频成功后下载并转存到本地 storage

### 3.8 审核流

- 已实现 review 记录提交
- 已实现待审核列表
- 已实现对以下对象的审核状态同步：
  - topic
  - copy draft
  - creative asset
  - video asset
  - publish job

### 3.9 发布与 Meta 适配

- 已实现 Facebook 页面发布 dry-run
- 已实现 Facebook 广告相关 request 预览
- 已实现 Meta Ads draft / package 逻辑
- 已实现发布任务创建与执行
- 已实现 token reference 管理
- 已实现 Meta 配置检查接口
- 已实现真实请求与 dry-run 分支

### 3.10 数据回流

- 已实现每日 insights 手工录入
- 已实现 Campaign 维度的 daily insights 查询
- 计算了 CTR / CPC

### 3.11 前端管理台

- 已有 React/Vite 管理台
- 页面已经不是空壳，能完整跑通构建
- 目前有这些工作区块：
  - Dashboard
  - Work Orders
  - Campaign
  - Topics
  - Copy
  - Creatives
  - Videos
  - Publishing
- 前端可以直接调用后端 API 完成整条操作链

### 3.12 验证状态

- 后端测试已通过：`26 passed`
- 前端构建已通过：`npm run build`

## 4. 目前没有完全实现 / 仍是预留的部分

### 4.1 LangGraph 工作流还是骨架

- 现在已经有 graph 和 state
- 也有 human-in-the-loop 的 interrupt 节点
- 但节点本身大多还是“透传状态 / 占位返回”
- 真正把服务层能力编排进 graph 的部分还不够完整

### 4.2 Celery 任务还是占位

- `generate_topics_task`
- `publish_job_task`

这两个任务目前只返回 `queued`，还没有真正把业务流程放进异步 worker 执行。

### 4.3 对象存储还没真正扩展到多后端

- 现在视频落盘只支持 `local`
- 配置里虽然预留了 `s3 / r2 / minio`
- 但 `VideoStorageService` 里遇到非 local 会直接报错
- 图片素材也还没有独立的对象存储落盘流程

### 4.4 登录、权限、角色管理还没有做完

- `User`、`FacebookAccount` 这些模型已经存在
- 但还没有对应的登录 / 鉴权 / RBAC API
- 也没有前端登录态和权限控制

### 4.5 Meta Insights 自动回流还没有接

- 现在只有手工录入 daily insights
- 还没有从 Meta 自动拉取广告表现数据的任务

### 4.6 AI 工单深度解析还比较基础

- 现在工单解析主要是规则和字段别名
- 能满足常见结构化字段提取
- 但还没有做更强的 AI 语义解析、容错解析、复杂模板适配

### 4.7 生产联调仍依赖外部配置

- OpenAI / Volcengine / Facebook 真实联调都依赖环境变量和账号权限
- 代码层面的 adapter 已经有了
- 但真实生产环境还需要你把 key、token、Page、Ad Account 等配置完整

## 5. 当前技术栈

- 后端：Python + FastAPI + SQLAlchemy Async
- 数据库：PostgreSQL
- 迁移：Alembic
- 任务队列：Celery + Redis
- AI：LLM / Image / Video provider 抽象
- 工作流：LangGraph
- 前端：React + Vite + TypeScript
- 存储：当前 local storage，未来可扩展到 S3 / R2 / MinIO

## 6. 现在这个项目可以怎么理解

如果用一句话概括：

这是一个“以工单为入口、以人审为控制点、以 AI 为生产力、以 Facebook 发布为终点”的广告自动化中台。

它现在已经不是纯概念，也不是空架子，而是一个可以本地跑通的 MVP。
真正还差的，是把骨架里的占位能力，逐步替换成更完整的异步编排、真实回流、权限体系和生产级外部联调。

