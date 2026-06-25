# 最终预审包字段说明

本文解释 AI 素材/广告自动化系统在运营确认后，给外部投放系统拉取的最终预审包 JSON 中每个字段的含义。

最终预审包不是直接发布广告的指令，而是一份“已由运营确认、可供外部系统创建广告草稿或填充广告表单”的结构化数据。外部系统仍然负责真实投放、账号选择、Pixel 绑定、素材入库、权限校验和最终发布。

## 获取方式

运营在 AI 系统里点击“确认并回传”后，任务状态会变成 `returned`。外部系统拿到 `job_id` 后，通过下面接口拉取最终 JSON：

```http
GET /api/v1/integrations/publishing/ad-generation/jobs/{job_id}/result
Authorization: Bearer <AI_ADS_ACCESS_TOKEN>
```

如果任务还没有确认回传，接口会返回 `409`。如果 token 缺失或错误，会返回 `401`。

## 顶层结构

```json
{
  "job_id": "ai-job-id",
  "external_order_id": "external-order-id",
  "status": "returned",
  "campaign_payload": {},
  "adset_payload": {},
  "creative_payload": {},
  "assets": {
    "images": [],
    "videos": []
  },
  "review": {},
  "metadata_json": {}
}
```

## 顶层字段

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `job_id` | string | AI 系统内部任务 ID。外部系统用它拉取最终预审包，也可以作为排查日志的主键。 |
| `external_order_id` | string/null | 外部系统传入的订单或业务 ID。如果进入 AI 系统时没有传，则可能为空。 |
| `status` | string | 当前最终结果状态。外部可拉取成功时通常是 `returned`。预审阶段内部可能出现 `final_review`。 |
| `campaign_payload` | object/null | 广告系列层级字段。对应外部系统里的广告系列或 Campaign。 |
| `adset_payload` | object/null | 广告组层级字段。对应外部系统里的广告组或 Ad Set。 |
| `creative_payload` | object/null | 广告创意层级字段。包含文案、标题、落地页、按钮、最终选中的素材 URL。 |
| `assets.images` | array | 本次生成并可供选择的图片素材列表。外部通常优先使用 `creative_payload.image_url`，需要素材库时再读取这里。 |
| `assets.videos` | array | 本次生成并可供选择的视频素材列表。外部通常优先使用 `creative_payload.video_url`。 |
| `review` | object | 预审信息、缺失字段、警告、品牌安全检查结果和最终预审勾选状态。 |
| `metadata_json` | object | AI 系统内部关联信息，例如 campaign、topic、draft、素材 ID 和最终预审时间。外部系统通常不需要直接投放这些字段。 |

## `campaign_payload`

广告系列层级字段。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `name` | string | 广告系列名称。通常来自项目名、产品名或运营确认后的名称。 |
| `objective` | string | 广告系列目标。常见值有 `OUTCOME_SALES`、`OUTCOME_LEADS`、`OUTCOME_TRAFFIC`、`OUTCOME_ENGAGEMENT`、`OUTCOME_APP_PROMOTION`。 |
| `status` | string | 建议创建状态。当前固定为 `PAUSED`，表示外部系统应先创建草稿或暂停态广告，不自动上线。 |
| `draft` | number | 草稿标记。当前为 `1`，表示建议外部系统按草稿处理。 |

说明：

- `objective` 会根据优化事件推断。例如购买、加购、发起结账、添加支付信息、首充通常归为 `OUTCOME_SALES`；完成注册通常归为 `OUTCOME_LEADS`。
- 外部系统如果有自己的目标枚举，需要在导入时做一层映射。

## `adset_payload`

广告组层级字段。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `name` | string | 广告组名称。通常包含国家、事件、年龄段等信息，便于运营识别。 |
| `daily_budget` | number/null | 日预算。当前来自外部或 AI 创建偏好；如果外部系统预算单位不同，需要自行换算。 |
| `billing_event` | string | 计费事件。当前默认 `IMPRESSIONS`。 |
| `optimization_goal` | string | 优化目标。转化类事件通常是 `OFFSITE_CONVERSIONS`，流量类可能是 `LINK_CLICKS`。 |
| `bid_strategy` | string | 出价策略。当前默认 `LOWEST_COST_WITHOUT_CAP`。 |
| `event_name` | string/null | AI 系统内部兼容字段，用于保留旧流程中的事件名。外部新对接优先使用 `customEventType`。 |
| `customEventType` | string/null | 外部系统优化事件参数。当前最重要的事件字段，应直接回填到外部系统的优化事件字段。 |
| `countries` | string | 投放国家代码，当前是单个国家代码，例如 `US`、`IN`、`PH`。 |
| `country_code` | string | 投放国家代码，和 `countries` 保持一致，便于外部系统读取。 |
| `country_label` | string/null | 国家展示名，例如 `United States`。没有识别到时可能为空或等于国家代码。 |
| `age_min` | number | 最小年龄。默认 `18`。 |
| `age_max` | number | 最大年龄。默认 `65`。 |
| `gender` | string/null | 性别定向。可能是 `male`、`female`、`all`、中文值或空，取决于工单输入和运营确认。 |
| `audience_description` | string/null | 投放人群描述。用于外部系统备注、人群手工配置或后续 AI 分析。 |
| `start_type` | string | 投放开始方式。当前默认 `I`，表示立即或按外部系统默认开始方式处理。 |
| `start_time` | number | 开始时间。当前默认 `0`，表示不指定具体时间。 |
| `status` | string | 建议广告组状态。当前固定 `PAUSED`。 |
| `draft` | number | 草稿标记。当前为 `1`。 |

