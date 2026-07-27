# Storyboard V2 Final Fix 3 Report

## 基本信息

- 日期：2026-07-22
- 工作区：`C:\Users\panda\Documents\Advertising Automation\.worktrees\storyboard-v2-dynamic-action-arc`
- 起始 HEAD：`f289b5d8752a1813e6feb981e2f06efb29e3d474`
- 审查输入：
  - `.superpowers/sdd/final-fix-2-review.md`
  - `.superpowers/sdd/review-final-fix-2-40c1c29-f289b5d.diff`
  - `.superpowers/sdd/final-review-fix-2-report.md`
  - `docs/superpowers/specs/2026-07-22-storyboard-v2-dynamic-action-arc-design.md`
  - `docs/superpowers/plans/2026-07-22-storyboard-v2-dynamic-action-arc-implementation.md`
- 处理结论：8 项 finding 均成立，均已按 TDD 增加回归测试并修复；无 finding 被推翻。

## Finding 1：否定、静态、无主体及 support-only 文本不得冒充 execution

### RED

新增参数化回归 `test_final_validation_rejects_static_or_camera_only_execution_text`。在原实现中，`No subject moves.`、`Nothing changes.`、`The subject remains still while the composition shifts.` 等文本仍会因执行动词 substring 或“没有 support cue”路径被识别为执行；camera/effect-only motion 也可误过。

### GREEN / 实现证据

- `backend/app/services/storyboard_director_coverage_service.py::_scene_has_subject_execution`
  - 按 clause 切分 `while/whereas/as` 等作用域。
  - 要求执行动词前存在明确 subject/state actor cue。
  - support cue 若比 actor cue 更接近执行动词，则拒绝 camera/effect 驱动动作。
  - `_NEGATED_EXECUTION_PATTERN` 拒绝 `no/not/never/nothing/without` 等否定范围。
  - `_STATIC_EXECUTION_TERMS` 拒绝 still/static/unchanged 等静止状态。
- GREEN 覆盖九类负例：否定主体动作、nothing changes、主体静止而构图变化、纯静止、final state 不变、camera 包装静态主体、纯 camera、纯 particles、纯 effect。

## Finding 2：omit 使用结构化 literal/equivalent 不可行类别和证据

### RED

新增以下回归：

- `test_signature_schema_rejects_endpoint_only_omit_wrapped_as_infeasible`
- `test_signature_schema_rejects_endpoint_keyword_injection_without_structured_causes`
- `test_signature_schema_accepts_separate_mechanism_infeasibility_causes`
- `test_signature_schema_rejects_when_only_equivalent_reason_is_endpoint_mismatch`
- `test_review_rejects_endpoint_only_omit_wrapped_as_infeasible`

原实现依赖 `cannot/no equivalent/no controllable` 等可注入 substring；endpoint-only 理由可包装绕过，同时真实 mechanism infeasibility 可能因缺少特定词而误拒绝。

### GREEN / 实现证据

- `backend/app/schemas/ai.py::DirectorSignatureMoment` 新增内部字段：
  - `literal_infeasibility_category`
  - `literal_infeasibility_evidence`
  - `equivalent_infeasibility_category`
  - `equivalent_infeasibility_evidence`
- omit 必须分别提供 literal 和 equivalent 两套类别及非空证据；任一类别为 `endpoint_constraint_only` 即拒绝。
- `backend/app/services/storyboard_director_coverage_service.py::_valid_omission` 仅消费上述结构化类别/证据，不再从可注入 prose substring 推断因果类别。
- `backend/app/integrations/llm/openai_provider.py` 的私有 director prompt 和 normalization 同步生成/保留这些字段。
- public Storyboard V2 request/polling contract 未新增字段；这些字段仅存在于内部 director schema/metadata。

## Finding 3：阶段证据必须 exact signature moment linkage

### RED

新增 `test_final_validation_does_not_borrow_payoff_or_return_from_same_source_moment`：构造两个不同 `moment_id` 共用同一 source beat，Moment A 只有 preparation/execution，Moment B 才有 payoff/return。原 OR linkage 会使 A 借用 B 的阶段证据。

