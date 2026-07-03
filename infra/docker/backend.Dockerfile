FROM python:3.13-slim

ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL}

WORKDIR /app

COPY pyproject.toml README.md alembic.ini ./
COPY backend ./backend

RUN python -m pip install --upgrade pip --retries 10 --timeout 60 --resume-retries 10 \
    && python -m pip install --retries 10 --timeout 60 --resume-retries 10 .

EXPOSE 8001

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8001"]
