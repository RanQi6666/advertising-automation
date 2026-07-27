# Storyboard V2 第八轮结构化执行与省略重构报告

## 基本信息

- 日期：2026-07-22
- 工作区：`C:\Users\panda\Documents\Advertising Automation\.worktrees\storyboard-v2-dynamic-action-arc`
- 分支：`codex/storyboard-v2-dynamic-action-arc`
- 起始 HEAD：`d7298d2`
- 第八轮提交：`6d5688769d5c3b42e63d46b385c5dc05eece52d7`
- 目标：移除以自然语言启发式判断动作执行和省略有效性的门禁，改为私有结构化契约；保持公共 API、异步 polling、`storyboard_text`、队列和下游视频生成契约不变。

## 修改范围

生产代码：

- `backend/app/schemas/ai.py`
- `backend/app/services/storyboard_director_coverage_service.py`
- `backend/app/integrations/llm/openai_provider.py`
- `backend/app/integrations/llm/mock_provider.py`

测试代码：

- `tests/test_storyboard_director_coverage_service.py`
- `tests/test_frame_anchored_storyboard_provider.py`
- `tests/test_external_ai_generation.py`

本轮没有修改 public route、外部请求或 polling schema、Redis、Celery、queue、worker、generation task、video provider、frontend 或 migration。

## 结构化 execution evidence

`FrameAnchoredStoryboardScene` 增加私有 `execution_evidence` 列表。每条证据在第八轮包含：

- `executor_kind`：`target_subject`、`target_object`、`target_state`、`camera_support`、`effect_support` 或 `environment_support`；
- `assertion`：`affirmed`、`negated` 或 `static`；
- `action_or_state_change`：非空的可见动作或状态变化说明；
- `signature_moment_ids`：与 signature moment 的精确私有 ID 关联；
- `source_behavior_beat_ids`：与 source behavior beat 的精确私有 ID 关联。

最终动作覆盖只接受同时满足以下条件的证据：

1. executor 是 `target_subject`、`target_object` 或 `target_state`；
2. assertion 是 `affirmed`；
3. `action_or_state_change` 非空；
4. 证据与当前 scene、所需 signature moment 和所需 source behavior beat 精确关联。

`camera_support`、`effect_support`、`environment_support` 只能提供摄影、效果或环境支持，不能替代目标主体、目标物体或目标状态实际执行动作。`negated` 和 `static` 同样不能证明动作执行。

本轮删除了以 motion prose 中的 actor、动作词或设备词进行 execution gate 判定的旧路径。诸如 camera equipment、crane、lighting fixture、panel、particle emitter、generator 等文本不再通过自然语言解析决定动作覆盖；唯一有效门禁是结构化 target execution evidence。

Call 3 prompt 明确要求模型输出 execution evidence，OpenAI parser 保留字段，Mock provider 按 signature moment 生成 affirmed target evidence，并保留 exact signature/source linkage。已有 phase order、准备、执行、payoff、return 和 final lock 校验继续生效。

## 结构化 omit infeasibility facts

signature moment 的 `omit` 决策改为同时要求 literal 与 equivalent 两条私有结构化不可行事实。每条事实包含：

- `category`
- `basis`
- `polarity`：`affirmed` 或 `negated`
- `scope`：`global`、`action_interval` 或 `endpoint_only`
- `detail`

合法 omit 必须同时满足：

- literal fact 的 category 与 basis 属于允许的 literal 不可行类型；
- equivalent fact 明确表示 causal equivalent unavailable，并使用对应 basis；
- 两条事实的 polarity 均为 `affirmed`；
- 两条事实的 scope 均为 `global` 或 `action_interval`；
- detail 非空。

`endpoint_only` 只说明终点姿态、末帧或终点构图局部不匹配，不能证明动作在整个可用区间不可行。`negated` 事实也不能证明不可行。`omission_reason`、`equivalent_replacement_failure` 以及旧 category/evidence prose 仅作为说明，不再参与 omit validity gate。

