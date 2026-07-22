# Storyboard V2 Final Fix 6 Report

## 基本信息

- 日期：2026-07-22
- 工作区：`C:\Users\panda\Documents\Advertising Automation\.worktrees\storyboard-v2-dynamic-action-arc`
- 起始 HEAD：`38f469d1822af71ed72f602a3f0a3d7f07ff768f`
- 分支：`codex/storyboard-v2-dynamic-action-arc`
- 审查输入：
  - `.superpowers/sdd/final-fix-5-review.md`
  - `.superpowers/sdd/review-final-fix-5-20744d0-38f469d.diff`
  - `docs/superpowers/specs/2026-07-22-storyboard-v2-dynamic-action-arc-design.md`
  - `docs/superpowers/plans/2026-07-22-storyboard-v2-dynamic-action-arc-implementation.md`
  - 当前 schema、coverage validator、external generation orchestration/provider、相关 focused tests
- 结论：第五轮审查的 2 个 Important 与 1 个 Minor finding 均已按 TDD 处理；第五轮已 clean 的 private-ID、linkage、action-arc、调用次数、错误分类与公开合同范围未回归。

## Finding 1（Important）：execution parser 使用完整局部 subject phrase

### RED

先扩展完整 final-validator 回归，覆盖：

- 必须接受：`The camera moves and Alex near the camera advances.`
- 必须拒绝：`The subject does not move and the particle field transforms.`
- 必须接受：`The camera does not move or Alex advances.`
- 同时保留前几轮通用主体、静态主体、camera-only、effect-only、复合否定和共享谓词正负例。

与 Finding 2 一起运行目标选择集，RED 结果：

```text
15 failed, 24 passed, 54 deselected
```

失败原因与审查一致：旧逻辑把 predicate 前最后一个 token 当 actor，丢失 `Alex near the camera` 与 `particle field` 的完整主语信息；协调边界也未覆盖 `or`。

### GREEN

- `backend/app/services/storyboard_director_coverage_service.py`
  - 将协调边界统一为 `and / but / then / or`。
  - 每个 execution predicate 从自身协调片段提取完整局部 actor phrase，不再只取最后 token。
  - 对 `near/beside/behind/...` 等主语后修饰边界提取 actor core；`Alex near the camera` 的 actor core 为 `Alex`。
  - support-only 检查覆盖完整 actor core；`particle field` 中的 `particle` 能阻止 effect-only predicate 冒充主体执行。
  - 有显式新 actor 时，否定只使用本地 scope。
  - 无显式新 actor 的共享谓词中，`and/or` 可继承前一 predicate 的否定；`but/then` 不继承。
- 因而继续保持：
  - `The subject does not move or turn.` 拒绝。
  - `The subject does not remain still and moves forward.` 接受。
  - `The warrior does not move but the camera advances.` 拒绝。

修复后原目标选择集结果：

```text
39 passed, 54 deselected
```

完整 coverage test 文件最终结果：

```text
95 passed
```

## Finding 2（Important）：omit category/evidence 使用一致的事实极性检测

### RED

新增 schema 与 coverage-review 回归，要求拒绝：

- `The mechanism isn't unavailable; only the final pose differs.`
- `The mechanism can't be unavailable; only the final pose differs.`
- `The mechanism cannot be missing; only the final pose differs.`
- `No mechanism is missing; only the final pose differs.`
- `The causal equivalent isn't unavailable; only ending framing differs.`
- `No causal equivalent is missing; only ending framing differs.`

并要求接受肯定强调：

- `The target mechanism is not only unavailable—it is absent.`
- `The target mechanism is not merely unavailable—it is absent.`

上述用例包含在首次 `15 failed` 的 RED 中。

在 GREEN 审查阶段另补一条“不得靠宽泛 `no` 命中”的回归：

- `The mechanism has no endpoint mismatch; only the final pose differs.` 必须拒绝。

该补充测试先产生预期 RED：

```text
2 failed, 12 passed, 81 deselected
```

### GREEN

- `backend/app/schemas/ai.py`
  - 统一常见 contractions：`isn't/aren't/wasn't/weren't/can't/couldn't/won't/wouldn't/doesn't/don't/didn't/hasn't/haven't/hadn't`。
  - 将弯引号 apostrophe 统一为 ASCII apostrophe。
  - 将 `not only`、`not merely` 归一为肯定强调，不再误判为否定。
  - 每个 category 先要求同一局部 clause 具备目标上下文：target/subject、mechanism/articulation、identity/semantic、equivalent + causal role。
  - 再检查有限的肯定事实族：明确 negative state、`not available`、执行能力缺失、以及针对 capability/subject/state/mechanism/equivalent 等有限资源名词的 absence/lack 事实。
  - 先拒绝语义反转事实：`not unavailable`、`cannot be unavailable/missing`、`No ... is missing/unavailable`、`does not lack`。
  - bare `no`/`without` 不再作为任意肯定不可行证明；endpoint mismatch 文本即使包含 category 名词也不能通过。
