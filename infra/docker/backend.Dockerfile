FROM python:3.13-slim

ARG APT_MIRROR=
ARG APT_SECURITY_MIRROR=
ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL}

WORKDIR /app

RUN set -eux; \
    if [ -n "${APT_MIRROR}" ]; then \
        if [ -n "${APT_SECURITY_MIRROR}" ]; then \
            sed -i "s#http://deb.debian.org/debian-security#${APT_SECURITY_MIRROR}#g" /etc/apt/sources.list.d/debian.sources; \
        else \
            sed -i "s#http://deb.debian.org/debian-security#${APT_MIRROR}-security#g" /etc/apt/sources.list.d/debian.sources; \
        fi; \
        sed -i "s#http://deb.debian.org/debian#${APT_MIRROR}#g" /etc/apt/sources.list.d/debian.sources; \
    fi; \
    apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md alembic.ini ./
COPY backend ./backend

RUN python -m pip install --upgrade pip --retries 10 --timeout 60 --resume-retries 10 \
    && python -m pip install --retries 10 --timeout 60 --resume-retries 10 .

EXPOSE 8001

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8001"]
