# One image is a whole Star Ground: Node builds the website, then Python
# serves it at / beside the API at /api. Cloud Run runs this same image.

FROM node:24-bookworm@sha256:6449e19d8184d84062525b6a7524def03146c2cb04a7f08b076680d0e8e4240a AS site

# The site reads Star Ground's accent from branding/, and the circuit
# catalog's pages from catalog/, beside web/.
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY branding/ /src/branding/
COPY catalog/ /src/catalog/
COPY web/ ./
# Same origin as the API, so the site calls /api directly.
ENV PUBLIC_API_URL=""
RUN npm run build

FROM python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4

COPY --from=ghcr.io/astral-sh/uv:latest@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-cache

# Models belong to the image, not a first user's network connection or cache.
RUN .venv/bin/python -c 'from rapidocr import RapidOCR; RapidOCR()'

COPY src/ src/
COPY raw_boms/tayda/ raw_boms/tayda/
COPY --from=site /src/web/dist/ site/

RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app
USER app

ENV STAR_GROUND_SITE_DIR=/app/site
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=6 \
    CMD ["python", "-c", "import os, urllib.request; r = urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/api/health', timeout=2); assert r.status == 200"]

# Cloud Run sets PORT; it gives a stopping instance 10 s after SIGTERM.
CMD ["sh", "-c", "exec uvicorn src.server:app --host 0.0.0.0 --port \"${PORT:-8000}\" --timeout-graceful-shutdown 8"]
