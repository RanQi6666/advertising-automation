# Storyboard V2 第九轮稳定执行 Claim 修复报告

## 基本信息

- 日期：2026-07-22
- 工作区：`C:\Users\panda\Documents\Advertising Automation\.worktrees\storyboard-v2-dynamic-action-arc`
- 分支：`codex/storyboard-v2-dynamic-action-arc`
- 基线 HEAD：`6d5688769d5c3b42e63d46b385c5dc05eece52d7`
- 审查输入：`.superpowers/sdd/final-fix-8-review.md`
- 目标：修复第八轮独立审查的两个 Important finding，为 execution evidence 增加稳定的私有 claim identity，并从可读信息重写损坏的第八轮报告。

## TDD 记录

### 首轮 RED

先新增 stable claim identity、同 scene 冲突、跨 scene 冲突、合法多动作、OpenAI parser 和 Mock provider 回归，然后在未修改生产代码时运行目标测试：

```text
8 failed
```

真实失败原因包括：

- 同一 scene 中，同一 claim 的 affirmed 与 negated/static 只要使用不同 `action_or_state_change` 措辞就不会被拒绝；
- 跨 scene 的同一 claim 可出现相反 assertion；
- `StoryboardExecutionEvidence` 没有 `claim_id`；
- OpenAI parser 不保留 `claim_id`；
- Mock provider 不生成 `claim_id`。

### 第一轮 GREEN

实现最小生产改动后，原 8 个目标用例结果为：

```text
8 passed in 1.23s
```

### 隐私边界补充 RED

继续增加 normalization collision、raw alias polling 泄漏、canonical claim ID formatter scrub，以及 final validator 对赋值注入 dict evidence 的再验证，真实结果为：

```text
3 failed
```

三处问题分别是：

- provider 原始 alias `primary-claim` 仍可能进入 polling 的 `storyboard_text`；
- final validator 遇到通过赋值注入的 dict evidence 时触发 `AttributeError`；
- formatter fixture 没有把 canonical claim ID 纳入私有来源。

### 隐私边界 GREEN

修复 alias 替换、统一 revalidation 和 formatter fixture 后：

```text
3 passed, 1 warning in 3.27s
```

## 生产实现

### 1. 受约束的私有 `claim_id`

`StoryboardExecutionEvidence` 新增必填 `claim_id`，长度限制为 1 到 128，并只接受：

- canonical 私有 ID，例如 `__sbv2_claim_001__`；
- 由字母数字片段和 `_`、`:`、`.`、`-` 分隔符组成的 provider token，例如 `execution_claim` 或 `claim:moment_1`。

普通自然语言句子不能作为 claim ID。`action_or_state_change` 继续用于描述非空、可见的动作或状态变化，但不再参与 claim identity。

### 2. Claim identity 与一致性规则

同一 `claim_id` 必须始终绑定同一组结构化信息：

- `executor_kind`；
- exact `signature_moment_ids`；
- exact `source_behavior_beat_ids`。

校验规则同时作用于单个 scene 和整个 storyboard：

- 同一 claim ID 绑定不同 executor、signature IDs 或 source IDs 时拒绝；
- 同一 claim ID 出现不同 assertion 时拒绝，因此 affirmed 不能与 negated 或 static 共存，即使动作措辞完全不同；
- 跨 scene 使用同一 claim ID 也执行同样的一致性校验；
- 语义不同的合法动作使用不同 claim ID，可以在同一 scene 共存；
- preparation/static 与 execution/affirmed 若语义不同，必须使用不同 claim ID。系统不从自由文本推断两者是否是同一 claim。

final coverage validator 在执行 coverage gate 前再次运行 storyboard 级 claim 一致性校验；即使测试或内部代码通过赋值注入 dict evidence，也会先重新执行 Pydantic validation，而不是绕过结构化约束。

### 3. 最终动作覆盖门禁保持严格

本轮没有放宽第八轮 execution gate。最终覆盖仍只接受：

1. assertion 为 `affirmed`；
2. executor 是 `target_subject`、`target_object` 或 `target_state`；
3. `action_or_state_change` 非空；
4. 与所需 scene、signature moment 和 source behavior beat 精确关联。

`camera_support`、`effect_support`、`environment_support`、`negated` 与 `static` 仍不能替代目标动作执行。

### 4. Provider 与 Mock 同步

