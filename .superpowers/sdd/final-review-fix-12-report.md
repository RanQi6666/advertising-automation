# Storyboard V2 最终审查 Important #1/#2 修复报告

- 日期：2026-07-23
- 工作区：`C:\Users\panda\Documents\Advertising Automation\.worktrees\storyboard-v2-dynamic-action-arc`
- 分支：`codex/storyboard-v2-dynamic-action-arc`
- 基线 HEAD：`1fd6798be5f5f7a364fda1f8ec48083daa94c0d7`
- 修复范围：仅最终审查 Important #1 与 #2

## 结论

本次修复将每个非 omit signature moment 的通过证据收紧为同一合法 scene 内的完整绑定：当前 `moment_id`、该 moment 的全部 required source behavior IDs、`assigned_beat_id`、affirmed target execution evidence，以及与动态 director beat 时间窗口的严格正区间重叠。全局 source union 仅保留为补充诊断，不再替代 per-moment 完整覆盖。

## 生产代码改动

### A. per-moment exact source linkage

- `_matching_target_execution_evidence()` 由 source 任意交集改为当前 moment required source set 的完整子集校验。
- `_scene_links_moment()` 要求 scene 同时包含当前 `moment_id` 和该 moment 的完整 required source set。
- preparation/action/payoff/return phase validator 改为使用当前 moment 的 `referenced_required_ids`，不再借用全局 source union。
- execution evidence 继续要求 target executor、`affirmed` assertion、非空 action/state change，并保留 claim binding/assertion consistency 的 fail-closed 校验。
- 合法联合 scene 可同时携带多个 moment、完整 source sets 和 director beats；不会因为联合执行而被粗粒度拒绝。

### B. assigned beat 与时间严格绑定

- 新增动态 beat 时间窗严格重叠判断：`max(scene_start, beat_start) < min(scene_end, beat_end)`。
- 所有显式或自动分配到 scene 的合法 director beat IDs，在 `_assign_missing_director_beat_ids()` 完成后统一校验。
- 仅边界接触为零长度 overlap，按预期拒绝。
- 自动分配只从与对应 beat 窗口存在严格正 overlap 的 scene 中选择，不新增固定秒数或固定比例。
- 每个非 omit signature moment 的有效 execution scene 必须同时携带该 moment 的 `assigned_beat_id`，并与该 beat 的动态窗口严格重叠。
- beat ID 在另一 scene、execution 在当前 scene 的拆分证据不能通过。

## TDD 记录

### RED

先新增 7 个回归测试并在生产修复前运行，真实结果：

```text
5 failed, 2 passed
```

真实失败覆盖：

1. moment A 需要 source A+B，但 evidence 只有 A，moment B 的 B 全局覆盖掩盖缺失。
2. moment A 借用只绑定 moment B/source B 的 preparation/payoff/return scene。
3. execution evidence 合法，但 execution scene 不包含该 moment 的 assigned beat。
4. director beat ID 只位于与 beat 窗口不重叠的 scene。
5. scene 与 beat 窗口仅边界零长度接触。

RED 阶段已经通过的 2 个测试证明：合法联合 scene 和既有合法显式/自动 beat 分配未被测试夹具误拒。

### GREEN

完成最小生产修复后，同一组 7 个测试结果：

```text
7 passed in 1.20s
```

## 2026-07-23 提交前 fresh 验证

### Focused 三文件

命令：

```text
python -m pytest tests/test_storyboard_director_coverage_service.py tests/test_frame_anchored_storyboard_provider.py tests/test_external_ai_generation.py -q
```

结果：

```text
214 passed, 1 warning in 22.10s
```

### Ruff

命令：

```text
python -m ruff check backend tests
```

结果：

```text
All checks passed!
```

### Full pytest

命令：

```text
python -m pytest -q
```

结果：

```text
720 passed, 1 warning in 107.88s
```

### 3/2 calls 与 private ID 专项

覆盖：

- uncached success 严格 3 次 LLM 调用；
- invalid omit 严格停在 Call 2；
- formatter 不输出注册的 private IDs；
- polling 对注册 private ID 执行安全 scrub，同时保留不应被误删的未注册更长 token。

结果：

```text
4 passed, 1 warning in 4.08s
```

## 静态与范围检查

- `git diff --cached --check` 通过。
- 6 个提交文件均通过严格 UTF-8 解码检查，且不含 Unicode replacement character。
- staged 文件不含 `AGENTS.md`、`.env*`、凭证或无关文件。
- changed production added lines 不含 `FINAL_TEXT_OVERLAY_LOCKS`、sword、diamond、`x200,000` 或 `200,000` 创意硬编码。
- 未修改 public Storyboard V2 POST/schema/polling/storyboard_text。
- 未修改 Redis/Celery/queue/worker、generation task、video provider、frontend 或 migration。
- 未处理最终审查 Important #3-#7 或 Minor。
- 未执行 merge、rebase 或 push。

## 修改文件

- `backend/app/services/storyboard_director_coverage_service.py`
- `backend/app/schemas/ai.py`
- `tests/test_storyboard_director_coverage_service.py`
- `tests/test_frame_anchored_storyboard_provider.py`
- `.superpowers/sdd/progress.md`
- `.superpowers/sdd/final-review-fix-12-report.md`

## Concern

唯一 warning 是既有 Starlette `httpx`/`httpx2` deprecation warning，不是本次改动引入。严格 interval overlap 是有意的 fail-closed 行为：beat ID 与 scene 只有边界接触，或 scene 缺少可验证时间区间时，storyboard 会被拒绝。