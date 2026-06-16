# 当前项目状态

## 已完成

- 外部投放系统跳转式 AI 生成流程。
- AI 工单创建与列表。
- 工单字段 LLM 识别和人工确认；确认后创建 AI 任务会复用第一次识别结果。
- 外部任务 `review_url`。
- 选题、文案、图片、视频的人工审核流程。
- 最终投放数据包保存与确认回传。
- 前端工作台按运营流程重新排版。
- 删除旧 Meta 授权、发布、Insights 和 PublishJob 审核代码。
- 删除 Celery/Redis 依赖。

## 当前流程状态

任务会按以下状态推进：

```text
queued
processing
fields_review
topic_review
copy_review
image_review
video_review
final_review
returned
failed
```

## 后续建议

1. 优化工单识别提示词和字段置信度展示。
2. 接入真实图片生成和视频生成供应商。
3. 增加最终预审校验规则。
4. 与外部投放系统联调 `return_url` 和 `job_id` 查询。
5. 等多人使用时再补用户、角色、权限和操作审计。
