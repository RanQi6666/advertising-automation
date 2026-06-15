# Advertising Automation

AI-assisted operations system for advertising topic planning, copywriting, creative
generation, human review, Facebook publishing, and performance feedback.

## What is included

- FastAPI backend API.
- React/Vite web admin at `frontend/web-admin`.
- PostgreSQL-first persistence with SQLAlchemy async models.
- LangGraph workflow skeleton for durable, human-in-the-loop ad generation.
- Replaceable adapters for LLM, image generation, and Facebook APIs.
- Local infrastructure via Docker Compose: PostgreSQL, Redis, and MinIO.
- Alembic migration scaffold plus an initial schema migration.

## Local setup

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
docker compose -f infra/docker-compose.yml up -d
alembic upgrade head
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8001
```

Health check:

```text
GET http://127.0.0.1:8001/api/v1/health/live
GET http://127.0.0.1:8001/api/v1/health/ready
```

API docs:

```text
http://127.0.0.1:8001/docs
```

Web admin:

```powershell
cd frontend/web-admin
npm install
npm run dev
```

The admin opens at:

```text
http://127.0.0.1:5173
```

The default API base URL is `http://127.0.0.1:8001/api/v1`. Override it with:

```env
VITE_API_BASE_URL=http://127.0.0.1:8001/api/v1
```

## Architecture

```text
backend/app/api          HTTP routes and request/response boundaries
backend/app/services     Business use cases
backend/app/db           Database sessions, models, repositories
backend/app/integrations External providers: LLM, image, Facebook, storage
backend/app/agents       LangGraph workflow state, nodes, and graph builders
backend/app/workers      Async/background task entry points
frontend/web-admin       React admin console for the operator workflow
```

The default providers are local-safe:

- `LLM_PROVIDER=mock` generates deterministic topics and copy for development.
- `IMAGE_PROVIDER=placeholder` creates creative records without calling an image API.
- `FACEBOOK_DRY_RUN=true` simulates publishing without calling Meta APIs.

Switch providers by changing environment variables and extending the adapter classes.

## LLM provider

The text LLM provider can be switched without changing business code.

Local mock mode:

```env
LLM_PROVIDER=mock
```

Volcengine Ark mode:

```env
LLM_PROVIDER=volcengine
VOLCENGINE_API_KEY=your_api_key
VOLCENGINE_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
VOLCENGINE_MODEL=doubao-seed-1-8-251228
```

Image generation can also use Volcengine Ark:

```env
IMAGE_PROVIDER=volcengine
VOLCENGINE_IMAGE_MODEL=doubao-seedream-4-5-251128
VOLCENGINE_IMAGE_SIZE=2K
VOLCENGINE_IMAGE_WATERMARK=false
```

Use `IMAGE_PROVIDER=placeholder` when you want to create creative records without
calling the real image API.

## Facebook publishing

Keep dry-run enabled until Meta permissions and test assets are ready:

```env
FACEBOOK_DRY_RUN=true
FACEBOOK_GRAPH_API_VERSION=v24.0
FACEBOOK_APP_ID=
FACEBOOK_APP_SECRET=
FACEBOOK_PAGE_ID=
FACEBOOK_PAGE_ACCESS_TOKEN=
FACEBOOK_AD_ACCOUNT_ID=act_xxxxx
FACEBOOK_AD_ACCESS_TOKEN=
```

Publish jobs store token references such as `facebook_page_default` and
`facebook_ad_default`; the raw tokens stay in `.env`.
