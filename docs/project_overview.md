# 项目说明

## 项目目标

本项目用于把外部投放系统里的工单转成 AI 可处理的内容生产流程，并保留人工审核。

目标不是替代投放系统，而是让运营在正式投放前快速完成：

- 工单参数识别。
- 投放国家、年龄、人群、事件、落地页确认。
- 选题生成和选择。
- 文案生成和审核。
- 图片生成和审核。
- 视频生成和审核。
- 最终投放数据包预审和回传。

## 当前核心模块

- FastAPI 后端。
- React 运营工作台。
- WorkOrder 工单。
- Campaign 内容生产项目。
- LandingPageSnapshot 落地页分析。
- Topic 选题。
- CopyDraft 文案。
- CreativeAsset 图片素材。
- VideoAsset 视频素材。
- ReviewTask 人工审核。
- AdGenerationJob 外部投放系统集成任务。

## 不再包含的模块

- Meta OAuth 登录。
- Facebook Page 获取。
- Ad Account 获取。
- Pixel 获取。
- 创建 Facebook 发布任务。
- dry-run 发布。
- 一键发布。
- 激活/暂停广告。
- 同步 Meta 状态。
- Meta Insights 报表。
- PublishJob 类型审核。
- 旧发布页。
- Celery/Redis 异步队列。

## 本地启动

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8001 --reload
```

```powershell
npm run dev -- --host 0.0.0.0
```

Swagger:

```text
http://127.0.0.1:8001/docs
```
