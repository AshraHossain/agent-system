FROM python:3.11-slim

# Install uv (fast Python package manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install dependencies first for better layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Copy application code
COPY app/ ./app/
COPY tools/ ./tools/
COPY memory/ ./memory/

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Entry point: app/main.py defines `app = FastAPI()`, imported as app.main:app.
# Call uvicorn directly (venv is already synced and on PATH) rather than via
# `uv run`, which would try to re-sync/build the project package at runtime
# using pyproject.toml's `readme = "PLANNING.md"` reference — a file not
# copied into this image.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
