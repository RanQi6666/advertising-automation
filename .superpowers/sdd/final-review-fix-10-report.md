# Storyboard V2 第十轮私有 claim ID 替换修复报告

## 基本信息

- 日期：2026-07-22
- 工作区：`C:\Users\panda\Documents\Advertising Automation\.worktrees\storyboard-v2-dynamic-action-arc`
- 分支：`codex/storyboard-v2-dynamic-action-arc`
- 起始 HEAD：`5448d3dd89b637f8036ecdcadf41773a8f6e6414`
- 审查输入：`.superpowers/sdd/final-fix-9-review.md`
- 范围：只修复第九轮审查中的两个 Important finding：Unicode 相邻 private ID 泄漏，以及前缀重叠 alias 的顺序式替换破坏。

## TDD 记录

### RED

先只增加回归测试，没有修改生产代码。运行新增 6 个参数化 case 后，真实结果：

```text
6 failed, 1 warning in 6.30s
```

失败直接复现了两个 finding：

- 结构化 `primary-claim-extra` 被短 alias 先替换成非法的 `__sbv2_claim_001__-extra`；
- 中文和日文紧邻的 canonical `__sbv2_claim_001__` 仍出现在 formatter 输出；
- 完整服务与 polling 中，中文/日文相邻的 raw alias 没有被规范化和清除。

最小实现后的第一次回归为 `2 failed, 4 passed`。剩余两项不是生产缺陷，而是测试用 `raw_claim_id not in storyboard_text` 同时错误命中了必须保留的普通自然语言 `primary-claimant`。随后把断言收紧为检查注入的完整上下文 token（例如 `中primary-claim文`），继续保留并断言 `primary-claimant` 原样存在。

### GREEN

收紧测试断言后，同一组新增用例真实结果：

```text
6 passed, 1 warning in 5.33s
```

## 最小实现与设计选择

生产代码仅修改 `backend/app/services/external_ai_generation_service.py`：

1. 新增统一的私有 ID 文本替换 helper，raw normalization 与 formatter canonical scrub 共用同一策略。
2. 结构化字典中的 `claim_id` 优先执行 strip 后的 exact dictionary lookup，不再使用全文 regex 改写。
3. 可渲染 prose 和其他字符串只执行一次 regex alternation：
   - alias 按长度降序排列，保证 `primary-claim-extra`、`phase:claim.extra` 等最长 token 优先；
   - 每个 alias 使用 `re.escape`；
   - 边界只排除 ASCII identifier 字符 `[A-Za-z0-9_]`，不再使用 Unicode-aware `\w`，因此中文、日文等 Unicode 字母紧邻时仍能匹配完整 private ID；
   - callback 直接根据原始 match 查表，替换结果不会进入后续 alias 替换，避免 `first-id -> second-id -> third-id` 级联。
4. formatter scrub 将已知 private IDs 一次性映射为 `linked item`，与 raw alias normalization 使用完全相同的最长匹配和 ASCII 边界逻辑。
5. reserved canonical collision、private namespace 编号避让、`FrameAnchoredStoryboard.model_validate(...)` 完整 revalidation、claim binding/assertion consistency 和 final execution gate 均保持不变。

## 新增覆盖

- raw alias 紧邻中文进入 visual、motion、action-result、effect 等可渲染字段，并经过完整 service、formatter 和 polling 后不泄漏；
- canonical claim ID 紧邻中文和日文，经 formatter 与 polling 后不泄漏；
- `primary-claim` / `primary-claim-extra` 在两种 evidence 顺序下均稳定 normalization 与 revalidation；
- `phase:claim` / `phase:claim.extra` 在两种 evidence 顺序下均稳定 normalization 与 revalidation；
- 替换结果不级联；
- `primary-claimant` 这种包含相似子串但不是完整 private ID 的普通自然语言不会被误删；
- 既有 reserved canonical collision 和完整 private namespace 回归继续通过。

## 验证结果

### 新增回归

```text
6 passed, 1 warning in 5.33s
```

### Focused

覆盖 `tests/test_storyboard_director_coverage_service.py`、`tests/test_frame_anchored_storyboard_provider.py`、`tests/test_external_ai_generation.py`：

```text
170 passed, 1 warning in 21.34s
```

### Ruff

检查范围为 `backend tests`：

```text
All checks passed!
```

### Full pytest

```text
676 passed, 1 warning in 113.40s
```

### 严格调用次数专项

```text
2 passed, 1 warning in 3.44s
```

正常未缓存成功仍严格 3 次 LLM；invalid omit 仍严格在 Call 2 后停止；未增加第 4 次模型审片。

### 静态与范围检查

- `git diff --check` 通过；
- 生产变更仅位于 `backend/app/services/external_ai_generation_service.py`；
- 测试变更仅位于 `tests/test_external_ai_generation.py`；
- 文档变更仅为本报告和 `.superpowers/sdd/progress.md`；
- public Storyboard V2 POST/schema/polling 与 `storyboard_text` 契约未修改；
- Redis、Celery、queue、worker、generation task、video provider、frontend、migration 均未修改；
- 未恢复 `FINAL_TEXT_OVERLAY_LOCKS`，未引入 `sword`、`diamond`、`x200,000` 等创意硬编码；
- 提交范围不包含 `AGENTS.md`、`.env*`、凭证或敏感配置；
- 临时 RED 调试文件 `.superpowers/sdd/.fix10-red-debug.txt` 已删除且不纳入提交。

## 保持不变的契约与门禁

- public Storyboard V2 请求、异步 job polling 和公开 `storyboard_text` 形状不变；
- private claim namespace 仍只保存在内部 candidate/metadata，公开 formatter 只输出清理后的自然语言；
- claim binding、assertion consistency、exact signature/source linkage、phase order 和 final execution gate 未弱化；
- reserved canonical claim ID 仍参与 collision avoidance，不会被重新编号覆盖；
- 正常成功 3 次 LLM、invalid omit 2 次 LLM 的调用预算不变。

## Concern

唯一观察到的 warning 是既有 Starlette/FastAPI `TestClient` 对 `httpx2` 的 deprecation warning；它在 focused、full 和调用次数专项中各出现一次，与本轮替换逻辑无关。本轮没有其他已知 concern。
