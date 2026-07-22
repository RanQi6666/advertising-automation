# Storyboard V2 Final Fix 7 Report

## 基本信息

- 日期：2026-07-22
- 工作区：`C:\Users\panda\Documents\Advertising Automation\.worktrees\storyboard-v2-dynamic-action-arc`
- 起始 HEAD：`4eb10d7dfcceb0998189ed6ca0ec3c3f44754203`
- 分支：`codex/storyboard-v2-dynamic-action-arc`
- 审查输入：
  - `.superpowers/sdd/final-fix-6-review.md`
  - `.superpowers/sdd/review-final-fix-6-38f469d-4eb10d7.diff`
  - `.superpowers/sdd/final-review-fix-6-report.md`
  - `docs/superpowers/specs/2026-07-22-storyboard-v2-dynamic-action-arc-design.md`
  - `docs/superpowers/plans/2026-07-22-storyboard-v2-dynamic-action-arc-implementation.md`
  - 当前 schema、coverage validator 与相关测试
- 结论：第六轮审查的 4 个 Important finding 均已按 TDD 修复；此前 clean 的 private-ID、exact linkage、strict overlap、动态压缩、调用次数和公开合同范围未回归。

## TDD 记录

### 首次 RED

先新增四组完整回归：

1. coordinated subject 的 `and/or` 正反顺序。
2. actor member head/entity 分类与纯 support-only 对照。
3. endpoint-scoped omit 在 literal/equivalent、schema `model_validate()` 与 `model_copy()` review 两条路径的拒绝矩阵。
4. identity conflict 与 unavailable 类别的局部 polarity/scope 正反例。

首次目标测试结果：

```text
25 failed, 31 passed, 68 deselected
```

失败与四个审查 finding 一致。

### 补充 RED

GREEN 审查时发现简单去除复数 `s` 会把 `lens` 归一成 `len`，先补充回归：

- `The lens moves.` 必须继续拒绝。
- `The camera operators advance.` 必须接受。

补充测试先得到：

```text
1 failed, 33 passed, 100 deselected
```

修复后 actor 目标集：

```text
34 passed, 100 deselected
```

endpoint、polarity 与独立全局正例目标集：

```text
32 passed, 100 deselected
```

## Finding 1（Important）：识别完整 coordinated subject

- 首个 execution predicate 前的 `and/or` 不再无条件作为 predicate 边界。
- 仅当 coordinator 前已经存在 predicate 证据时，才把它作为后续 predicate 的局部边界。
- 首个 predicate 使用完整 coordinated subject；各成员分别分类，只有所有成员均为 support-only actor 才拒绝。
- 已覆盖并接受：
  - `Alex and the camera advance.`
  - `The camera and Alex advance.`
  - `Alex or the camera advances.`
  - `The camera or Alex advances.`
- 既有局部否定继承规则保持：共享 actor 的 `and/or` 可继承前一谓词否定，`but/then` 不继承；显式新 actor 使用自身局部 scope。

## Finding 2（Important）：按 coordinated actor member 的 head/entity 分类

- support-only 判断不再因 actor phrase 中任一修饰 token 命中 `camera/lighting/particle` 就拒绝整段。
- 每个 coordinated member 先提取 actor core，再按 head/entity category 判断：
  - 人物、代理或职业实体 head（如 `operator`、`technician`、`researcher`）为真实 actor。
  - camera、particles、lighting、effect 及其 field/rig/array/cloud/layer/system 等 support compound head 保持 support-only。
- 通用职业 head 集并非完整句子特例。
- 已覆盖并接受：`camera operator`、`lighting technician`、`particle researcher`。
- 已覆盖并拒绝：`camera advances`、`lens moves`、`particles transform`。
- 复数匹配仅在 token 本身不已匹配时尝试去除尾部 `s`，避免 `lens` 被错误归一。

## Finding 3（Important）：拒绝非 endpoint category 的局部 endpoint-only omit

