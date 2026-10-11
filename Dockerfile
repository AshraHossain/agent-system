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
ENV PATH="/app/.venv/bin:$PATH" \
    NETPULSE_DB=/app/.netpulse/netpulse.db
RUN useradd --create-home netpulse && mkdir -p /app/.netpulse && chown netpulse /app/.netpulse
USER netpulse

# One image, three entry points (see docker-compose.yml):
#   api: uvicorn netpulse.api.main:app --host 0.0.0.0 --port 8000
#   ui:  streamlit run netpulse/ui/app.py --server.address 0.0.0.0
#   cli: netpulse investigate --case case-04
EXPOSE 8000 8501
CMD ["netpulse", "--provider", "heuristic", "investigate", "--case", "case-04"]
