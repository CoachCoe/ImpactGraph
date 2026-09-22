# syntax=docker/dockerfile:1
# Build context is the repository root: the image needs the compiled contract ABI, which
# the EVM adapter loads at runtime to decode registry events.
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

FROM base AS build
COPY apps/api/pyproject.toml ./
COPY apps/api/impactgraph ./impactgraph
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install .

FROM base AS runtime
COPY --from=build /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY apps/api/impactgraph ./impactgraph
COPY apps/api/alembic ./alembic
COPY apps/api/alembic.ini ./alembic.ini
COPY contracts/out/ImpactRegistry.sol/ImpactRegistry.json \
     /app/contracts/out/ImpactRegistry.sol/ImpactRegistry.json

ENV CONTRACT_ARTIFACT_PATH=/app/contracts/out/ImpactRegistry.sol/ImpactRegistry.json \
    EVIDENCE_STORAGE_PATH=/data/evidence

RUN useradd --system --uid 10001 impactgraph \
 && mkdir -p /data/evidence \
 && chown -R impactgraph:impactgraph /data
USER impactgraph

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live',timeout=3).status==200 else 1)"

CMD ["uvicorn", "impactgraph.main:app", "--host", "0.0.0.0", "--port", "8000"]
