# Publishing System Integration Notes

This note records the current integration findings for the external Facebook ad
publishing system at `https://newpixel.messrocts.com/`.

## Positioning

The external publishing system should own:

- Facebook authorization users.
- Ad accounts.
- Facebook pages.
- CTA button type.
- Draft saving, publishing, activation, and status sync.
- Material library persistence and Facebook upload details.

This project should own:

- Work order understanding.
- Campaign/ad set/creative field suggestions.
- Copy generation.
- Image/video generation.
- Returning a structured ad generation package.

## Current Publishing System API Base

The frontend uses:

```text
https://fb.ggcss.xyz/api
```

All paths below are relative to that base URL.

## Existing Publishing System Endpoints

### Auth Users

```text
GET /facebook_ad/auth_list
```

Observed params:

```json
{
  "user_id": "optional"
}
```

Frontend maps auth users to:

```json
{
  "id": "2",
  "fbUserId": "...",
  "fbName": "...",
  "fbEmail": "...",
  "adAccounts": [],
  "pages": []
}
```

The AI service should not choose the auth user. The publishing system should
pass or resolve it.

### Ad Tree

```text
GET /facebook_ad/get_ad
```

Observed params:

```json
{
  "auth_user_id": "2",
  "name": "optional keyword"
}
```

Response items are mapped recursively. Relevant fields:

```json
{
  "id": "local db id",
  "type": "campaign | adset | creative",
  "name": "...",
  "fbId": "...",
  "status": "PAUSED",
  "draft": 1,
  "adId": "ad account id",
  "campaignDbId": "campaign local db id",
  "objective": "OUTCOME_SALES",
  "dailyBudget": 5000,
  "billingEvent": "IMPRESSIONS",
  "optimizationGoal": "LINK_CLICKS",
  "bidStrategy": "LOWEST_COST_WITHOUT_CAP",
  "pixelId": "...",
  "customEventType": "...",
  "countries": "US",
  "ageMin": 18,
  "ageMax": 65,
  "adsetDbId": "adset local db id",
  "pageId": "facebook page id",
  "message": "primary text",
  "link": "landing page URL",
  "adsName": "ad headline",
  "description": "ad description",
  "btnType": "LEARN_MORE",
  "assetId": "material local db id",
  "creativeType": "image | video | carousel",
  "extra": {},
  "createdAt": 0,
  "children": []
}
```

### Create Campaign Draft

```text
POST /facebook_ad/create_campaign
```

Payload:

```json
{
  "ad_id": "ad account id",
  "name": "campaign name",
  "objective": "OUTCOME_SALES",
  "status": "PAUSED",
  "draft": 1
}
```

AI should provide only:

```json
{
  "name": "...",
  "objective": "OUTCOME_SALES",
  "status": "PAUSED",
  "draft": 1
}
```

The publishing system should inject `ad_id`. The AI service includes `draft: 1`
for convenience, but the publishing system can override it.

### Create Ad Set Draft

```text
POST /facebook_ad/create_adset
```

Payload:

```json
{
  "campaign_db_id": "local campaign id",
  "name": "ad set name",
  "daily_budget": 5000,
  "billing_event": "IMPRESSIONS",
  "optimization_goal": "LINK_CLICKS",
  "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
  "pixel_id": "optional",
  "custom_event_type": "optional",
  "countries": "US",
  "age_min": 18,
  "age_max": 65,
  "status": "PAUSED",
  "draft": 1
}
```

AI should provide the selected country as a single country code:

```json
{
  "name": "...",
  "daily_budget": 5000,
  "billing_event": "IMPRESSIONS",
  "optimization_goal": "LINK_CLICKS",
  "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
  "pixel_id": null,
  "custom_event_type": null,
  "countries": "US",
  "country_code": "US",
  "country_label": "美国",
  "age_min": 18,
  "age_max": 65,
  "status": "PAUSED",
  "draft": 1
}
```

The publishing system should inject `campaign_db_id`. The AI service includes
`draft: 1` for convenience, but the publishing system can override it.

The current frontend uses a single-select country field. Although the dropdown
contains multiple country options, one ad set selects one country. If a work
order mentions multiple countries, the AI service should choose the most likely
one only when confidence is high; otherwise it should return a warning requiring
operator confirmation.

### Create Creative Draft

```text
POST /facebook_ad/create_creative
```

Payload:

```json
{
  "adset_db_id": "local adset id",
  "name": "creative name",
  "type": "image",
  "page_id": "facebook page id",
  "message": "primary ad text",
  "link": "landing page URL",
  "ads_name": "headline",
  "description": "description",
  "btn_type": "LEARN_MORE",
  "asset_id": "material local db id",
  "draft": 1
}
```

AI should provide:

```json
{
  "name": "...",
  "type": "image",
  "message": "...",
  "link": "https://example.com",
  "ads_name": "...",
  "description": "...",
  "asset_url": "https://ai-service.example/assets/image.png",
  "asset_id": null,
  "draft": 1
}
```

The publishing system should inject `adset_db_id`, `page_id`, `btn_type`,
and preferably `asset_id` after importing `asset_url` into its material library.
The AI service includes `draft: 1` for convenience, but the publishing system
can override it.

### Material Library

List materials:

```text
GET /facebook_ad/asset
```

Observed params:

