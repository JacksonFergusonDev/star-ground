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

COPY app.py ./
COPY src/ src/
COPY raw_boms/tayda/ raw_boms/tayda/
COPY .streamlit/config.toml .streamlit/config.toml

RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app
USER app

EXPOSE 8501
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=6 \
    CMD ["python", "-c", "import urllib.request; r = urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=2); assert r.status == 200 and r.read() == b'ok'"]

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.headless=true"]
