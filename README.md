# Advertising Automation

外部投放系统的 AI 广告内容生产与审核台。

这个项目不是直接替代投放系统，也不再负责 Meta/Facebook 发布。它负责把外部投放系统传来的工单转成可审核的 AI 内容生产流程，产出广告系列、广告组、素材和最终投放数据包，再交回外部投放系统继续发布。

## 当前能力

- FastAPI 后端 API，默认前缀为 `/api/v1`。
- React/Vite 运营工作台，位于 `frontend/web-admin`。
- PostgreSQL + SQLAlchemy async 持久化，Alembic 管理数据库迁移。
- 工单字段识别、人工确认、落地页记录、选题、文案、图片、视频和最终审核。
- 外部投放系统集成任务：创建任务、返回 `review_url`、查询任务、查询最终结果、确认后可回跳或回调。
- LLM 适配器：`mock`、`openai`、`volcengine`。
- 图片适配器：`placeholder`、`volcengine`。
- 视频适配器：`placeholder`、`volcengine`。
- 生成素材当前落本机磁盘，并通过后端 `/storage/...` 静态路径暴露。

当前已经移除的旧能力：

- Meta OAuth、Facebook Page/Ad Account/Pixel 获取。
- Facebook 广告创建、dry-run 发布、一键发布、同步 Meta 状态。
- Meta Insights 报表、PublishJob 审核流。
- Celery/Redis 后台队列。`infra/docker-compose.yml` 仍会启动 Redis 和 MinIO，但当前业务代码不依赖它们。

## 本地启动

要求：

- Python 3.11 或更高版本。
- Node.js 22 或兼容版本。
- Docker Desktop，用于本地 PostgreSQL。

后端：

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
docker compose -f infra/docker-compose.yml up -d postgres
alembic upgrade head
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8001
```

前端：

```powershell
cd frontend/web-admin
npm install
npm run dev
```

默认地址：

- 后端健康检查：`http://127.0.0.1:8001/api/v1/health/live`
- 后端就绪检查：`http://127.0.0.1:8001/api/v1/health/ready`
- API 文档：`http://127.0.0.1:8001/docs`
- 前端工作台：`http://127.0.0.1:5173`

本地 `.env` 里如果 `AI_ADS_ACCESS_TOKEN` 为空，业务 API 不校验 token。需要模拟生产鉴权时，填入一个共享 token，然后用下面任一方式访问：

```http
Authorization: Bearer <token>
```

或：

```text
http://127.0.0.1:5173/work-orders/new?access_token=<token>
```

## 当前工作流

外部投放系统可以调用：

```http
POST /api/v1/integrations/publishing/ad-generation/jobs
Authorization: Bearer <AI_ADS_ACCESS_TOKEN>
```

请求创建后，后端会返回 `job_id` 和 `review_url`。运营人员进入 `review_url` 后，按阶段审核：

```text
queued
processing
fields_review
topic_review
copy_review
image_review
video_review
final_review
reviewing
returned
failed
```

最终确认后：

- 任务状态变为 `returned`。
- 外部系统可查询 `/api/v1/integrations/publishing/ad-generation/jobs/{job_id}/result` 获取最终 JSON。
- 如果创建任务时传了 `callback_url`，后端会 POST 一个轻量事件通知。
- 如果需要浏览器回跳，使用任务里的 `return_url`，否则使用 `.env` 中的 `AI_ADS_RETURN_URL`。

## 环境变量

环境文件分工：

- `.env.example`：本地开发模板，默认使用 `mock` / `placeholder`，不调用真实 AI 服务。
- `.env`：本机真实配置，已被 `.gitignore` 忽略。
- `.env.production.example`：生产部署模板。
- `.env.production`：生产真实配置，已被 `.gitignore` 和 `.dockerignore` 忽略。
- `frontend/web-admin/.env.example`：前端本地 API 地址模板。

关键配置：

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5433/ad_automation
AI_ADS_ACCESS_TOKEN=
AI_ADS_RETURN_URL=
PUBLIC_BASE_URL=http://127.0.0.1:8001
AD_GENERATION_REVIEW_BASE_URL=http://127.0.0.1:5173
```

`PUBLIC_BASE_URL` 用于生成素材 URL 和结果查询 URL。`AD_GENERATION_REVIEW_BASE_URL` 用于生成给外部系统或运营人员打开的审核链接。

本地安全模式：

```env
LLM_PROVIDER=mock
IMAGE_PROVIDER=placeholder
VIDEO_PROVIDER=placeholder
OBJECT_STORAGE_PROVIDER=local
```

接入当前真实 AI 生成能力时：

```env
LLM_PROVIDER=volcengine
IMAGE_PROVIDER=volcengine
VIDEO_PROVIDER=volcengine
VOLCENGINE_API_KEY=your_api_key
VOLCENGINE_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
VOLCENGINE_MODEL=your_ark_text_endpoint_id
VOLCENGINE_IMAGE_MODEL=doubao-seedream-4-5-251128
VOLCENGINE_VIDEO_MODEL=your_seedance_endpoint_id
```

`VOLCENGINE_VIDEO_API_KEY` 可以单独配置；为空时视频生成会复用 `VOLCENGINE_API_KEY` 或 `ARK_API_KEY`。当前素材持久化只实现了 `OBJECT_STORAGE_PROVIDER=local`。

前端本地开发默认请求：

```env
VITE_API_BASE_URL=http://127.0.0.1:8001/api/v1
```

如果需要覆盖默认值，把 `frontend/web-admin/.env.example` 复制为 `frontend/web-admin/.env.local` 后修改。

生产 Docker 前端默认使用 Nginx 反代，所以 `.env.production` 中通常保持：

```env
VITE_API_BASE_URL=/api/v1
```

## 生产部署

准备配置：

```powershell
Copy-Item .env.production.example .env.production
```

至少修改：

- `POSTGRES_PASSWORD`
- `SECRET_KEY`
- `AI_ADS_ACCESS_TOKEN`
- `AI_ADS_RETURN_URL`
- `CORS_ORIGINS`
- `PUBLIC_BASE_URL`
- `AD_GENERATION_REVIEW_BASE_URL`
- `VOLCENGINE_API_KEY`
- `VOLCENGINE_MODEL`
- `VOLCENGINE_VIDEO_MODEL`

启动：

```powershell
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
```

生产 compose 会：

- 启动 PostgreSQL。
- 构建并启动后端。
- 后端启动时执行 `alembic upgrade head`。
- 构建前端静态资源并用 Nginx 提供服务。
- 把后端 `/api/v1/` 和 `/storage/` 通过 Nginx 暴露。

## 常用开发命令

```powershell
pytest
ruff check .
```

前端：

```powershell
cd frontend/web-admin
npm run build
```