- schema validator 与 coverage review 共用同一 `omission_infeasibility_matches_category()`，因此 `model_copy()` 绕过 schema 后仍会被 review 拦截。
- 合法 literal + equivalent 双重不可行继续通过。

补充收窄后的目标结果：

```text
20 passed, 75 deselected
```

## Finding 3（Minor）：恢复第五轮报告 UTF-8 中文

### 修复

- 以 UTF-8 无 BOM 重写 `.superpowers/sdd/final-review-fix-5-report.md`。
- 内容根据第五轮 commit diff、历史测试记录和第五轮独立审查结论重新整理。
- 明确区分第五轮已完成事项与后来审查发现、交由第六轮修复的遗留项，不把第六轮结果倒写成第五轮成果。

### 编码验证

Python UTF-8 解码检查：

```text
utf8=True
bom=False
question_runs=0
replacement=0
han=1021
lines=168
```

并用 PowerShell `Get-Content -Encoding UTF8` 人工抽查标题、基本信息和 Finding 中文段落，可读且未出现连续问号损坏。

本报告 `.superpowers/sdd/final-review-fix-6-report.md` 同样使用 UTF-8 无 BOM 写入，并在提交前执行相同检查与人工抽查。

## 已确认保持的 clean 行为

- private ID：所有 ID 字段与引用字段继续按字段身份统一映射。
- reserved collision：同 namespace 先收集保留 ID，新 opaque ID 继续避让碰撞。
- revalidate：normalization 后继续重新执行完整 `FrameAnalysis.model_validate(...)`。
- exact moment linkage：preparation/execution/payoff/return 仍要求同一 moment/source 的顺序和局部关联。
- window overlap：继续使用严格区间相交；相邻边界不算 overlap。
- 动态压缩：继续依据 duration、evidence complexity 与窗口预算调整，未恢复固定秒数或比例。
- invalid omit：仍在 Call 2 后停止，总计 2 calls。
- 正常未缓存成功：仍严格 3 calls，未增加第 4 次模型调用。
- 错误分类：invalid omission、linkage 与其他 director contract 继续使用既有脱敏分类，公开错误不泄露 private IDs。
- public contract：request/response、polling shape 与公开 `storyboard_text` 不变。
- 范围：未修改 external orchestration、provider、Redis/Celery/queue/worker、frontend 或 migration。
- 创意与常量：未恢复 `FINAL_TEXT_OVERLAY_LOCKS`，未加入 `sword|diamond|x200,000` 等具体创意硬编码。

## 最终验证

### Focused 三文件

```powershell
& 'C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe' -m pytest tests/test_storyboard_director_coverage_service.py tests/test_frame_anchored_storyboard_provider.py tests/test_external_ai_generation.py -q
```

结果：

```text
198 passed, 1 warning in 18.12s
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
704 passed, 1 warning in 123.96s
```

### Whitespace / scope / forbidden checks

```powershell
git diff --check
git diff --name-only 38f469d
git grep -n -E "FINAL_TEXT_OVERLAY_LOCKS|sword|diamond|x200,000" -- backend/app/schemas/ai.py backend/app/services/storyboard_director_coverage_service.py backend/app/services/external_ai_generation_service.py
```

结果：

- `git diff --check`：通过，无 whitespace error。
- forbidden scan：无匹配。
- 最终预期只提交 5 个文件：2 个 runtime、1 个 test、2 个 UTF-8 report。

## Scope

### Runtime

- `backend/app/schemas/ai.py`
- `backend/app/services/storyboard_director_coverage_service.py`

### Tests

- `tests/test_storyboard_director_coverage_service.py`

### Reports

- `.superpowers/sdd/final-review-fix-5-report.md`
- `.superpowers/sdd/final-review-fix-6-report.md`

### 明确未修改

- `backend/app/services/external_ai_generation_service.py`
- provider 与 prompt 实现
- public route/request/response/polling contract
- Redis、Celery、queue、worker
- video provider
- frontend
- migration/database schema
- `.env*`、`AGENTS.md`、凭证或部署配置

## Concerns

- 功能 finding：无已知未解决项。
- 非阻塞 warning：仅有项目既存的 FastAPI/Starlette `TestClient` 对 `httpx2` 的 deprecation warning，本轮未新增 warning。
- parser 与 omit fact gate 仍是有意限制范围的英文句法/语义启发式，而不是通用 NLP；本轮已覆盖审查要求、前几轮正负例及宽泛 `no` 反例，未扩大 public contract 或引入新模型调用。
- 未执行 reset、rebase、merge 或 push。