# Agent Relay API + dashboard.
#   docker build -t agent-relay:local .
#   docker run --rm -p 8000:8000 agent-relay:local
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

# Dependencies first, in their own layer, so code edits don't reinstall them.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project

COPY *.py dashboard.html ./

# Run as an unprivileged user. SQLite lives in /data (mount a volume there to
# keep it across containers); RELAY_DATABASE_URL overrides it, e.g. PostgreSQL.
RUN useradd --system --uid 10001 relay \
    && mkdir /data && chown relay /data
USER relay
ENV PATH="/app/.venv/bin:$PATH" \
    RELAY_DATABASE_URL=sqlite:////data/agent-relay.db

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=2)"

# 0.0.0.0, not uvicorn's default 127.0.0.1, or `-p` can't reach it.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
