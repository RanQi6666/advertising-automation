# 外部视频提示词透传设计

## 状态

已批准的产品与技术设计。

本文档只定义现有外部视频生成接口的后台提示词透传行为，不实施代码。后续实现必须先基于本文档编写实施计划。

## 背景

当前外部投放系统通过 AI 系统的现有接口提交两张首尾帧图片和 `storyboard_text`：

```http
POST /api/v1/integrations/video-generation/videos
```

外部任务创建后，AI 后台把 `storyboard_text` 保存为 `VideoAsset.prompt`，但视频 Worker 构造供应商请求时仍会调用共享提示词增强逻辑。该逻辑会：

1. 改写或替换部分安全相关词语；
2. 追加 Creative Safety 提示词块；
3. 追加固定的品牌、3A 游戏广告和 `0-3s / 3-9s / 9-12s` 节奏规则；
4. 在存在 `creative_strategy` 时追加国家、市场、风格包和创意策略；
5. 在视频生成完成后，根据 `final_text_overlay_locks` 对最终视频进行文字覆盖。

因此，当前视频供应商收到的 `prompt` 不等于外部系统提交的 `storyboard_text`。

## 目标

只针对现有外部视频生成任务实现提示词透传：

```text
外部系统提交 storyboard_text
  -> AI 后台只做必要技术校验和头尾空白清理
  -> storyboard_text 作为供应商 prompt
  -> 不追加或改写任何 AI 后台创意规则
  -> 不执行最终文字覆盖
```

外部投放系统不修改接口地址、鉴权方式、请求字段、轮询方式或响应解析逻辑。

## 非目标

- 不新增外部接口。
- 不新增 `prompt_mode`、`passthrough` 等公开请求字段。
- 不新增或更换鉴权 Token。
- 不增加来源 IP 白名单。
- 不修改 `/www/wwwroot/pixel_project` 的 PHP 或前端代码。
- 不改变 AI 系统内部视频生成任务的提示词增强逻辑。
- 不取消图片、时长、比例、Base64、幂等、队列或供应商参数校验。
- 不尝试关闭或绕过视频供应商自身的内容审核、风控或模型限制。
- 不更换视频供应商，不引入自托管视频模型。

## 已批准的关键决策

### 1. 复用现有接口

继续使用：

```http
POST /api/v1/integrations/video-generation/videos
GET /api/v1/integrations/video-generation/jobs/{job_id}
```

不创建独立透传接口。

### 2. 复用现有鉴权

继续使用现有全局：

```text
AI_ADS_ACCESS_TOKEN
```

调用方式保持：

```http
Authorization: Bearer <AI_ADS_ACCESS_TOKEN>
```

本设计接受一个明确边界：任何持有全局 Token 且能调用现有外部视频接口的调用方，都会获得相同的外部视频提示词透传能力。本设计不再增加第二层调用方身份区分。

### 3. 外部系统零改动

外部系统继续提交现有 JSON：

```json
{
  "external_request_id": "trusted-system-business-id",
  "images": [
    "data:image/jpeg;base64,<first-frame>",
    "data:image/jpeg;base64,<last-frame>"
  ],
  "storyboard_text": "video instructions",
  "duration_seconds": 12,
  "aspect_ratio": "9:16"
}
```

图片顺序保持：

```text
images[0] = first_frame
images[1] = last_frame
```

### 4. 关闭最终文字覆盖

外部视频任务不再解析、保存或应用：

```text
[FINAL_TEXT_OVERLAY_LOCKS]
...
[/FINAL_TEXT_OVERLAY_LOCKS]
```

如果 `storyboard_text` 本身包含这些文本，因为透传要求不允许后台重写或删除，它们仍作为普通提示词文本发送给供应商；AI 后台不再把它们解释为后处理指令。

## 方案比较

### 方案 A：根据现有外部来源标记在共享视频服务中切换，推荐

现有外部任务已经带有：

```json
{
  "source": "external_video_generation"
}
```

共享视频服务根据该内部来源标记选择提示词路径：

```text
source == external_video_generation
  -> passthrough provider prompt
otherwise
  -> existing augmented provider prompt
```

优点：

- 外部系统零改动；
- 不扩大公开 API；
- 能复用现有任务队列、重试、轮询、转存和状态管理；
- 内部任务和外部任务边界明确；
- 旧外部任务记录也已经带有相同来源标记，便于部署兼容。

风险：

- 共享 `VideoService` 中会增加一个明确分支；
- 后续新增其他外部视频来源时，必须显式决定是否透传，不能自动继承。

### 方案 B：外部请求增加 `prompt_mode=passthrough`，不采用

优点是调用意图显式。缺点是外部系统需要修改请求，而且任何持有全局 Token 的调用方都可主动打开透传，不符合“外部系统零改动”的要求。

