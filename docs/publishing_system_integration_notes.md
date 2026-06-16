# 外部投放系统对接说明

本文档给外部投放系统开发使用。当前推荐流程是跳转模式：投放系统只提供入口，运营点击“AI 生成”后跳转到 AI 项目；工单创建、参数识别、选题、文案、图片、视频和最终预审都在 AI 项目完成；确认后再把结果回传给投放系统。

## 职责边界

投放系统负责：

- 展示“AI 生成”入口按钮。
- 跳转到 AI 项目的创建工单页面。
- 接收 AI 项目最终回跳。
- 根据 `job_id` 查询最终结果。
- 保存素材到自己的素材库。
- 创建广告系列、广告组、广告创意。
- 负责账号、Page、Ad Account、Pixel、按钮类型、发布、暂停、状态同步和报表。

AI 项目负责：

- 创建 AI 工单。
- LLM 识别工单中已有的投放参数；人工确认后创建 AI 任务时会复用本次识别结果，不再重复识别。
- 对缺失参数给出 AI 建议值，并交给人工确认。
- 根据投放链接和工单生成选题。
- 根据选题生成文案。
- 根据文案生成图片。
- 根据图片生成视频。
- 最终预审并回传投放系统。

## 跳转地址

本地测试：

```text
http://127.0.0.1:5173/work-orders/new
```

同 WiFi 联调时，把 `127.0.0.1` 换成 AI 项目电脑的局域网 IP：

```text
http://你的电脑IP:5173/work-orders/new
```

推荐携带参数：

```text
http://你的电脑IP:5173/work-orders/new?source=publishing&external_order_id=投放系统工单ID&return_url=http%3A%2F%2F投放系统地址%2Fai-return
```

可选参数：

```text
raw_content
project_name
country
countries
event_name
audience
age_min
age_max
landing_url
```

如果同时传 `country` 和 `countries`，AI 项目会优先取第一个可识别国家。当前投放系统国家为单选，AI 项目最终返回的 `countries` 是单个国家代码字符串。

## 回跳格式

运营在 AI 项目点击最终确认后，如果入口带了 `return_url`，AI 项目会跳回：

```text
{return_url}?job_id={job_id}&status=returned&external_order_id={external_order_id}
```

投放系统收到回跳后，使用 `job_id` 查询最终预审包 JSON。

## 查询最终结果

```text
GET /api/v1/integrations/publishing/ad-generation/jobs/{job_id}/result
```

本地示例：

```text
GET http://127.0.0.1:8001/api/v1/integrations/publishing/ad-generation/jobs/{job_id}/result
```

同 WiFi 示例：

```text
GET http://你的电脑IP:8001/api/v1/integrations/publishing/ad-generation/jobs/{job_id}/result
```

该接口只在 AI 工单完成最终确认、状态变为 `returned` 后返回 200。如果运营还没有最终确认，会返回 409，表示最终包未就绪。

投放系统重点读取：

```text
campaign_payload
adset_payload
creative_payload
assets.images
assets.videos
review
```

## 最终预审包 JSON 示例

```json
{
  "job_id": "ad-generation-job-id",
  "external_order_id": "publishing-order-id",
  "status": "returned",
  "campaign_payload": {
    "name": "Streaming TV App",
    "objective": "OUTCOME_SALES",
    "status": "PAUSED",
    "draft": 1
  },
  "adset_payload": {
    "name": "US - purchase - 25-45",
    "daily_budget": 5000,
    "billing_event": "IMPRESSIONS",
    "optimization_goal": "OFFSITE_CONVERSIONS",
    "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
    "event_name": "purchase",
    "countries": "US",
    "country_code": "US",
    "country_label": "United States",
    "age_min": 25,
    "age_max": 45,
    "gender": "不限",
    "audience_description": "age 25-45",
    "start_type": "I",
    "start_time": 0,
    "status": "PAUSED",
    "draft": 1
  },
  "creative_payload": {
    "name": "Streaming TV App - video",
    "type": "video",
    "message": "广告主文案",
    "link": "https://example.com/product",
    "ads_name": "广告标题",
    "description": "广告描述",
    "btn_type": "LEARN_MORE",
    "asset_url": "https://example.com/video.mp4",
    "asset_id": null,
    "image_asset_url": "https://example.com/image.png",
    "video_asset_url": "https://example.com/video.mp4",
    "draft": 1
  },
  "assets": {
    "images": [],
    "videos": []
  },
  "review": {
    "missing_fields": [],
    "warnings": [],
    "low_confidence_fields": []
  }
}
```

## AI 项目不决定的字段

```text
auth_user_id
page_id
ad_account_id
pixel_id
asset_id
facebook_campaign_id
facebook_adset_id
facebook_creative_id
facebook_ad_id
delivery_status
spend
impressions
clicks
conversions
```

这些字段由投放系统自己选择、创建、保存或同步。
