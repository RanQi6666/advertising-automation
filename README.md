# Advertising Automation Backend

AI-assisted backend for advertising topic planning, copywriting, creative generation,
human review, Facebook publishing, and performance feedback.

## What is included

- FastAPI backend only. No frontend is implemented yet.
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
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

Health check:

```text
GET http://127.0.0.1:8000/api/v1/health/live
GET http://127.0.0.1:8000/api/v1/health/ready
```

API docs:

```text
http://127.0.0.1:8000/docs
```

## Architecture

```text
backend/app/api          HTTP routes and request/response boundaries
backend/app/services     Business use cases
backend/app/db           Database sessions, models, repositories
backend/app/integrations External providers: LLM, image, Facebook, storage
backend/app/agents       LangGraph workflow state, nodes, and graph builders
backend/app/workers      Async/background task entry points
```

The default providers are local-safe:

- `LLM_PROVIDER=mock` generates deterministic topics and copy for development.
- `IMAGE_PROVIDER=placeholder` creates creative records without calling an image API.
- `FACEBOOK_DRY_RUN=true` simulates publishing without calling Meta APIs.

Switch providers by changing environment variables and extending the adapter classes.
