# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# Stage 1: build — install deps into a venv, compile nothing into the final image
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml ./
COPY src ./src

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --upgrade pip \
    && pip install .

# ---------------------------------------------------------------------------
# Stage 2: runtime — slim, non-root, only what's needed to run
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 recon \
    && useradd --uid 1000 --gid recon --shell /bin/bash --create-home recon

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY src ./src
COPY config ./config
COPY alembic.ini ./
COPY migrations ./migrations

RUN mkdir -p /var/lib/recon/uploads && chown -R recon:recon /app /var/lib/recon

USER recon

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD curl -f http://localhost:8000/health/live || exit 1

EXPOSE 8000

CMD ["uvicorn", "recon.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