### 方案 C：外部服务绕开共享 `VideoService`，不采用

优点是代码路径完全隔离。缺点是需要重复实现供应商启动、状态轮询、失败重试、视频转存和数据库状态更新，长期维护风险最高。

## 推荐架构

```mermaid
flowchart LR
    A["外部投放系统<br/>现有接口、参数和 Token"] --> B["ExternalVideoGenerationService"]
    B --> C["保存 VideoAsset<br/>source=external_video_generation"]
    C --> D["现有 video_queue"]
    D --> E["VideoService._build_provider_request"]
    E --> F{"是否为外部视频来源"}
    F -->|是| G["prompt = video.prompt.strip()"]
    F -->|否| H["现有安全、3A、风格包和创意策略增强"]
    G --> I["视频供应商"]
    H --> I
    I --> J["现有轮询和视频转存"]
    J --> K{"是否为外部视频来源"}
    K -->|是| L["跳过最终文字覆盖"]
    K -->|否| M["保留现有覆盖逻辑"]
```

## 提示词构造规则

### 外部视频任务

外部任务的供应商提示词规则为：

```python
provider_prompt = video.prompt.strip()
```

现有接口入口已经对 `payload.storyboard_text` 执行 `.strip()`，实现时可以保留该技术规范化。透传的精确定义是：

> 除移除整个字符串开头和结尾的空白字符外，不做任何词语替换、语义改写、规则追加、标签删除或格式重组。

如果清理后为空，继续返回现有 `storyboard_text is required` 类型错误。

外部任务不得调用或间接应用：

```python
sanitize_creative_safety_text(...)
creative_safety_prompt_block()
_keyframe_brand_aaa_video_rules()
_creative_strategy_prompt_block(...)
```

也不得追加：

- Creative Safety 规则；
- 赌博、金币、提现、奖励等词语替换；
- 品牌展示硬规则；
- 固定 3A 游戏广告风格；
- `0-3s / 3-9s / 9-12s` 时间规则；
- `style_pack`；
- `country_style_pack`；
- `market_game_style_pack`；
- `creative_strategy`；
- CTA、Boss、VIP、VFX 或国家市场规则。

### AI 系统内部视频任务

所有非 `external_video_generation` 来源继续调用现有提示词增强逻辑。内部工作流的安全改写、Creative Safety、3A 时间规则、风格包、国家市场策略和 `creative_strategy` 均保持不变。

禁止用“`creative_strategy` 是否为空”判断是否透传，因为部分内部任务也可能没有 `creative_strategy`。唯一批准的业务判断依据是明确的外部视频来源标记。

## 供应商请求数据

外部任务最终向视频供应商构造：

```python
VideoGenerationRequest(
    prompt=video.prompt.strip(),
    source_images=[first_frame, last_frame],
    duration_seconds=video.duration_seconds,
    aspect_ratio=video.aspect_ratio,
    metadata={
        "campaign_id": video.campaign_id,
        "draft_id": video.draft_id,
        "video_id": video.id,
    },
)
```

当前火山引擎适配器继续负责将其映射为供应商字段：

```json
{
  "model": "<server-controlled model>",
  "content": [
    {"type": "text", "text": "<storyboard_text>"},
    {"type": "image_url", "role": "first_frame"},
    {"type": "image_url", "role": "last_frame"}
  ],
  "resolution": "<server-controlled resolution>",
  "ratio": "9:16",
  "duration": 12,
  "generate_audio": true,
  "watermark": false,
  "return_last_frame": false,
  "execution_expires_after": 172800,
  "priority": 0
}
```

`campaign_id`、`draft_id` 和 `video_id` 是后台追踪元数据，不得拼接进 `prompt`。

## 最终文字覆盖处理

### 创建阶段

`ExternalVideoGenerationService.create_video()` 不再对外部 `storyboard_text` 调用：

```python
extract_final_text_overlay_locks(...)
```

新建外部 `VideoAsset.metadata_json` 不再保存：

```json
{
  "final_text_overlay_locks": []
}
```

### 转存阶段

共享 `_apply_final_text_overlay_locks()` 必须先检查来源。外部视频来源直接返回，不调用 FFmpeg/覆盖服务：

```python
if is_external_video_generation(video):
    return
```

该防御判断用于兼容部署前已经创建、但部署后才完成转存的外部任务。即使旧记录中已经有 `final_text_overlay_locks`，也不得在新版本 Worker 中应用。

内部任务的最终文字覆盖逻辑保持不变。

## 保留的技术校验和运行逻辑

提示词透传不等于取消技术校验。以下行为全部保留：

