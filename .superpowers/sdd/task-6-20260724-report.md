# Task 6 完成报告：查询并发、完整来源归因和评分状态机

- 工作目录：`C:\Users\panda\AppData\Local\Temp\广告研究召回视觉评分设计-20260724`
- 分支：`文档/广告研究召回与视觉评分优化`
- 基线：`51a7cb0`
- 实现提交：`1caaebf6040d7e2b8673029b1d3286fa77b348f5`（功能：并发采集并补偿失败广告评分）

## RED

先新增并运行了简报要求的两个测试：

1. `test_orchestrator_bounds_collector_concurrency_and_keeps_origin`
2. `test_retryable_score_is_retried_before_more_collection`

执行命令：

```powershell
& 'C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe' -m pytest tests/test_ad_research_orchestrator.py -k "bounds_collector or retryable_score" -v
```

结果：RED。原实现没有公开 `source_query` 归因字段，且重试型评分失败会被失败集合排除，未在下一轮采集前优先补偿评分。

## GREEN

最小实现包括：

- 每轮 Collector 查询使用 `asyncio.gather`，并由 `AD_RESEARCH_COLLECTOR_CONCURRENCY` semaphore 严格限流；单查询仍使用固定 `PER_QUERY_LIMIT=50`，结果回收时受全局原始候选上限约束。
- 同一广告 ID 保持单次技术探测和单次成功评分；后续查询只按首次出现顺序更新完整来源归因。
- 公共结果新增 `source_query`、`matched_queries`、`matched_user_keywords`、`matched_query_origins`，保留 `first_source_query_id`、`source_query_ids` 兼容字段。
- 评分状态机输出 `pending`、`scoring`、`retryable_failed`、`scored`、`permanent_failed`；每候选最多 3 次编排级评分机会，重试型失败在后续采集前优先补偿，包含 `permanent failure` 的 `ProviderError` 不再补偿。
- 汇总新增 `model_scoring_states`，并保留 `model_scoring_failed` 兼容字段。

## 验证

```powershell
& 'C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe' -m pytest tests/test_ad_research_orchestrator.py -k "concurrency or source or query_metrics or scoring_failure or retries" -v
# 6 passed, 27 deselected

& 'C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe' -m pytest tests/test_ad_research_orchestrator.py -q
# 33 passed

& 'C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe' -m ruff check backend/app/services/ad_research_orchestrator.py tests/test_ad_research_orchestrator.py
# All checks passed!

git diff --check
# 无输出
```

## 风险和边界

- 本次仅使用 Fake Collector、Fake Media 和 Fake Model 的单元级验证，尚未连接真实 Collector 或真实模型网关。
- `MODEL_SCORE_ATTEMPTS=3` 是编排级评分机会；既有单次评分内立即重试兼容逻辑仍可能使一次编排机会包含额外的底层模型调用。
- 未修改前端、部署配置、环境变量、`AGENTS.md` 或 `.env`，未执行推送、SSH、部署。
