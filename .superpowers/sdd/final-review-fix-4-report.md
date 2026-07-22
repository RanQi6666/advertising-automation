# Storyboard V2 Final Fix 4 Report

## 基本信息

- 日期：2026-07-22
- 工作区：`C:\Users\panda\Documents\Advertising Automation\.worktrees\storyboard-v2-dynamic-action-arc`
- 起始 HEAD：`bb6254ee0380a0dbebb6d2899f62cfea15b11322`
- 审查输入：
  - `.superpowers/sdd/final-fix-3-review.md`
  - `.superpowers/sdd/review-final-fix-3-f289b5d-bb6254e.diff`
  - `docs/superpowers/specs/2026-07-17-external-storyboard-v2-frame-anchored-design.md`
  - `docs/superpowers/specs/2026-07-21-storyboard-v2-aaa-director-agent-design.md`
  - `docs/superpowers/specs/2026-07-22-storyboard-v2-dynamic-action-arc-design.md`
  - `docs/superpowers/plans/2026-07-22-storyboard-v2-dynamic-action-arc-implementation.md`
- 处理结论：4 项 finding 均成立，均通过 TDD 增加回归覆盖并修复；无 finding 被推翻。

## Finding 1（Important）：execution 检测接受通用肯定施事关系并识别谓词局部否定

### RED

先增加通过完整 `validate_final_storyboard_action_coverage()` 的正向回归，覆盖：

- 具名主体：`Alex turns and advances.`
- 代词主体：`She moves forward.`
- 普通角色名词：`The warrior turns and advances.`
- 普通物体名词：`The package rotates and moves forward.`
- 否定静止、随后肯定动作：`The subject does not remain still and moves forward.`

原实现依赖有限主体名词白名单，并把前置否定词宽泛作用到后续谓词。RED 结果：上述 5 个正例均被误拒，`5 failed`。

同时扩展负向回归，确保继续拒绝：

- `No subject moves.`
- `Nothing changes.`
- `The warrior does not advance.`
- `The package never rotates.`
- `The subject does not move or turn.`
- 静态主体、camera-only、effect-only、composition-only 文本。

### GREEN / 实现证据

- `backend/app/services/storyboard_director_coverage_service.py`
  - execution 判断不再要求施事者命中有限角色/物体名词白名单。
  - 按 clause 和候选执行谓词建立通用“施事者在谓词前”关系。
  - 排除 `no` / `nothing` 等否定施事者。
  - 否定范围绑定当前执行谓词；`does not remain still` 不再错误否定后续 `moves forward`。
  - camera/effect/composition support cue 仍不能冒充主体执行。
- GREEN：正负组合目标回归 `15 passed`；最终 focused 三文件全量验证也通过。

## Finding 2（Important）：omit 类别必须与 reason/evidence 一致，prompt 明确完整 enum 契约

### RED

新增 schema、coverage review 和 prompt 回归：

1. `mechanism_unavailable` 搭配只有 final pose 的 reason/evidence 必须拒绝。
2. `causal_equivalent_unavailable` 搭配只有 ending framing 的 reason/evidence 必须拒绝。
3. 通过 `model_copy()` 绕过 schema 的同类候选必须被 coverage review 标记为 invalid omission。
4. 合法 literal mechanism unavailable + equivalent causal unavailable 的双重不可行必须通过。
5. OpenAI director prompt 必须精确列出全部 enum、literal/equivalent 字段允许组合及 endpoint 禁用规则。

原实现只检查类别字符串不是 `endpoint_constraint_only`，没有交叉验证类别与 paired reason/evidence；prompt 也未完整告知枚举。RED 结果：3 个目标回归失败。

### GREEN / 实现证据

- `backend/app/schemas/ai.py`
  - 保持 public API 不变，仅加强内部 director schema。
  - literal omit 只允许：
    - `target_capability_unavailable`
    - `mechanism_unavailable`
    - `identity_semantics_conflict`
  - equivalent omit 只允许：
    - `target_capability_unavailable`
    - `mechanism_unavailable`
    - `identity_semantics_conflict`
    - `causal_equivalent_unavailable`
  - `endpoint_constraint_only` 在 literal/equivalent omit 两侧均禁用。
  - category 必须分别与 paired reason 和 evidence 的根因语义一致；仅 endpoint pose/framing/composition/scale 描述无法伪装为其他类别。
- `backend/app/services/storyboard_director_coverage_service.py`
  - `_valid_omission()` 重用相同一致性检查，防御 `model_copy()` 等跳过 schema validation 的内部对象。
- `backend/app/integrations/llm/openai_provider.py`
  - director system prompt 明确列出全部五个精确 enum 值。
  - 明确 literal/equivalent 各自允许值、reason/evidence 匹配要求和 `endpoint_constraint_only` 禁用规则。
- GREEN：目标 schema/review/prompt 合同 `6 passed`；合法 mechanism/equivalent 双重不可行仍通过。

## Finding 3（Important）：private ID 使用 namespaced opaque namespace，公开 formatter 只 scrub 安全格式

### RED

新增 polling 与 formatter 回归，构造 private IDs 为 `camera`、`action`、`subject`，同时让最终自然语言包含：

`The camera follows the action while the subject moves.`

目标同时证明：

- private 标识不会公开；
- 普通自然语言逐字保留；
- polling 正常完成；
- 未缓存成功仍是 3 次模型调用。