- `storyboard_text` 必填和空字符串校验；
- `images` 必须正好包含两张图片；
- Base64/Data URL 解码；
- 图片文件大小上限；
- 首帧和尾帧角色顺序；
- 图片存储和公开 URL 解析；
- 供应商最大参考图片数量；
- 供应商支持的视频时长范围；
- `aspect_ratio` 现有 schema 校验；
- `external_request_id` 幂等；
- `GenerationTask`、`video_queue` 和 Worker 执行；
- 供应商任务启动和状态轮询；
- 失败重试与错误记录；
- 供应商视频下载和本地存储转存；
- 创建、轮询和成功 URL 的现有响应结构。

本设计不允许外部系统通过 `storyboard_text` 控制服务器配置项，例如模型、分辨率、音频、水印、优先级或超时时间。

## 视频供应商规则边界

本设计只取消 AI 后台自己的提示词改写和后处理，不修改供应商服务端规则。

可以由 AI 后台控制的内容包括：

- 发送给供应商的 `prompt`；
- 首帧和尾帧；
- 模型及受支持的技术参数；
- 分辨率、比例、时长、音频、水印和优先级等服务器配置。

不能由本项目代码关闭或修改的内容包括：

- 供应商输入文本审核；
- 供应商输入图片审核；
- 供应商输出视频审核；
- 供应商账号风控；
- 供应商隐藏系统规则；
- 模型训练形成的行为边界。

`safety_identifier` 不得被描述或实现为“关闭安全审核”的开关。即使该字段为空，供应商仍可能审核并拒绝任务。

因此验收保证是：

```text
供应商请求中的 text == storyboard_text.strip()
```

不保证：

```text
供应商一定接受提示词
供应商一定按照提示词生成
供应商不执行内容审核
```

## 错误处理

接口错误结构保持不变。

外部任务可能继续因以下原因失败：

- `storyboard_text` 为空；
- 图片数量不是 2；
- Base64 无法解码；
- 图片过大或供应商不支持图片尺寸；
- 时长或比例不被供应商支持；
- 供应商内容审核拒绝；
- 供应商超时或返回错误；
- 视频下载或本地转存失败。

透传分支不吞掉、不重写供应商错误。现有任务记录、重试分类和轮询失败响应继续作为事实来源。

## 可观测性和数据安全

- 保留现有任务 ID、供应商任务 ID、状态和错误记录；
- 可在 `VideoAsset.metadata_json` 中记录内部 `prompt_mode: "passthrough"` 作为审计标记，但不能要求外部请求提供该字段；
- 日志不得输出 `AI_ADS_ACCESS_TOKEN`；
- 日志不得完整输出两张 Base64 图片；
- 不新增真实凭据或 Token 到仓库、设计文档或测试夹具；
- `storyboard_text` 继续按照当前数据模型保存，不在本次设计中引入新的提示词存储机制。

`prompt_mode` 审计字段为可选内部实现细节。业务判断仍以 `source == external_video_generation` 为准，避免旧外部任务因缺少新字段而退回增强模式。

## 兼容性

### 外部系统

完全兼容，无需修改：

- PHP 客户端；
- 请求地址；
- 请求头；
- JSON 字段；
- `external_request_id`；
- 轮询 URL；
- 业务状态码；
- 最终视频 URL 读取方式。

### 已存在任务

- 已经向供应商提交的任务：供应商已经收到旧提示词，无法改变；
- 尚未启动供应商请求的外部任务：新 Worker 启动后按来源标记执行透传；
- 已经由供应商生成、但尚未完成本地转存的外部任务：新 Worker 跳过最终文字覆盖；
- 已完成任务：不重新生成、不重新转存、不修改历史视频；
- 失败后安全重排队的旧外部任务：重新启动供应商请求时使用透传。

### 数据库

不增加表或列，不需要 Alembic 数据库迁移。来源标记和可选审计信息继续保存在现有 JSON metadata 中。

## 预计代码修改范围

实现阶段预计只修改以下范围：

- `backend/app/services/video_service.py`
  - 增加统一的外部视频来源判断；
  - `_build_provider_request()` 对外部来源使用透传提示词；
  - `_apply_final_text_overlay_locks()` 对外部来源直接返回。

- `backend/app/services/external_video_generation_service.py`
  - 停止解析 `FINAL_TEXT_OVERLAY_LOCKS`；
  - 停止为新外部任务保存 `final_text_overlay_locks`；
  - 保留 `source=external_video_generation`。

- `tests/test_external_video_generation.py`
  - 验证公开接口契约不变；
  - 验证外部提示词透传；
  - 验证不保存和不应用最终文字覆盖；
  - 验证幂等和重排队仍工作。

