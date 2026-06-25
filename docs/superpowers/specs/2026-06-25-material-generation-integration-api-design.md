# 外部素材生成接口设计

## 背景

外部投放系统现在希望直接调用 AI 系统生成素材，而不只通过浏览器 URL 跳转进入工单流程。新的接口需要覆盖文案、图片和视频三类能力。外部系统传业务必要参数，AI 系统返回状态码和对应信息。图片和视频结果只需要状态码和素材链接地址。

当前项目已有内部生成链路：

- 文案生成接口依赖内部 `topic_id`。
- 图片生成接口依赖内部 `draft_id`。
- 视频生成接口依赖内部 `campaign_id`、`creative_asset_ids`，并且实际供应商生成是异步状态模型。

因此新接口不应该让外部系统传内部 ID，而应该作为集成层封装。外部系统只传产品、落地页、人群、国家、事件、生成要求等业务参数，AI 系统内部自动创建最小可追踪记录。

## 目标

1. 新增一组稳定的外部素材生成 API，供外部投放系统调用。
2. 文案和图片接口同步返回可用结果。
3. 视频接口异步返回任务状态，外部系统通过查询接口获取最终视频链接。
4. 返回结构统一为 `code`、`message`、`data`，降低外部系统接入成本。
5. 复用现有 `AI_ADS_ACCESS_TOKEN` 鉴权方式。
6. 复用现有品牌安全规则，禁止生成赌博、博彩、药品、货币、钱、折扣、低价等相关内容。

## 非目标

第一版不做一个接口生成全部素材。文案、图片、视频保持独立接口，避免视频耗时影响文案和图片调用。

第一版不要求外部系统传 `topic_id`、`draft_id`、`campaign_id`、`creative_asset_ids` 等内部 ID。接口内部负责创建或复用必要记录。

第一版不改变已有预审包、确认回传、投放分析接口的返回协议。

第一版不做主动回调外部系统的视频完成通知。外部系统通过查询接口轮询状态。后续如果外部系统支持回调，再扩展 `callback_url`。

## 推荐方案

采用“文案和图片同步，视频异步”的方案。

接口路径：

```text
POST /api/v1/integrations/material-generation/copy
POST /api/v1/integrations/material-generation/images
POST /api/v1/integrations/material-generation/videos
GET  /api/v1/integrations/material-generation/jobs/{job_id}
```

鉴权沿用当前 token 校验规则：

- `Authorization: Bearer <AI_ADS_ACCESS_TOKEN>`
- `?ai_access_token=<AI_ADS_ACCESS_TOKEN>`
- `?access_token=<AI_ADS_ACCESS_TOKEN>`

外部素材生成接口应尽量返回统一错误 body。鉴权失败时使用 `401 Unauthorized`，body 使用 `code = 4003` 和明确的 `message`。

## 外部请求字段

三个生成接口共享一组基础业务字段：

```json
{
  "external_request_id": "optional-external-id",
  "product_name": "产品或项目名称",
  "landing_url": "https://example.com",
  "audience": "目标人群描述",
  "country": "US",
  "event_name": "注册",
  "customEventType": "COMPLETE_REGISTRATION",
  "language": "zh-CN",
  "brief": "生成要求或业务说明",
  "selling_points": ["核心卖点 1", "核心卖点 2"],
  "constraints": {}
}
```

字段含义：

- `external_request_id`：外部系统请求 ID，用于追踪和幂等。可选，但建议传。
- `product_name`：产品、项目或广告对象名称。必填。
- `landing_url`：落地页地址。可选，但建议传，后续可用于落地页分析。
- `audience`：目标人群描述。可选。
- `country`：投放国家或地区。可选，默认按系统逻辑处理。
- `event_name`：外部系统事件名称，例如注册、快速注册、购买、加购。可选。
- `customEventType`：外部系统标准事件字段。可选；如果未传，系统可根据 `event_name` 做识别。
- `language`：期望输出语言。可选。
- `brief`：本次生成的具体要求。必填或与 `selling_points` 至少提供一个。
- `selling_points`：卖点列表。可选。
- `constraints`：额外约束。可选，供后续扩展。