因此，`end`、`ending`、global 与 endpoint 混写、`conflict-free`、`free of conflict` 等自然语言表达不能改变结构化 polarity 或 scope，也不能替代结构化不可行事实。

## Provider、Mock 与编排

- OpenAI Call 2 prompt/parser 输出并保留 literal/equivalent structured infeasibility facts。
- OpenAI Call 3 prompt/parser 输出并保留每个 scene 的 `execution_evidence`。
- Mock provider 同步生成结构化 omit 与 execution 数据，避免测试 provider 使用不同契约。
- 结构化 correction 仍在 Call 2 后由本地 review 产生并附加到既有 Call 3 输入，不新增模型调用。
- 正常成功路径仍严格执行三次 LLM 调用：frame analysis、director plan、final storyboard。
- invalid 或 unrecoverable omit 在 Call 2 后本地失败，因此严格停在两次 LLM 调用。
- 未增加第 4 次审片或修复调用。

## TDD 记录

### 首轮 RED

先增加 schema、validator、provider、Mock、orchestration 和 coverage 回归，真实结果为：

```text
14 failed, 244 passed
```

失败证明旧实现尚未提供完整的结构化 execution/omit 契约。

### 补充 RED

初次实现后，focused 测试暴露三处旧 fixture 没有提供新要求的 execution evidence，真实结果为：

```text
3 failed, 158 passed
```

涉及 execution-before-preparation、two-beat preparation isolation，以及 same-source moment payoff/return isolation。补齐精确关联的结构化证据后进入 GREEN。

### GREEN

```text
Focused：161 passed, 1 warning in 18.26s
Ruff：All checks passed!
Full pytest：667 passed, 1 warning in 98.94s
调用次数专项：2 passed, 1 warning in 3.27s
```

唯一 warning 是既有 Starlette/FastAPI `TestClient` 对 `httpx2` 的 deprecation warning，与本轮实现无关。

## 回归与边界检查

本轮回归覆盖：

- motion prose 不再证明 target execution；
- camera/effect/environment support 不能替代目标动作；
- `affirmed` target evidence 才能覆盖动作，`negated` 与 `static` 均不能；
- execution evidence 必须与 scene、signature moment 和 source behavior beat 精确关联；
- structured omit 的 category、basis、polarity、scope 和 detail 全部受验证；
- `endpoint_only`、`negated` 以及含糊 prose 不能使 omit 合法；
- strict interval overlap、private ID mapping/collision/revalidation、动态短时长压缩、correction、payoff、return 和 final lock 既有行为未回归；
- formatter 与 polling 仍只公开 `storyboard_text` 和既有公共字段，私有 director plan、coverage review、signature/source IDs 与 execution evidence 不进入公共响应；
- `git diff --check` 通过；
- 未恢复 `FINAL_TEXT_OVERLAY_LOCKS`，未引入特定创意动作、时间、比例、产品、UI、奖励、身份或效果硬编码；
- 未修改或提交 `AGENTS.md`、`.env*`、凭证或敏感配置。

## 第八轮已知限制

第八轮虽然把动作执行门禁迁移到了结构化 evidence，但当时仍以 `executor_kind`、自由文本 `action_or_state_change`、signature IDs 和 source IDs 的组合推导 claim identity。这样，同一语义 claim 只要改写 action 文本，就可能绕过 affirmed 与 negated/static 的矛盾检查；跨 scene 也缺少稳定 claim identity。

该限制由第八轮独立审查列为 Important finding，并留给第九轮通过受约束的私有 `claim_id`、同 scene 与跨 scene 一致性检查，以及 private ID normalization/collision/scrub/revalidation 完整修复。

## 结论

第八轮完成了 execution evidence 与 omit validity 的结构化重构，消除了自然语言 parser 作为动作执行和省略合法性的决定性门禁，并保持三次成功调用、两次 invalid omit 调用和公共边界不变。其主要遗留问题是缺少稳定、受约束的 execution claim identity；本报告明确记录该限制，不将第八轮描述为完全关闭该风险。