### `customEventType` 取值

`customEventType` 只使用外部系统截图里的固定事件参数：

| 中文事件 | 回传参数 |
| --- | --- |
| 完成注册 | `COMPLETE_REGISTRATION` |
| 购买 | `PURCHASE` |
| 加入购物车 | `ADD_TO_CART` |
| 发起结账 | `INITIATED_CHECKOUT` |
| 搜索行为 | `SEARCH` |
| 添加支付信息 | `ADD_PAYMENT_INFO` |
| 首充 | `first_recharge` |

识别规则：

- 只要工单或外部字段里包含“注册”，例如“注册”“快速注册”“完成注册”，都会归一为 `COMPLETE_REGISTRATION`。
- 外部系统创建广告时，应优先读取 `adset_payload.customEventType`，不要再读取旧字段 `optimization_event`。
- 如果后续外部系统新增事件枚举，需要先同步到 AI 系统映射表，再让 AI 生成新的参数。

## `creative_payload`

创意层级字段，表示最终选中的文案和素材。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `name` | string | 创意名称。通常由项目名加素材类型组成，例如 `Campaign - image` 或 `Campaign - video`。 |
| `type` | string | 创意类型。当前常见为 `image` 或 `video`。 |
| `message` | string | 广告正文，也就是 Primary Text。 |
| `link` | string/null | 落地页链接。外部系统应作为广告跳转链接使用。 |
| `ads_name` | string/null | 广告标题，也就是 Headline。字段名沿用外部系统习惯。 |
| `description` | string/null | 广告描述。可用于 Description 或补充说明。 |
| `btn_type` | string | 按钮类型。当前默认 `LEARN_MORE`。 |
| `asset_url` | string/null | 最终选中素材 URL。图片广告时通常等于图片 URL；视频广告时通常等于视频 URL。 |
| `asset_id` | string/null | 外部素材库 ID。当前多数情况下为空，因为素材还没有进入外部系统素材库。 |
| `image_asset_url` | string/null | 最终选中图片素材 URL。视频广告也会保留封面或参考图片 URL。 |
| `video_asset_url` | string/null | 最终选中视频素材 URL。仅视频创意有值；为空时接口会移除该字段。 |
| `material_url` | string/null | 通用素材 URL。用于外部系统只认一个素材地址字段的场景。 |
| `file_url` | string/null | 通用文件 URL。和 `material_url` 类似，便于外部系统下载。 |
| `image_url` | string/null | 图片 URL。和 `image_asset_url` 保持一致，便于外部系统或投放分析读取。 |
| `video_url` | string/null | 视频 URL。和 `video_asset_url` 保持一致；没有视频时会被移除。 |
| `draft` | number | 草稿标记。当前为 `1`。 |

使用建议：

- 图片广告优先读取 `image_url` 或 `image_asset_url`。
- 视频广告优先读取 `video_url` 或 `video_asset_url`。
- 如果外部系统只有一个素材字段，读取 `material_url` 或 `asset_url`。
- `asset_id` 不是必须字段。外部系统下载素材并入库后，可以生成自己的素材 ID。

## `assets.images[]`

图片素材列表。它包含所有本次生成并通过流程保留下来的图片，未必都是最终选中的那一张。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `id` | string/null | AI 系统内部图片素材 ID。 |
| `filename` | string/null | 从 URL 推断出的文件名。 |
| `url` | string/null | 图片访问 URL。 |
| `asset_url` | string/null | 图片素材 URL，和 `url` 保持一致。 |
| `material_url` | string/null | 图片素材 URL，便于外部系统按素材字段读取。 |
| `file_url` | string/null | 图片文件 URL，便于外部系统下载。 |
| `image_url` | string/null | 图片 URL。 |
| `image_asset_url` | string/null | 图片素材 URL。 |
| `type` | string/null | 素材类型，图片为 `image`。 |
| `size` | string/null | 图片尺寸或比例信息，例如 `1024x1024`。 |
| `prompt` | string/null | 生成该图片时使用的提示词。外部投放一般不需要使用，可用于追溯。 |
| `alt_text` | string/null | 图片替代说明或运营备注。 |

使用建议：