- OpenAI Call 3 system prompt 明确要求输出稳定、非 prose 的 `claim_id`，并说明 claim 绑定、跨 scene 复用和不同语义动作使用不同 ID 的规则。
- OpenAI parser 保留 `claim_id`，并继续规范化 signature/source ID 列表。
- Mock provider 按 signature moment 生成 claim ID，使 mock 与真实 provider 使用同一私有契约。
- 没有增加新模型调用。正常成功仍是三次 LLM；invalid omit 仍在 Call 2 后停止，共两次 LLM；不存在第 4 次审片。

### 5. Private ID normalization、collision、scrub 与 revalidation

Call 3 返回后，service 将 claim namespace 规范化为 `__sbv2_claim_NNN__`：

- 已存在的 canonical claim ID 作为 reserved ID 保留；
- provider alias 分配到未占用的 canonical ID，避免与 reserved ID 碰撞；
- 同一 alias 在所有 evidence 和可渲染字符串中统一替换；
- 规范化后的完整 storyboard 再次通过 `FrameAnchoredStoryboard.model_validate()`，重新执行 binding 与 assertion consistency 校验；
- `_SAFE_PRIVATE_ID_PATTERN` 增加 `claim` namespace，formatter 使用既有 private-ID scrub 删除 canonical claim ID；
- raw alias 若被模型复制进 visual、motion 等 prose，会先替换为 canonical ID，再由 formatter scrub，因此 raw 和 canonical claim ID 都不会进入 `storyboard_text` 或 polling 响应。

## 第八轮报告恢复

`.superpowers/sdd/final-review-fix-8-report.md` 没有做不可逆内容的编码转换，而是依据第八轮提交、可读测试记录和独立审查重新撰写。恢复后的报告准确记录：

- 结构化 `execution_evidence` 替代 motion prose 作为 execution gate；
- target executor、assertion、可见动作文本与 exact signature/source linkage；
- camera/effect/environment support、negated/static 不能替代 target execution；
- omit 使用 category、basis、polarity、scope、detail 的结构化事实；
- invalid omit 在 Call 2 后停止，正常成功保持三次调用；
- 第八轮当时仍缺 stable claim identity，这正是第九轮修复范围。

恢复后程序化检查结果：

```text
Strict UTF-8：通过
U+FFFD：0
ASCII 问号：0
连续 4 个以上 ASCII 问号：0
标题：# Storyboard V2 第八轮结构化执行与省略重构报告
中文行数：74
```

标题和中文限制段落已人工读取，可正常显示。

## 修改文件

生产代码：

- `backend/app/schemas/ai.py`
- `backend/app/integrations/llm/openai_provider.py`
- `backend/app/integrations/llm/mock_provider.py`
- `backend/app/services/external_ai_generation_service.py`
- `backend/app/services/storyboard_director_coverage_service.py`

测试：

- `tests/test_storyboard_director_coverage_service.py`
- `tests/test_frame_anchored_storyboard_provider.py`
- `tests/test_external_ai_generation.py`

报告与 ledger：

- `.superpowers/sdd/final-review-fix-8-report.md`
- `.superpowers/sdd/final-review-fix-9-report.md`
- `.superpowers/sdd/progress.md`

## 最终验证

### Focused

```text
164 passed, 1 warning in 18.37s
```

覆盖文件：

- `tests/test_storyboard_director_coverage_service.py`
- `tests/test_frame_anchored_storyboard_provider.py`
- `tests/test_external_ai_generation.py`

### Ruff

第一次最终 Ruff 发现 prompt 字符串一处 `E501`，仅换行字符串、不改变拼接结果后重新运行：

```text
All checks passed!
```

### Full pytest

```text
670 passed, 1 warning in 98.66s
```

### 调用次数专项

```text
2 passed, 1 warning in 3.52s
```

覆盖：

- 正常未缓存成功严格三次 LLM 调用；
- invalid omit 严格在 Call 2 后停止。

### Diff、范围和敏感检查

- `git diff --check`：通过。
- 修改文件不包含 public route 或 public request/polling schema。
- 未修改 Redis、Celery、queue、worker、generation task、video provider、frontend 或 migration。
- 未恢复 `FINAL_TEXT_OVERLAY_LOCKS`。
- 未写死 sword、diamond、`x200,000` 或其他特定创意内容。
- 未修改或提交 `AGENTS.md`、`.env*`、token、API key、密码、SSH 私钥或其他凭证。
- polling 返回结构未增加 `claim_id` 字段，`storyboard_text` 不泄漏 raw 或 canonical claim ID。

## Concern

唯一已知非阻塞项是测试环境既有的 Starlette/FastAPI `TestClient` 对 `httpx2` 的 deprecation warning。本轮未修改依赖或测试基础设施。没有发现与 stable claim identity、公共边界或调用次数相关的剩余阻塞。