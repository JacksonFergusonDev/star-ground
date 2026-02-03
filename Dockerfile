FROM python:3.13-slim

# 1. Install 'uv' from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Prevent Python from writing pyc files and buffering stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# 2. Copy dependency files first (for Docker layer caching)
COPY pyproject.toml uv.lock ./

# 3. Install dependencies using uv
# --frozen: fail if the lockfile is out of sync
# --no-dev: production only (skips pytest, ruff, etc.)
# --no-install-project: install libs first, project code comes later
RUN uv sync --frozen --no-dev --no-install-project

# 4. Copy the rest of the application
COPY . .

# 5. Add the virtual environment to PATH
# uv creates the venv at /app/.venv by default
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8501

# Note: Ensure 'curl' is installed if you keep this healthcheck,
# as python:slim images often lack it.
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0"]
