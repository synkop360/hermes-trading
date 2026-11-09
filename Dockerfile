FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates git && rm -rf /var/lib/apt/lists/*
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"
COPY pyproject.toml uv.lock ./
COPY hermes_trading ./hermes_trading
COPY scripts ./scripts
RUN uv sync --locked
# Reuse only the existing Nous credential resolver and its rotation lock.
# No agent loop, tools, billing mutation or model fallback is invoked.
RUN sh scripts/install_nous_auth.sh /opt/hermes-auth /app/.venv/bin/python
ENV HERMES_TRADING_MODE=paper
ENV HERMES_TRADING_ALLOW_REAL_ORDERS=false
CMD ["uv", "run", "--no-sync", "python", "-m", "hermes_trading.paper_boot", "--root", "/app/state"]
