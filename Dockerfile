# OpsPilot AI — serves the ADK dev UI/API for the agent in adk_apps/.
# Local by default: publish only on loopback, e.g.
#   docker build -t opspilot .
#   docker run --rm -p 127.0.0.1:8000:8000 opspilot
# Live Gemini: pass credentials at runtime, never bake them into the image:
#   docker run --rm -p 127.0.0.1:8000:8000 -e OPSPILOT_MODEL_PROVIDER=gemini \
#     -e GOOGLE_API_KEY opspilot
FROM python:3.11-slim

# Optional: behind a TLS-intercepting proxy, supply its CA as a BuildKit secret
# (never baked into a layer):  docker build --secret id=extra_ca,src=ca.pem .
ARG UV_VERSION=0.11.32
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    OPSPILOT_DATA_DIR=/app/var/datasets \
    OPSPILOT_SESSION_DB=sqlite+aiosqlite:////app/var/sessions.db

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then \
      export PIP_CERT=/run/secrets/extra_ca SSL_CERT_FILE=/run/secrets/extra_ca; fi \
 && pip install --no-cache-dir "uv==${UV_VERSION}" \
 && uv sync --frozen --no-dev --no-install-project

COPY opspilot/ ./opspilot/
COPY adk_apps/ ./adk_apps/
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export SSL_CERT_FILE=/run/secrets/extra_ca; fi \
 && uv sync --frozen --no-dev \
 && useradd --create-home --uid 10001 opspilot \
 && mkdir -p /app/var && chown -R opspilot /app/var \
 && su opspilot -c "/app/.venv/bin/opspilot build-data"

USER opspilot
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
# 0.0.0.0 inside the container only; exposure is controlled by `docker run -p`.
CMD ["adk", "web", "--host", "0.0.0.0", "--port", "8000", "adk_apps"]
