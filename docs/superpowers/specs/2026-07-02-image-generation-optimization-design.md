# 图片生成阶段优化设计

## 目标

当前关键帧图片生成一次会提交 6 张图，用户需要等待整体任务完成后才能完整判断结果。优化目标是缩短体感等待时间，降低单张失败带来的返工成本，同时保留后端定位耗时瓶颈的能力。

本次采用方案 C：

- 将 6 张关键帧拆成 3 个方案任务。
- 每个方案包含首帧和尾帧两个图片槽位。
- 支持单图重试和整组方案重生。
- 后端记录图片阶段分段耗时日志。
- 图片 provider 并发从 2 小步调到 3，验证稳定后再评估是否继续提升。

## 当前链路

图片生成入口主要有两类：

- 后台工作流：前端调用 `/creatives/generate/task`，后端创建 `image_queue` 任务。
- 外部素材接口：`/integrations/material-generation/images`，先生成或复用文案，再复用同一套图片生成服务。

核心链路：

```text
CopyDraft
-> generate_image_briefs
-> image provider
-> provider image URL
-> download to local storage
-> creative_assets
```

标准图片以文案草稿为主输入；视频关键帧模式会额外带入 storyboard 和 keyframe plan 作为上下文。

## 新的关键帧任务拆分

关键帧模式仍然维持 3 个候选方案、每个方案 2 张图：

```text
方案 1：图 1 首帧 + 图 2 尾帧
方案 2：图 3 首帧 + 图 4 尾帧
方案 3：图 5 首帧 + 图 6 尾帧
```

但执行上不再用一个大任务覆盖 6 张图，而是拆成 3 个方案任务。每个方案任务只负责自己的两个槽位。

前端展示保持业务语言：

```text
方案 1：[首帧] [尾帧]
方案 2：[首帧] [尾帧]
方案 3：[首帧] [尾帧]
```

不展示 `brief 生成中`、`图片下载保存中` 这类技术状态。生成中只表现为对应图片槽位 loading。

## 重试策略

单张失败时，默认只重试失败图片。

示例：

```text
方案 2：
图 3 首帧成功
图 4 尾帧失败
```

用户操作：

- `重试此图`：只重新生成图 4，保留图 3。
- `重生此方案`：重新生成图 3 和图 4，用于首尾风格不一致或方案整体不满意。

后端继续复用现有 `target_index` 机制做单图重试。新生成的资产仍写入 `creative_assets`，并通过 `image_index`、`keyframe_group`、`keyframe_role` 归组。

## 后端耗时日志

耗时日志只用于排查，不直接暴露给前端界面。

需要记录：

- image task 总耗时。
- image brief 生成耗时。
- 每张图 provider 请求耗时。
- provider URL 下载到本地 storage 的耗时。
- `creative_assets` 写入和 refresh 耗时。
- 失败槽位、失败阶段、错误摘要。

日志应使用结构化字段，至少包含：

```text
task_id
draft_id
campaign_id
image_index
keyframe_group
keyframe_role
stage
duration_ms
provider
model
status
error_code
```

## 并发调整

先将：

```env
MODEL_PROVIDER_IMAGE_CONCURRENCY=6
```

不直接设为 6。当前生产式本地 Docker 使用 Celery image worker，worker 本身还有并发；如果 provider 并发直接升到 6，叠加多个 worker 进程后可能对 CLIProxyAPI 或上游模型造成过量并发，引发排队、限流、超时或失败率上升。

验证稳定后，再考虑从 3 升到 4。

## 验收标准

- 关键帧生成时，前端按 3 个方案展示，每个方案有首帧和尾帧两个槽位。
- 任一方案完成后能先展示该方案，不等待全部 6 张图完成。
- 单张失败时，可以只重试该图，不影响同方案另一张已成功图片。
- 可以对某个方案执行整组重生。
- 后端日志能区分 brief、provider、下载保存、DB 写入各阶段耗时。
- `MODEL_PROVIDER_IMAGE_CONCURRENCY=6` 下，图片任务成功率不低于现状，耗时有可观测对比数据。

## 非目标

- 不把技术阶段文案显示到前端。
- 不改外部素材接口的公开返回结构。
- 不改变 `creative_assets` 作为图片资产落库表的事实。
- 不一次性把图片 provider 并发拉到 6。
