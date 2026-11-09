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
RUN uv pip install --python /app/.venv/bin/python --no-deps "hermes-agent @ git+https://github.com/NousResearch/hermes-agent@eb52760564dbba2e5971fa54bd67384e281cd3b8"
RUN /app/.venv/bin/python -c "import hermes_cli.auth, hermes_cli.nous_billing"
ENV HERMES_TRADING_MODE=paper
ENV HERMES_TRADING_ALLOW_REAL_ORDERS=false
CMD ["uv", "run", "--no-sync", "python", "-m", "hermes_trading.paper_boot", "--root", "/app/state"]
