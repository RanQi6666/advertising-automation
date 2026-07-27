# Storyboard V2 第十一轮 private ID token 边界修复报告

## 基本信息

- 日期：2026-07-23
- 工作区：`C:\Users\panda\Documents\Advertising Automation\.worktrees\storyboard-v2-dynamic-action-arc`
- 分支：`codex/storyboard-v2-dynamic-action-arc`
- 起始 HEAD：`486bae8d4a17a12b07a9f75b6814253e3068bf85`
- 审查输入：`.superpowers/sdd/final-fix-10-review.md`
- 范围：只修复审查报告唯一 Important，即已知短 alias 在 `-`、`:`、`.`、`_` 或混合 separator 连接的未登记更长 private-ID-shaped token 中被局部替换。

## 根因

`_replace_private_ids_in_text` 原先只用 `[A-Za-z0-9_]` 判断左右边界。该规则能保护直接相邻的 ASCII 字母、数字、下划线，却把 `-`、`:`、`.` 无条件当成 token 边界，因此 mapping 仅登记 `primary-claim` 时会错误改写：

- `primary-claim-extra`、`primary-claim:extra`、`primary-claim.extra`；
- `prefix-primary-claim`、`prefix:primary-claim`、`prefix.primary-claim`；
- canonical `__sbv2_claim_001__` 在同类前后缀中的局部子串。

这与 `_SAFE_PRIVATE_ID_PATTERN` 的 generic grammar 不一致；separator 在两侧连接 ASCII alphanumeric segment 时，应被视为更长完整 token 的组成部分，而不是自然语言标点。

## TDD 记录

### RED

先只修改 `tests/test_external_ai_generation.py`，未修改生产代码。新增 raw、canonical 和完整 service + polling 三组回归后运行：

```text
14 failed, 23 passed, 1 warning in 4.30s
```

失败精确复现：

- raw 短 alias 在 `-`、`:`、`.` 前后缀中被局部替换；
- canonical ID 在相同连接方式中被局部 scrub；
- 完整 service 与 polling 输出未保留未登记更长 token。

`_` 连接、Unicode 邻接、括号/逗号/分号/句号、字符串开头/结尾等既有正确行为在 RED 中继续通过，证明失败集中于审查指出的 separator token 边界。

### GREEN

生产代码只对 `_replace_private_ids_in_text` 的单次 regex 增加 grammar-aware 边界：

- 左侧拒绝直接 ASCII identifier 邻接，也拒绝 `[A-Za-z0-9][_:.-]` 形式的更长 token 前缀；
- 右侧拒绝直接 ASCII identifier 邻接，也拒绝 `[_:.-][A-Za-z0-9]` 形式的更长 token 后缀；
- separator 后为白空格、字符串结尾或自然语言标点时仍允许替换；
- Unicode 字母不属于 ASCII private ID segment，继续允许紧邻替换；
- longest-first alternation、single-pass callback 和无级联行为保持不变。

同一组新增回归随后通过：

```text
37 passed, 1 warning in 3.36s
```

## 新增覆盖

### Raw alias

mapping 仅登记 `primary-claim`，验证完整保留：

- `primary-claim-extra`
- `primary-claim:extra`
- `primary-claim.extra`
- `primary-claim_extra`
- `prefix-primary-claim`
- `prefix:primary-claim`
- `prefix.primary-claim`
- `prefix_primary-claim`
- `prefix.primary-claim_extra`

同时验证中文/日文 Unicode 邻接、括号、逗号、分号、中文句号、英文句号后空白/结尾，以及字符串开头/结尾仍正常替换。

### Canonical scrub

对 `__sbv2_claim_001__ -> linked item` 使用同一矩阵，验证 canonical ID 在更长 private-ID-shaped token 中完整保留，正常 Unicode 邻接与自然语言标点仍被 scrub。

### 完整 service + polling

新增真实 `ExternalAIGenerationService.execute_frame_anchored_video_storyboard(...)` 与公开 polling 回归，证明：

- 未登记的 raw/canonical 更长 token 在最终 `storyboard_text` 中原样保留；
- 真正登记的 `primary-claim` 被规范化为 canonical claim ID；
- Unicode 邻接的已知 raw/canonical private ID 在公开文本中被清理为 `linked item`；
- 内部 candidate 可重新通过 `FrameAnchoredStoryboard.model_validate(...)`；
- 成功路径仍严格调用 analyze、director、storyboard 三次 LLM。

## 验证结果

### Focused 三文件

```text
207 passed, 1 warning in 22.20s
```

覆盖：

- `tests/test_storyboard_director_coverage_service.py`
- `tests/test_frame_anchored_storyboard_provider.py`
- `tests/test_external_ai_generation.py`

### Ruff

```text
All checks passed!
```

检查范围：`backend tests`。

### Full pytest

```text
713 passed, 1 warning in 119.79s
```

### 严格 3/2 调用次数专项

```text
2 passed, 1 warning in 3.41s
```

- 正常未缓存成功严格 3 次 LLM；
- invalid omit 严格在 Call 2 后停止；
- 未增加第 4 次审片。

### 静态、范围与敏感检查

- `git diff --check` 通过；
- public Storyboard V2 POST、schema、polling 和 `storyboard_text` 契约文件未修改；
- Redis、Celery、queue、worker、generation task、video provider、frontend、migration 未修改；
- 未恢复 `FINAL_TEXT_OVERLAY_LOCKS`；
- 未引入 `sword`、`diamond`、`x200,000`；
- 未修改或纳入 `AGENTS.md`、`.env*`、凭证、私钥或敏感配置；
- 生产改动仅为 `backend/app/services/external_ai_generation_service.py` 的 private ID 文本边界；
- 测试改动仅为 `tests/test_external_ai_generation.py`；
- 文档改动仅为本报告和 `.superpowers/sdd/progress.md`。

## 保持不变的门禁

- 结构化 `claim_id` 继续 exact dictionary lookup；
- 已登记完整长 alias 继续 longest-first；
- 替换继续 single-pass、无级联；
- reserved canonical collision avoidance 与 namespace normalization 不变；
- 完整 storyboard revalidation、claim consistency、final execution gate 不回退；
- public API、异步任务和 provider 调用预算不变。

## Concern

唯一观察到的 warning 是既有 Starlette/FastAPI `TestClient` 对 `httpx2` 的 deprecation warning；它与本轮 private ID token 边界修复无关。本轮没有其他已知 concern。
