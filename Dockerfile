FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

COPY netpulse/ ./netpulse/
COPY data/synthetic/ ./data/synthetic/
COPY eval/datasets/ ./eval/datasets/
RUN uv sync --frozen --no-dev

# Ground-truth labels (eval/labels) and the generator (synthgen) are deliberately NOT copied.
ENV PATH="/app/.venv/bin:$PATH"
RUN useradd --create-home netpulse
USER netpulse

# Phase 6: CLI demo. The API service entry point arrives in Phase 9.
ENTRYPOINT ["netpulse"]
CMD ["investigate", "--case", "case-04"]