- 新增 endpoint scope 识别，覆盖 `final pose`、`last frame`、`endpoint`、`ending frame`，并扩展到 final/last/ending state、composition 等同类 endpoint 表达。
- 若 unavailable/absent/missing/conflict 等不可行事实仅通过 `in/at/from/during/on/within/for` 限定在 endpoint，则不能证明非 endpoint category 的全局不可行。
- literal 与 equivalent 两侧均使用同一事实门禁。
- schema `model_validate()` 会直接拒绝；通过 `model_copy()` 绕过 schema 的对象仍会在 `review_director_action_coverage()` 中标记为 unrecoverable。
- 存在独立全局机制、能力、身份冲突或 causal-equivalent 不可行事实时，附带 endpoint 局部描述不会使合法 omit 失效。

## Finding 4（Important）：统一类别上下文与局部 polarity/scope

- unavailable 类别统一拒绝反向极性：`far from`、`by no means`、`anything but`、`hardly`、`scarcely` 等。
- identity conflict 类别统一拒绝：
  - `no identity conflict`
  - `conflict is absent/missing/nonexistent/resolved`
  - `compatible with no conflict`
  - 既有 `not/never/without ... conflict` 反向形式
- 保持 `not only unavailable—it is absent` 与 `not merely unavailable—it is absent` 为肯定强调：归一化先把强调结构转换为肯定语义，再执行局部事实门禁。
- 实现以 category context、clause-local polarity 和 endpoint scope 组合判断，不使用具体句子硬编码。

## 已确认保持的 clean 行为

- private ID mapping、reserved collision 与 normalization 后完整 revalidate 不变。
- exact moment linkage 与 preparation/execution/payoff/return 局部关联不变。
- strict interval overlap 不变，相邻边界仍不算 overlap。
- 动态压缩继续依据 duration、evidence complexity 与窗口预算。
- invalid omit 仍在 Call 2 后停止，总计 2 calls。
- 正常未缓存成功仍严格 3 calls。
- UTF-8 报告、public request/response/polling contract、queue、worker、provider、frontend、migration 均未改变。
- 未恢复 `FINAL_TEXT_OVERLAY_LOCKS`，未加入 `sword|diamond|x200,000` 等具体创意硬编码。
- 未修改 `.env*`、`AGENTS.md`、凭证或部署配置。

## 最终验证

### Focused 三文件

```powershell
& 'C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe' -m pytest tests/test_storyboard_director_coverage_service.py tests/test_frame_anchored_storyboard_provider.py tests/test_external_ai_generation.py -q
```

结果：

```text
237 passed, 1 warning in 18.27s
```

### Ruff

```powershell
& 'C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe' -m ruff check backend tests
```

结果：

```text
All checks passed!
```

### Full pytest

```powershell
& 'C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe' -m pytest
```

结果：

```text
743 passed, 1 warning in 98.23s
```

唯一 warning 为项目既有 FastAPI/Starlette `TestClient` 对 `httpx2` 的 deprecation warning。

### Whitespace、scope、编码与 forbidden checks

提交前执行：

```powershell
git diff --check
git diff --name-only 4eb10d7
git grep -n -E "FINAL_TEXT_OVERLAY_LOCKS|sword|diamond|x200,000" -- backend/app/schemas/ai.py backend/app/services/storyboard_director_coverage_service.py backend/app/services/external_ai_generation_service.py
```

并以 Python 严格 UTF-8 解码检查本报告的 BOM、U+FFFD、连续问号与中文字符数量。

## Scope

### Runtime

- `backend/app/schemas/ai.py`
- `backend/app/services/storyboard_director_coverage_service.py`

### Tests

- `tests/test_storyboard_director_coverage_service.py`

### Report

- `.superpowers/sdd/final-review-fix-7-report.md`

### 明确未修改

- `backend/app/services/external_ai_generation_service.py`
- provider、prompt、public route/request/response/polling contract
- Redis、Celery、queue、worker、video provider
- frontend、migration、database schema
- `.env*`、`AGENTS.md`、凭证或部署配置

## Concerns

- 功能 finding：无已知未解决项。
- 非阻塞 warning：仅有项目既存的 FastAPI/Starlette `TestClient` 对 `httpx2` 的 deprecation warning。
- parser 与 omit fact gate 仍是有意限制范围的英文句法/语义启发式，不是通用 NLP；本轮仅强化审查要求的 coordinated subject、actor head、endpoint scope 与 polarity scope，未扩大公开合同或增加模型调用。
- 测试新增矩阵较长，但分别覆盖 schema、`model_copy()` review、literal/equivalent 和正负行为，保留了审查可追溯性。
- 未执行 reset、rebase、merge 或 push。