### GREEN / 实现证据

- `backend/app/services/storyboard_director_coverage_service.py::_scene_links_moment` 现在只接受 `moment_id in scene.signature_moment_ids`；source ID 不再代替 moment linkage。
- `_validate_moment_phase_order` 的 preparation/payoff/return 均从 exact moment-linked scenes 取证。
- source beat ID 仍用于 required beat 覆盖和 execution/source 一致性校验。
- `backend/app/integrations/llm/mock_provider.py` 为 preparation scenes 补齐对应 `signature_moment_ids`，使 mock 合同与严格 linkage 一致。

## Finding 4：director window overlap 使用标准严格区间相交

### RED

新增参数化 `test_director_phase_overlap_uses_strict_interval_intersection`，覆盖：

- 真重叠：`(0.1, 0.4)` 与 `(0.3, 0.6)`；
- 正向相离：`(0.1, 0.2)` 与 `(0.8, 0.9)`；
- 反向相离：`(0.8, 0.9)` 与 `(0.1, 0.2)`；
- 仅边界接触：`(0.1, 0.2)` 与 `(0.2, 0.4)`。

原非标准条件会误判反向相离窗口，并未明确区分边界接触。

### GREEN / 实现证据

`backend/app/services/storyboard_director_coverage_service.py::_director_phases_overlap` 使用：

```text
max(left.start, right.start) < min(left.end, right.end)
```

严格 `<` 表示只有正长度交集才算重叠，边界接触不算重叠。

## Finding 5：压缩门禁由 duration、window 与证据复杂度动态推导

### RED

新增 `test_phase_compression_changes_with_evidence_complexity_at_same_duration`：同一 storyboard duration 和同一 director windows 下，低复杂度证据判定无需压缩，高复杂度证据判定需要压缩。原 `execution_readability + 4 * 0.8` 对相同 duration 给出固定结论，不能反映实际复杂度。

### GREEN / 实现证据

- 删除 mock 和 coverage service 中全部 `4 * 0.8` 固定阶段预算。
- `backend/app/services/storyboard_director_coverage_service.py::_phase_compression_required_for_duration` 动态计算：
  - 每个 core beat 的 `minimum_readable_duration_seconds`；
  - preparation dependencies/state complexity；
  - payoff `visible_evidence` readability；
  - reference camera movement/intensity；
  - effect evidence；
  - first/last endpoint composition、perspective、visible subjects 差异；
  - continuity/final-lock evidence；
  - director phase windows 在实际 duration 下可提供的秒数。
- `_phase_window_seconds` 合并同 phase 重叠窗口后换算为实际秒数。
- mock provider 复用同一动态 compression helper；未增加固定高潮秒数或固定比例门禁。
- GREEN 明确证明相同 duration、不同复杂度产生不同压缩结论。

## Finding 6：invalid omit 在 Call 2 后 unrecoverable，正常成功仍为 3 calls

### RED

新增：

- `test_review_marks_invalid_omit_as_unrecoverable`
- `test_storyboard_v2_invalid_omit_stops_after_call2`
- 保留/复验 `test_storyboard_v2_uncached_success_uses_exactly_three_llm_calls`

原流程把无法由 Call 3 确定性修复的 invalid omit 当作 correction，仍会浪费 storyboard generation call。

### GREEN / 实现证据

- `review_director_action_coverage` 检出 invalid omit 后返回 `status="unrecoverable"`、`invalid_omission_moment_ids` 和原因，不生成无法验证的 structured correction。
- `ExternalAIGenerationService.execute_frame_anchored_video_storyboard` 在存储 Call 2 review metadata 后立即对 `unrecoverable` 抛出 `ProviderError`，Call 3 不会执行。
- invalid omit 测试精确断言调用序列只有：
  1. `analyze_video_frame_pair`
  2. `direct_frame_anchored_video_storyboard`
- 正常未缓存成功路径精确断言仍为：analysis、director、storyboard 三次 LLM 调用，没有第 4 次调用。

## Finding 7：从完整 private namespace 收集并 scrub private IDs

