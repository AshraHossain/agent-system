FROM python:3.12-slim
WORKDIR /app
RUN pip install uv
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev
COPY trafficflow/ trafficflow/
EXPOSE 7860
CMD ["uv", "run", "trafficflow", "serve", "--host", "0.0.0.0", "--port", "7860"]