- `tests/test_video_provider.py` 或更合适的共享视频服务测试文件
  - 验证外部提示词不被改写或追加；
  - 验证内部任务仍使用现有增强逻辑；
  - 验证旧外部 metadata 中存在 overlay 时仍跳过覆盖。

实现阶段不得修改外部投放系统目录 `/www/wwwroot/pixel_project`，除非后续出现与本设计无关且经用户单独批准的问题。

## 测试设计

### 1. 外部提示词精确透传

使用包含安全词、游戏词、中文、换行和已有风格描述的 `storyboard_text`，断言：

```python
provider_request.prompt == storyboard_text.strip()
```

并断言结果中不包含后台追加的：

- Creative Safety 块；
- `0-3s opening rule`；
- 3A、Boss、VIP、CTA 规则；
- `creative_strategy:`；
- 风格包内容。

### 2. 外部任务不执行文字覆盖

覆盖以下两种情况：

1. 新任务的 `storyboard_text` 包含 `[FINAL_TEXT_OVERLAY_LOCKS]`，不解析、不保存 overlay；
2. 模拟旧外部任务 metadata 已有 `final_text_overlay_locks`，转存完成时不调用覆盖函数。

### 3. 内部任务回归

对非外部来源任务断言：

- 仍执行安全文本处理；
- 仍追加 Creative Safety；
- 仍追加固定时间/3A 规则；
- 有 `creative_strategy` 时仍追加相应策略；
- 有合法 overlay metadata 时仍执行最终文字覆盖。

### 4. 接口兼容性

保留并通过现有测试：

- 创建返回 HTTP 202 和 `code=1001`；
- 使用 `job_id` 轮询；
- 成功返回 `code=0` 和视频 URL；
- 失败返回现有失败 envelope；
- 相同 `external_request_id` 保持幂等；
- 未知公开字段继续被 `extra="forbid"` 拒绝；
- 现有 `AI_ADS_ACCESS_TOKEN` 鉴权继续生效。

### 5. 供应商 Payload

断言最终供应商 payload：

- `content[0].text` 等于 `storyboard_text.strip()`；
- `content[1].role == "first_frame"`；
- `content[2].role == "last_frame"`；
- 时长、比例和服务器控制参数保持原值；
- 不新增关闭供应商审核的伪参数。

## 验收标准

实现完成必须同时满足：

1. 外部投放系统不修改任何代码即可继续创建和轮询视频；
2. 新外部任务的供应商 `prompt` 精确等于 `storyboard_text.strip()`；
3. 外部任务不调用安全改写、Creative Safety、3A 时间规则或创意策略拼接；
4. 外部任务不解析、不保存、不执行最终文字覆盖；
5. 旧外部任务即使存在 overlay metadata，也不在新 Worker 中执行覆盖；
6. 内部视频任务的现有增强与覆盖行为保持不变；
7. 现有鉴权、幂等、队列、轮询、转存和响应契约保持不变；
8. 无数据库迁移；
9. 测试明确区分 AI 后台透传与供应商自身审核边界；
10. 没有提交 `.env`、`.env.production`、`AGENTS.md`、Token、API Key 或其他无关工作区文件。

## 部署设计

实现通过测试并经用户批准部署后，需要重新构建并启动至少：

- FastAPI Backend；
- Video Celery Worker。

只重启 HTTP Backend 不足以使 Worker 中的供应商请求构造逻辑生效。

建议部署顺序：

1. 部署前记录当前分支、提交和容器状态；
2. 备份服务器 `.env.production`，但不修改和不提交；
3. 拉取已批准提交；
4. 重新构建 Backend 和 Worker 所在镜像/服务；
5. 检查容器状态和健康接口；
6. 创建一个新的外部测试任务；
7. 从任务记录或受控测试桩验证供应商 `prompt` 等于输入文本；
8. 验证视频转存后没有最终文字覆盖；
9. 验证一个内部视频任务仍保留原提示词增强；
10. 验证外部轮询接口和最终 URL 正常。

本设计不要求重建外部投放系统 `/www/wwwroot/pixel_project`。

## 回滚

回滚应用代码和 Worker 到改造前版本即可恢复旧的外部提示词增强及 overlay 行为。由于没有数据库迁移，代码回滚不需要 schema 回滚。

需要注意：在透传版本中已经提交给供应商的任务不会因为代码回滚而重新获得旧提示词；同理，回滚后已经完成的视频不会自动重新执行文字覆盖。

## 实施前约束

- 本文档批准后，下一步只编写实施计划；
- 实施必须优先增加失败测试，再修改生产代码；
- 不修改或暂存工作区已有的无关文件；
- 不提交 `.env`、`.env.production`、`AGENTS.md`、凭据或运行时产物；
- 未经用户单独批准，不推送公司仓库或部署测试服务器。