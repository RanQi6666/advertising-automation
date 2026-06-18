# 新架构说明

## 定位

AI 项目现在是“广告内容生产与人工审核工作台”，不是投放执行系统。

外部投放系统负责投放账号、广告系列/广告组/广告创意创建、素材入库、发布、暂停、状态同步和报表。AI 项目只负责把工单变成可审核、可回传的内容生产结果。

## 系统边界

AI 项目负责：

- 创建和保存 AI 工单。
- 识别工单里的投放参数。
- 对缺失参数给出建议值，并交给运营确认。
- 根据投放链接和工单生成选题。
- 根据选题生成文案。
- 根据文案生成图片。
- 根据图片生成视频。
- 人工审核文案、图片、视频。
- 最终预审并回传投放系统需要的数据。

外部投放系统负责：

- 登录、账号、Page、Ad Account、Pixel。
- 广告系列、广告组、广告创意的真实创建。
- 按钮类型、素材库 ID、广告 ID、投放状态。
- 激活、暂停、同步状态和报表。

## 保留接口

- `GET /api/v1/integrations/publishing/ad-generation/jobs`
- `POST /api/v1/integrations/publishing/ad-generation/jobs`
- `GET /api/v1/integrations/publishing/ad-generation/jobs/{job_id}`
- `GET /api/v1/integrations/publishing/ad-generation/jobs/{job_id}/result`
- `PATCH /api/v1/integrations/publishing/ad-generation/jobs/{job_id}/review`
- `POST /api/v1/integrations/publishing/ad-generation/jobs/{job_id}/confirm`

`publishing` 在这里表示“外部投放系统集成命名空间”，不表示 AI 项目直接投放广告。