原实现对所有纯字母 private IDs 做全局普通词替换，因此会破坏公开句子。RED 结果：2 个目标回归失败。

### GREEN / 实现证据

- `backend/app/services/external_ai_generation_service.py`
  - Call 3 前调用 `_normalize_private_storyboard_namespace()`，把完整 private namespace 一致重映射为安全 namespaced opaque IDs，例如：
    - `__sbv2_behavior_001__`
    - `__sbv2_beat_001__`
    - `__sbv2_window_001__`
    - `__sbv2_moment_001__`
  - remap 同步覆盖 reference analysis、timeline、director plan、action windows、signature moments、dependencies 与相关 linkage。
  - 公开 formatter 只 scrub 安全 namespaced ID 格式，不对 `camera` / `action` / `subject` 等普通词做全局替换。
- `tests/test_external_ai_generation.py`
  - fake Call 3 storyboard 从规范化 director plan 动态读取 moment/source/beat IDs，确保 reference-video、retry/cache 和 polling 测试遵守真实 private namespace。
- GREEN：目标 formatter/polling `2 passed`；最终 polling 保留原句、无 private 标识、调用序列仍为 analysis/director/storyboard 三次。

## Finding 4（Minor）：Call 2 invalid omit 与 linkage missing 使用不同脱敏错误分类

### RED

新增两个 Call 2 unrecoverable 路径的错误断言：

- invalid omission IDs / invalid omit reason → `invalid omission contract`
- required signature/source linkage missing → `required signature/source linkage is missing`

两者都必须在 Call 2 后停止，公开错误不得包含 private moment/beat IDs。

初始修复尝试在 namespace normalization 内重新执行完整 schema validation，导致故意使用 `model_copy()` 构造的 invalid omit 在 coverage review 分类前提前抛出 Pydantic `ValidationError`。RED 结果：目标组合 `1 failed, 1 passed`。

### GREEN / 实现证据

- `backend/app/services/external_ai_generation_service.py`
  - normalization 返回 remap 后的内部对象，不在此处重复完整 schema validation；正常 provider 输出已完成 schema validation，绕过对象继续由 coverage review 防御。
  - `_public_unrecoverable_director_reason()` 基于 review 的 `invalid_omission_moment_ids` 和 unrecoverable reasons 输出脱敏分类：
    - invalid omit → `invalid omission contract`
    - linkage missing → `required signature/source linkage is missing`
    - 其他 director contract → `director action coverage contract is invalid`
  - public message 不拼接 private IDs 或私有 correction 内容。
- GREEN：两个目标路径 `2 passed`；均精确停在两次 LLM 调用。

## 已确认保持的 clean 行为

- exact moment phase linkage 保持不变。
- director window overlap 继续使用正确严格区间相交。
- 动态压缩继续由时长、复杂度、证据与窗口预算决定；未引入固定 `0.8` 或固定阶段比例。
- invalid omit 继续在 2 calls 后停止。
- 正常未缓存成功继续精确 3 calls；未增加第 4 次模型调用。
- payoff allocation 隔离测试保持通过。
- public Storyboard V2 POST/polling contract 和 `storyboard_text` shape 不变。
- Redis/Celery/worker、video provider、frontend、migration 未修改。
- 未恢复 `FINAL_TEXT_OVERLAY_LOCKS`。
- 未写死具体创意元素。

## 最终验证命令与结果

### Focused 三文件

```powershell
& 'C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe' -m pytest tests/test_external_ai_generation.py tests/test_frame_anchored_storyboard_provider.py tests/test_storyboard_director_coverage_service.py
```

结果：`173 passed, 1 warning in 18.42s`

### Ruff backend/tests

```powershell
& 'C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe' -m ruff check backend tests
```

结果：`All checks passed!`

### Full pytest

```powershell
& 'C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe' -m pytest
```

结果：`679 passed, 1 warning in 98.17s`

### Whitespace

```powershell
git diff --check
```

结果：通过，无输出。

### Warning

唯一 warning 为现有 FastAPI/Starlette `TestClient` 对 `httpx2` 的 deprecation warning；本轮未引入新 warning。

## Scope

### Runtime 修改

- `backend/app/integrations/llm/openai_provider.py`
- `backend/app/schemas/ai.py`
- `backend/app/services/external_ai_generation_service.py`
- `backend/app/services/storyboard_director_coverage_service.py`

### Test 修改

- `tests/test_external_ai_generation.py`
- `tests/test_frame_anchored_storyboard_provider.py`
- `tests/test_storyboard_director_coverage_service.py`

### 报告

- `.superpowers/sdd/final-review-fix-4-report.md`

## Safety / Contract

- 未修改 public request/response schema、route、polling shape 或 `storyboard_text` shape。
- private analysis/director/review IDs 只在内部使用，Call 3 前规范化，公开结果只 scrub 安全 namespaced 格式。
- 错误分类脱敏，不公开 private moment/beat/window/behavior IDs。
- 未修改 Redis、Celery、worker、video provider、frontend 或 migration。
- 未添加第 4 次模型调用。
- 未修改、暂存或提交 `AGENTS.md`、`.env*`、API key、token、数据库密码、SSH/aaPanel 信息或其他凭证。
- 未执行 reset、rebase、merge 或 push。

## 未解决项

- 功能 finding：无。
- 非阻塞事项：仅保留项目既有 Starlette/httpx deprecation warning。