### RED

新增：

- `test_formatter_scrubs_full_private_namespace_and_preserves_other_language`
- `test_storyboard_v2_polling_scrubs_prose_only_pure_alpha_private_id`

原 scrub 只从 candidate structured fields 收集；只存在于 analysis/director/review/corrections 或 candidate prose 的纯字母 private ID 可能泄漏。

### GREEN / 实现证据

- `backend/app/services/external_ai_generation_service.py::_private_id_values` 递归遍历 dict/list/tuple/set，并收集 `_id`、`_ids`、`depends_on`、`cinematic_beat(s)` 字段。
- `_private_storyboard_ids`：
  - 对 standalone storyboard 继续仅直接收集非纯字母 ID，避免把普通 prose 单词误当 private ID；
  - 对显式 `private_sources` 收集所有 ID，包括纯字母 ID。
- production success formatter 传入完整 private namespace：frame analysis、director plan、review、structured corrections、candidate storyboard。
- `_scrub_private_storyboard_ids` 使用 token boundary 精确替换，不做任意自然语言 substring 删除。
- formatter 测试证明 `analysisid/directorid/reviewid/correctionid/candidateid` 全部被移除，同时 `ordinary subject motion remains visible` 保留。
- polling 测试证明 prose-only 纯字母 `privatewindow` 不进入最终公开 `storyboard_text`。

## Finding 8：payoff readability allocation 测试隔离变量并断言方向

### RED

原测试同时改变 `visible_evidence` 项数和文本，不能证明 allocation 变化来自 readability 文本，且只断言“不相等”时方向不明确。

### GREEN / 实现证据

`test_mock_payoff_budget_changes_with_payoff_readability_evidence`：

- 固定 `visible_evidence` 项数，显式断言新旧长度相等；
- 仅替换等数量的 evidence 文本，其他分析输入保持不变；
- 明确断言 readable plan 的 payoff 秒数 **大于** base plan，而不是仅断言不相等。

## 最终验证命令与结果

### Focused

```powershell
& 'C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe' -m pytest tests/test_storyboard_director_coverage_service.py tests/test_frame_anchored_storyboard_provider.py tests/test_external_ai_generation.py -q
```

结果：`162 passed, 1 warning in 18.40s`

### Ruff

```powershell
& 'C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe' -m ruff check backend tests
```

结果：`All checks passed!`

### Full pytest

```powershell
& 'C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe' -m pytest
```

结果：`668 passed, 1 warning in 102.45s`

### Whitespace

```powershell
git diff --check
```

结果：通过，无输出。

### 唯一 warning

现有 FastAPI/Starlette `TestClient` 的 `httpx2` deprecation warning；本修复未引入新 warning。

## Scope / Safety

- runtime 改动仅限：
  - `backend/app/integrations/llm/mock_provider.py`
  - `backend/app/integrations/llm/openai_provider.py`
  - `backend/app/schemas/ai.py`
  - `backend/app/services/external_ai_generation_service.py`
  - `backend/app/services/storyboard_director_coverage_service.py`
- 测试改动仅限三份指定 focused test 文件。
- public Storyboard V2 POST/polling schema、route 和外部 `storyboard_text` contract 未改变。
- Redis/Celery、queue/worker、video provider、frontend、migration/Alembic 均无改动。
- 正常未缓存成功路径仍严格 3 次 LLM；没有第 4 次调用。
- 未加入固定高潮秒数、固定阶段比例、特定人物/品牌/文字/产品/Logo/IP/武器/钻石/奖励或场景规则。
- 未恢复 `FINAL_TEXT_OVERLAY_LOCKS`。
- private IDs 不公开，普通自然语言保留。
- 未修改或暂存 `AGENTS.md`、`.env*`、凭证、token、key 或密码。
- 未执行 reset/rebase/merge/push。

## 未解决项 / Concerns

- 8 项 review finding 均已关闭，无功能性未解决项。
- 仅保留仓库现有的 Starlette/httpx deprecation warning。
- 本任务按要求只提交本 worktree 变更，不 push、不部署、不变更运行环境。
