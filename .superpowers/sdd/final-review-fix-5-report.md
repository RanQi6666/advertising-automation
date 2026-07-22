# Storyboard V2 Final Fix 5 Report

## 基本信息

- 日期：2026-07-22
- 工作区：`C:\Users\panda\Documents\Advertising Automation\.worktrees\storyboard-v2-dynamic-action-arc`
- 起始 HEAD：`20744d04558ab07167a4b15de3929928d54bd257`
- 第五轮提交：`38f469d1822af71ed72f602a3f0a3d7f07ff768f`
- 审查输入：
  - `.superpowers/sdd/final-fix-4-review.md`
  - `.superpowers/sdd/review-final-fix-4-bb6254e-20744d0.diff`
  - `docs/superpowers/specs/2026-07-22-storyboard-v2-dynamic-action-arc-design.md`
  - `docs/superpowers/plans/2026-07-22-storyboard-v2-dynamic-action-arc-implementation.md`
  - 当时的 schema、coverage validator、external generation orchestration/formatter 与 focused tests
- 说明：本文件根据第五轮提交 diff、测试记录和随后独立审查结论，以 UTF-8 重新整理；原提交中的中文曾被永久写成 `?`。

## Finding 1（Important）：execution predicate 按局部 actor 与局部否定判断

### RED

通过完整 `validate_final_storyboard_action_coverage()` 增加回归：

- 接受：`The camera moves and Alex advances.`
- 拒绝：`The warrior does not move but the camera advances.`
- 拒绝：`The subject does not move and particles transform.`

旧实现把整句否定范围或首个 actor 错误复用到后续 predicate，导致主体肯定动作被误拒，或 camera/effect 动作被误算成主体执行。与 Finding 2 的目标组合首次运行记录为 `7 failed, 17 passed, 54 deselected`。

### GREEN

- `backend/app/services/storyboard_director_coverage_service.py`
  - 按 clause 收集 execution predicate，并为每个 predicate 单独解析前置 actor 与否定范围。
  - 将 `and`、`but`、`then` 作为局部边界，避免前一 predicate 的 actor/否定无条件污染后一 predicate。
  - 保持 camera、effect、particle、composition 等 support-only actor 不能替代 subject/state execution。
- 新增正负回归后，原始三例按预期通过完整 final validator。

### 第六轮后续审查说明

第五轮独立审查随后发现 actor 仍只取最后 token，且 `or` 未纳入协调边界，因此第五轮在以下扩展示例上仍有缺口：

- `The camera moves and Alex near the camera advances.`
- `The subject does not move and the particle field transforms.`
- `The camera does not move or Alex advances.`

这些缺口由第六轮单独修复，不冒充第五轮已完成内容。

## Finding 2（Important）：omit category/reason/evidence 拒绝反转语义

### RED

增加 schema 与 coverage review 回归，要求拒绝：

- `mechanism_unavailable` 搭配 `The mechanism is not unavailable; only the final pose differs.`
- `causal_equivalent_unavailable` 搭配 `No causal equivalent is unavailable; only ending framing differs.`

同时保留合法的 literal mechanism unavailable + causal equivalent unavailable 双重不可行，并继续验证 director prompt 的 enum/字段约束。目标组合首次运行同样记录在 `7 failed, 17 passed, 54 deselected` 中。

### GREEN

- `backend/app/schemas/ai.py`
  - 将 reason/evidence 按局部 clause 检查，不再只靠全文命中类别关键词。
  - 拒绝 `not/never ... unavailable|impossible|absent|infeasible|lacking` 等否定不可行状态。
  - 拒绝 `No ... is/are/was/were/remains/seems ... unavailable|...` 这类实际否定“不可用”的包装。
  - reason 与 evidence 两侧都必须存在与 category 一致的肯定不可行事实。
- `backend/app/services/storyboard_director_coverage_service.py`
  - `_valid_omission()` 继续复用相同 category/evidence 规则，防御 `model_copy()` 绕过 schema validation 的内部对象。
- 合法双重不可行与既有 prompt enum contract 保持通过。

### 第六轮后续审查说明

第五轮独立审查随后发现 contractions、`missing` 反转和 `not only/not merely` 肯定强调仍未统一处理；这些语义缺口由第六轮单独修复。

## Finding 3（Important）：private ID 全字段统一映射到 opaque namespace

### RED

增加 Call 3、polling 与 formatter 回归，覆盖：