图片接口额外字段：

```json
{
  "count": 1,
  "size": "1:1"
}
```

视频接口额外字段：

```json
{
  "image_urls": ["https://ai.ggcss.xyz/storage/example.png"],
  "duration_seconds": 6,
  "aspect_ratio": "9:16",
  "prompt": "视频生成要求"
}
```

视频第一版需要至少一张参考图。参考图可以来自刚生成的图片链接，也可以是外部系统传入的可访问图片链接。实现时优先支持本系统生成的图片链接；外部图片 URL 如供应商不支持或无法转存，返回明确错误。

## 返回协议

所有接口使用统一 body：

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

约定：

- `code = 0`：成功，结果可直接使用。
- `code = 1001`：任务处理中，主要用于视频。
- `code = 4001`：参数错误。
- `code = 4003`：鉴权失败。
- `code = 4091`：品牌安全检查未通过。
- `code = 5001`：生成失败或供应商异常。

HTTP 状态码用于表达传输和服务状态：

- `200 OK`：同步成功或查询成功。
- `202 Accepted`：视频任务已创建，正在处理。
- `400 Bad Request`：参数错误。
- `401 Unauthorized`：鉴权失败。
- `409 Conflict`：结果未就绪或品牌安全阻断。
- `500 Internal Server Error`：系统或供应商异常。

### 文案返回

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "request_id": "internal-job-or-draft-id",
    "primary_text": "正文文案",
    "headline": "标题",
    "description": "描述",
    "cta": "LEARN_MORE"
  }
}
```

### 图片返回

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "request_id": "internal-job-or-asset-id",
    "urls": [
      "https://ai.ggcss.xyz/storage/images/example.png"
    ]
  }
}
```

### 视频创建返回

```json
{
  "code": 1001,
  "message": "processing",
  "data": {
    "job_id": "video-job-id",
    "status": "processing"
  }
}
```

### 视频查询处理中返回

```json
{
  "code": 1001,
  "message": "processing",
  "data": {
    "job_id": "video-job-id",
    "status": "processing"
  }
}
```

