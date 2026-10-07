# Application image: the API, the pages and every CLI command.
# The embedding model lives in its own image (see Dockerfile.embed).
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app

FROM base AS deps
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

FROM base AS runtime
RUN useradd --create-home --uid 1000 app
COPY --from=deps /app/.venv /app/.venv
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY domain ./domain
RUN uv sync --locked --no-dev && mkdir -p /app/data && chown -R app:app /app
ENV PATH="/app/.venv/bin:$PATH" ASKNEWS_DATA_DIR=/app/data ASKNEWS_DOMAIN_DIR=/app/domain
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz').read()"
CMD ["uvicorn", "asknews.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