- 带空格 ID：`camera action`
- 首尾空白及关联引用：` action `、` camera action `
- 普通词 ID：`subject`
- safe/opaque 形式：`camera-action`
- 最终自然语言：`The camera follows the action while the subject moves.`

旧实现可能只规范化声明字段而遗漏引用字段，或在公开 formatter 中按普通词全局替换，破坏自然语言。目标组合初次记录为 `2 failed, 46 deselected`。

### GREEN

- `backend/app/services/external_ai_generation_service.py`
  - behavior、beat、window、moment 使用各自 namespaced opaque ID。
  - mapping key 与 reference lookup 统一处理首尾空白。
  - 同步映射 reference behavior graph、timeline plan、director beats/windows/moments、`depends_on`、source IDs 与 `assigned_beat_id`。
  - Call 3、metadata、final validator 和 polling 使用一致的规范化引用。
  - 公开 formatter 只 scrub 安全的 `__sbv2_*__` private-ID 形式，不再替换 `camera`、`action`、`subject` 等普通 prose。
- 回归确认公开 `storyboard_text` 不泄露 private ID，并保留自然语言原句。

## Finding 4（Important）：reserved collision 避让与 normalization 后重新验证

### RED

增加 collision 与 revalidation 回归：

- 同一 beat namespace 已存在 `__sbv2_beat_001__` 时，普通 ID `action` 不得再映射到 `001`。
- 通过 `model_copy()` 构造断链或不合法对象时，normalization 后必须重新触发 Pydantic/contract validation。

旧 generator 从固定 `001` 起步且未先收集保留 ID，可能发生映射碰撞；仅重写对象而不重新验证也可能让重复 ID 或断链对象进入后续流程。目标组合初次记录为 `2 failed, 46 deselected`。

### GREEN

- 每个 namespace 先收集已有 safe/reserved IDs，再分配新 opaque IDs。
- collision 场景中：
  - 已有 `__sbv2_beat_001__` 保持不变。
  - 普通 `action` 避让并映射到 `__sbv2_beat_002__`。
- normalization 后通过 `FrameAnalysis.model_validate(normalized.model_dump(mode="python"))` 重新执行完整验证。
- normalization `ValidationError` 保持内部 contract failure 边界；invalid omit 仍由 coverage review 输出脱敏的 `invalid omission contract`，并在 Call 2 后停止。

## 当时确认保持的 clean 行为

- exact moment linkage 继续要求同一 moment/source 的 preparation、execution、payoff、return 顺序和局部关联。
- window overlap 使用严格区间相交；相邻边界不算 overlap。
- 动态压缩继续随 duration 与 evidence complexity 调整，未恢复固定秒数或固定比例。
- invalid omit 在 Call 2 后停止，总计 2 calls。
- 正常未缓存成功严格 3 calls，未增加第 4 次模型调用。
- 错误分类保持脱敏，公开错误不泄露 private IDs。
- public Storyboard V2 request/response、polling shape 与 `storyboard_text` contract 未修改。
- Redis/Celery/worker、video provider、frontend、migration 未修改。
- 未恢复 `FINAL_TEXT_OVERLAY_LOCKS`，未加入具体创意硬编码。
- 未修改、暂存或提交 `AGENTS.md`、`.env*` 或凭证。
- 未执行 reset、rebase、merge 或 push。

## 第五轮历史验证记录

### Focused 三文件

```powershell
& 'C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe' -m pytest tests/test_storyboard_director_coverage_service.py tests/test_frame_anchored_storyboard_provider.py tests/test_external_ai_generation.py -q
```

第五轮记录：`181 passed, 1 warning`。

### Ruff

```powershell
& 'C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe' -m ruff check backend tests
```

第五轮记录：`All checks passed!`

### Full pytest

```powershell
& 'C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe' -m pytest
```

第五轮记录：`687 passed, 1 warning`。

### Whitespace 与范围

- `git diff --check`：第五轮记录为通过，无 whitespace error。
- 第五轮 runtime 范围：schema、external generation service、coverage service。
- 第五轮 test 范围：external generation 与 coverage service tests。
- 唯一 warning 为既有 FastAPI/Starlette `TestClient` 对 `httpx2` 的 deprecation warning。

## 第五轮结论与遗留

- private ID 全字段映射、reserved collision 避让和 normalization 后 revalidate：clean。
- exact linkage、window overlap、动态压缩、调用次数、错误分类与 public/runtime scope：clean。
- 遗留到第六轮：execution actor phrase/`or` scope、omit contractions/polarity/emphasis，以及本报告 UTF-8 恢复。