```json
{
  "page": 1,
  "limit": 20,
  "file_name": "optional",
  "auth_user_id": "optional",
  "type": "optional"
}
```

Upload material:

```text
POST /facebook_ad/upload_material
Content-Type: multipart/form-data
```

Form fields:

```text
file
auth_user_id
```

Response fields are mapped to:

```json
{
  "id": "material local db id",
  "type": "image | video",
  "fileName": "...",
  "fileUrl": "...",
  "imageHash": "...",
  "videoId": "..."
}
```

Recommended first version: AI returns `asset_url`; publishing system downloads
or uploads it into its own material library and then uses its own `asset_id`.

## Frontend Enum Values

Campaign objectives:

```json
[
  "OUTCOME_TRAFFIC",
  "OUTCOME_SALES",
  "OUTCOME_LEADS",
  "OUTCOME_APP_PROMOTION",
  "OUTCOME_ENGAGEMENT"
]
```

Status:

```json
["ACTIVE", "PAUSED", "DELETED"]
```

Billing event:

```json
["IMPRESSIONS", "LINK_CLICKS"]
```

Optimization goal:

```json
[
  "LINK_CLICKS",
  "LANDING_PAGE_VIEWS",
  "OFFSITE_CONVERSIONS",
  "THRUPLAY",
  "APP_INSTALLS",
  "LEADS"
]
```

Bid strategy:

```json
[
  "LOWEST_COST_WITHOUT_CAP",
  "LOWEST_COST_WITH_BID_CAP",
  "LOWEST_COST_WITH_MIN_ROAS"
]
```

Countries currently shown by the frontend:

```json
["US", "CN", "GB", "CA", "AU", "DE", "FR", "JP"]
```

Creative type:

```json
["image", "video", "carousel"]
```

CTA button type exists in the publishing system but should not be owned by this
AI project:

```json
[
  "LEARN_MORE",
  "SHOP_NOW",
  "SIGN_UP",
  "CONTACT_US",
  "APPLY_NOW"
]
```

## AI Button Status

The `AI 创建` button is present in the Facebook ad page header, but the current
frontend chunk does not bind an `onClick` handler to it. It is effectively a
reserved entry point.

The publishing system developers can wire this button to:

1. Open a work order / AI generation dialog.
2. Call this project's async generation API.
3. Poll or receive callback results.
4. Create campaign, ad set, creative drafts with the returned payloads.
5. Import generated assets into the material library.

## Recommended AI Service Contract

Create async job:

```text
POST /api/v1/integrations/publishing/ad-generation/jobs
```

Request:

```json
{
  "external_order_id": "publishing system order id",
  "callback_url": "optional",
  "work_order": {
    "raw_content": "...",
    "structured_fields": {
      "product_name": "...",
      "country": "US",
      "age_min": 18,
      "age_max": 65,
      "landing_url": "https://example.com",
      "event_name": "purchase"
    }
  },
  "preferences": {
    "creative_type": "image",
    "image_count": 1,
    "video_required": false
  }
}
```

Immediate response:

```json
{
  "job_id": "...",
  "status": "queued"
}
```

Query job:

```text
GET /api/v1/integrations/publishing/ad-generation/jobs/{job_id}
```

GET returns the job record. When `status` is `completed`, the generated package is
available under `result_payload`.

Completed `result_payload` shape:

```json
{
  "job_id": "...",
  "external_order_id": "...",
  "status": "completed",
  "campaign_payload": {
    "name": "AI generated campaign name",
    "objective": "OUTCOME_SALES",
    "status": "PAUSED",
    "draft": 1
  },
  "adset_payload": {
    "name": "AI generated ad set name",
    "daily_budget": 5000,
    "billing_event": "IMPRESSIONS",
    "optimization_goal": "LINK_CLICKS",
    "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
    "pixel_id": null,
    "custom_event_type": null,
    "countries": "US",
    "country_code": "US",
    "country_label": "美国",
    "age_min": 18,
    "age_max": 65,
    "status": "PAUSED",
    "draft": 1
  },
  "creative_payload": {
    "name": "AI generated creative name",
    "type": "image",
    "message": "primary text",
    "link": "https://example.com",
    "ads_name": "headline",
    "description": "description",
    "asset_url": "https://ai-service.example/assets/image.png",
    "asset_id": null,
    "draft": 1
  },
  "assets": {
    "images": [
      {
        "filename": "creative_1.png",
        "url": "https://ai-service.example/assets/image.png",
        "size": "1080x1080",
        "prompt": "...",
        "alt_text": "..."
      }
    ],
    "videos": []
  },
  "review": {
    "missing_fields": [],
    "warnings": [],
    "low_confidence_fields": []
  }
}
```

## Questions For Publishing System Developers

1. Should the publishing system import AI asset URLs by itself, or should the AI
   service call `/facebook_ad/upload_material` directly?
2. If AI service uploads materials directly, can they provide a service token or
   a scoped upload API that does not use an operator login token?
3. If a work order mentions multiple countries, should the publishing system
   create multiple ad sets, or should the operator choose one country manually?
4. What currency unit does `daily_budget` use? Current frontend default is
   `5000`.
5. Is `custom_event_type` expected to use Meta event names such as `PURCHASE`
   and `LEAD`?
6. Should the AI result create one campaign/ad set/creative only, or support
   multiple creative candidates per work order?