- 外部系统创建单条广告时，通常使用 `creative_payload.image_url`。
- 如果要把全部候选素材同步进素材库，再遍历 `assets.images[]`。

## `assets.videos[]`

视频素材列表。它包含本次生成并通过流程保留下来的视频。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `id` | string/null | AI 系统内部视频素材 ID。 |
| `url` | string/null | 视频访问 URL。 |
| `asset_url` | string/null | 视频素材 URL，和 `url` 保持一致。 |
| `material_url` | string/null | 视频素材 URL，便于外部系统按素材字段读取。 |
| `file_url` | string/null | 视频文件 URL，便于外部系统下载。 |
| `video_url` | string/null | 视频 URL。 |
| `video_asset_url` | string/null | 视频素材 URL。 |
| `type` | string/null | 素材类型，视频为 `video`。 |
| `cover_url` | string/null | 视频封面 URL。当前可能为空。 |
| `duration_seconds` | number/null | 视频时长，单位秒。 |
| `storyboard` | array | 视频分镜信息。主要用于运营复盘或后续 AI 分析。 |

使用建议：

- 外部系统创建视频广告时，通常使用 `creative_payload.video_url`。
- 如果需要保存视频生成过程或分镜，再读取 `storyboard`。

## `review`

预审信息，主要帮助外部系统或运营判断这份包是否完整。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `missing_fields` | array | AI 识别或生成阶段发现的缺失字段，例如投放链接、国家等。 |
| `warnings` | array | 需要运营注意的问题，例如多国家只返回第一个国家、非图片创意需要人工确认等。 |
| `low_confidence_fields` | array | AI 识别置信度较低的字段。 |
| `final_precheck` | object | 最终预审勾选状态。 |
| `brand_safety` | object | 品牌安全检查结果。确认回传时会写入。 |

### `review.final_precheck`

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `topic_reviewed` | boolean | 选题已审核。 |
| `copy_reviewed` | boolean | 文案已审核。 |
| `image_reviewed` | boolean | 图片已审核。 |
| `video_reviewed` | boolean | 视频已审核；如果该任务不需要视频，也会为 `true`。 |

### `review.brand_safety`

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `status` | string | 品牌安全状态。常见为 `passed` 或 `blocked`。 |
| `findings` | array | 命中的风险项。 |
| `highest_severity` | string/null | 最高风险等级。 |

当前品牌安全会拦截或提示与赌博、博彩、药品、货币、钱、折扣、低价等相关的内容。若 `status=blocked`，系统不会完成确认回传，需要运营修改或重新生成后再确认。

## `metadata_json`

AI 系统内部关联信息，主要用于排查、复盘和二次分析。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `source` | string | 数据来源，通常是 `publishing_system`。 |
| `workflow_stage` | string | 当前流程阶段。最终预审包通常为 `final_review`，确认回传后任务状态是 `returned`。 |
| `work_order_id` | string | AI 系统内部工单 ID。 |
| `campaign_id` | string | AI 系统内部 Campaign ID。 |
| `topic_id` | string | 选中的选题 ID。 |
| `draft_id` | string | 审核通过的文案草稿 ID。 |
| `creative_asset_ids` | array | 本次预审包包含的图片素材 ID 列表。 |
| `video_asset_ids` | array | 本次预审包包含的视频素材 ID 列表。 |
| `reviewed_delivery_fields` | object | 运营确认后的投放字段快照。 |
| `llm_extraction_review` | object | AI 字段识别的评审信息。 |
| `extraction_source` | string | 字段识别来源，例如 `llm`、`provided` 或本地规则。 |
| `final_prechecked_at` | string | 最终预审包生成时间，ISO 时间字符串。 |

外部系统可以保存 `metadata_json` 作为追溯信息，但创建广告时通常不需要直接使用这些字段。

## 最小可用字段

外部系统如果只想先完成广告创建，建议至少读取：

```text
campaign_payload.name
campaign_payload.objective
adset_payload.daily_budget
adset_payload.billing_event
adset_payload.optimization_goal
adset_payload.bid_strategy
adset_payload.customEventType
adset_payload.countries
adset_payload.age_min
adset_payload.age_max
creative_payload.message
creative_payload.link
creative_payload.ads_name
creative_payload.description
creative_payload.btn_type
creative_payload.image_url 或 creative_payload.video_url
```

## 注意事项

- 所有 `*_url` 字段都是 AI 系统可访问的素材地址。外部系统如果要长期投放，建议下载后入自己的素材库。
- `status=PAUSED` 和 `draft=1` 表示“先建草稿或暂停态”，不表示 AI 系统要求自动上线。
- `customEventType` 是当前优化事件的主字段，外部系统不要再依赖旧的 `optimization_event`。
- `creative_payload` 是最终选中的素材和文案；`assets` 是候选或完整素材列表。
- `metadata_json` 是追溯信息，不建议把它直接映射成投放字段。