### 视频查询成功返回

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "job_id": "video-job-id",
    "status": "succeeded",
    "url": "https://ai.ggcss.xyz/storage/videos/example.mp4"
  }
}
```

### 失败返回

```json
{
  "code": 5001,
  "message": "video generation failed",
  "data": {
    "job_id": "video-job-id",
    "status": "failed",
    "error": "供应商返回的错误信息"
  }
}
```

## 内部架构

新增模块建议：

- `backend/app/api/v1/endpoints/material_generation.py`
- `backend/app/schemas/material_generation.py`
- `backend/app/services/material_generation_service.py`

`material_generation_service` 负责把外部业务参数转换成内部生成上下文：

1. 创建或复用最小 `Campaign`。
2. 创建 `ContentTopic`，用于承载选题角度和卖点。
3. 调用现有 `CopywritingService` 生成文案。
4. 调用现有 `CreativeService` 生成图片。
5. 调用现有 `VideoService` 创建并启动视频任务。
6. 结果中只暴露外部需要的内容，不暴露内部复杂对象。

素材生成来源统一写入 `metadata_json`：

```json
{
  "source": "external_material_generation",
  "external_request_id": "optional-external-id"
}
```

视频任务可以复用 `VideoAsset` 作为状态记录。查询接口根据 `job_id` 读取视频状态，并在必要时调用现有刷新逻辑同步供应商状态。

## 数据流

文案：

1. 外部系统调用文案接口。
2. AI 系统校验鉴权和参数。
3. AI 系统创建最小 campaign/topic。
4. 调用现有文案生成服务。
5. 扫描品牌安全风险。
6. 返回文案字段。

图片：

1. 外部系统调用图片接口。
2. AI 系统校验鉴权和参数。
3. AI 系统创建最小 campaign/topic/draft，或基于请求文案生成 draft。
4. 调用现有图片生成服务。
5. 扫描品牌安全风险。
6. 返回图片 URL 列表。

视频：

1. 外部系统调用视频创建接口。
2. AI 系统校验鉴权和参数。
3. AI 系统解析参考图片，创建最小 campaign/draft/creative asset。
4. 调用现有视频服务创建任务并启动供应商生成。
5. 返回 `202 Accepted` 和 `job_id`。
6. 外部系统轮询查询接口。
7. 查询接口刷新供应商状态。
8. 成功后返回视频 URL，失败后返回错误信息。

## 品牌安全

接口必须复用现有品牌安全策略：

- 生成提示词必须包含禁止赌博、博彩、药品、货币、钱、折扣、低价等内容的规则。
- 文案结果返回前扫描 `primary_text`、`headline`、`description`、`cta`。
- 图片生成时扫描图片 brief 和 prompt。
- 视频生成时扫描 storyboard 和 prompt。
- 命中高风险内容时返回 `409 Conflict`，body 中使用 `code = 4091`。

第一版只做文本层面控制和扫描，不承诺识别已生成图片或视频画面中的真实视觉元素。

## 幂等和追踪

如果外部系统传 `external_request_id`，系统应在同一素材类型范围内尽量避免重复创建结果：

- 已成功的文案请求可以直接返回已有文案。
- 已成功的图片请求可以直接返回已有图片链接。
- 视频请求如果已有处理中的任务，返回原 `job_id` 和当前状态。

第一版可以使用现有模型的 `metadata_json.external_request_id` 进行查询，不新增数据库表。若后续并发或审计要求提高，再增加独立任务表和唯一索引。

## 错误处理

参数错误：

- 缺少 `product_name`。
- `brief` 和 `selling_points` 都为空。
- 图片 `count` 超出允许范围。
- 视频缺少参考图。
- 视频时长或比例超出供应商支持范围。

这些返回 `400 Bad Request` 和 `code = 4001`。

鉴权失败：

- 复用现有 token 来源和比对规则。
- 返回 `401 Unauthorized` 和 `code = 4003`。
- `message` 使用 `invalid or missing access token`。

品牌安全失败：

- 返回 `409 Conflict` 和 `code = 4091`。
- `data` 包含命中分类、字段路径、命中文本和修改建议。

供应商失败：

- 返回 `500 Internal Server Error` 和 `code = 5001`。
- 视频异步查询时，如果供应商任务失败，查询接口返回 `code = 5001`、`status = failed` 和错误信息。

## 测试策略

后端测试：

- 文案接口在合法参数下返回 `code = 0` 和文案字段。
- 图片接口在合法参数下返回 `code = 0` 和 URL 列表。
- 视频创建接口返回 `202`、`code = 1001` 和 `job_id`。
- 视频查询接口在处理中状态返回 `code = 1001`。
- 视频查询接口在成功状态返回 `code = 0` 和视频 URL。
- 缺少必填业务参数返回 `400`。
- 品牌安全命中时返回 `409` 和 `code = 4091`。
- 外部 `event_name` 包含“注册”时，内部 `customEventType` 仍识别为完成注册。

验证命令：

```powershell
.venv\Scripts\python.exe -m ruff check backend tests
.venv\Scripts\python.exe -m pytest
```

如果实现影响前端或生产镜像，还需要：

```powershell
cd frontend\web-admin
npm run build
```

以及本地生产式 Docker 重建：

```powershell
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

## 验收标准

1. 外部系统无需知道内部 ID，即可调用文案、图片、视频生成接口。
2. 文案接口同步返回可投放使用的文案字段。
3. 图片接口同步返回图片链接列表。
4. 视频接口创建任务后立即返回 `job_id`，查询接口最终返回视频链接。
5. 所有新接口复用现有鉴权。
6. 品牌安全规则在新接口中生效。
7. 新接口不破坏现有工单、预审包、确认回传、投放分析流程。
