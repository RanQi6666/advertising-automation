# Storyboard V2 确定性编译器实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Storyboard V2 最终模型输出改为结构化创意草稿，并由后端确定性编译为现有完整分镜。

**Architecture:** 新增 Draft Pydantic Schema 和独立编译器。Provider 的第三次模型调用返回 Draft，服务层编译后继续运行现有覆盖校验和公开文本格式化。

**Tech Stack:** Python 3.12、Pydantic v2、FastAPI、pytest、Gateway Responses strict JSON Schema。

## Global Constraints

- 不增加模型调用次数。
- 不增加第二次大模型审稿/修复调用。
- 外部接口和 `storyboard_text` 契约不变。
- 不修改或提交 `AGENTS.md`、`.env*`、密钥和主工作区其他改动。
- 所有生产代码遵循测试先行。

---

### Task 1: 定义分镜创意草稿 Schema

**Files:**
- Modify: `backend/app/schemas/ai.py`
- Test: `tests/test_frame_anchored_storyboard_provider.py`

**Interfaces:**
- Produces: `FrameAnchoredStoryboardDraft`, `FrameAnchoredStoryboardDraftScene`, `StoryboardDraftExecutionAction`

- [ ] 写失败测试，证明 Gateway 最终分镜请求携带 Draft strict JSON Schema，Schema 不含 `claim_id` 和完整 `phase_evidence`。
- [ ] 运行测试确认 RED。
- [ ] 实现最小 Draft Schema，并让 Provider 使用 `response_model=FrameAnchoredStoryboardDraft`。
- [ ] 运行测试确认 GREEN。

### Task 2: 实现确定性编译器

**Files:**
- Create: `backend/app/services/storyboard_v2_compiler.py`
- Test: `tests/test_storyboard_v2_compiler.py`

**Interfaces:**
- Consumes: `FrameAnchoredStoryboardDraft`, `FrameAnalysis`, target duration/aspect ratio
- Produces: `compile_storyboard_v2(...) -> FrameAnchoredStoryboard`

- [ ] 写失败测试覆盖 claim 稳定生成、空 phase 绑定、未知 ID、`final_lock`、首尾锚点和目标时长。
- [ ] 运行测试确认 RED。
- [ ] 实现最小编译器。
- [ ] 运行测试确认 GREEN。
- [ ] 重构并保持测试通过。

### Task 3: 接入 Provider、Mock 和服务层

**Files:**
- Modify: `backend/app/integrations/llm/base.py`
- Modify: `backend/app/integrations/llm/openai_provider.py`
- Modify: `backend/app/integrations/llm/mock_provider.py`
- Modify: `backend/app/services/external_ai_generation_service.py`
- Test: `tests/test_frame_anchored_storyboard_provider.py`
- Test: `tests/test_external_ai_generation.py`

**Interfaces:**
- Provider returns Draft.
- Service compiles Draft, validates coverage, and returns unchanged public fields.

- [ ] 写/调整失败测试，复现自然语言 claim、空 evidence、非法 tension hint 不再阻断草稿解析。
- [ ] 运行测试确认 RED。
- [ ] 接入编译器并更新 Mock。
- [ ] 运行定向测试确认 GREEN。

### Task 4: 回归验证和范围审查

**Files:**
- Verify only

- [ ] 运行 `tests/test_storyboard_v2_compiler.py`。
- [ ] 运行 `tests/test_frame_anchored_storyboard_provider.py`。
- [ ] 运行 `tests/test_external_ai_generation.py`。
- [ ] 运行完整 pytest 和 ruff。
- [ ] 检查 `git diff --check`、`git status --short` 和敏感文件排除。
- [ ] 只在用户要求时提交、推送、部署或创建真实任务。
