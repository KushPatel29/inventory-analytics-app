FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps
RUN apt-get update -y && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# App code
COPY . .
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

# One worker on purpose: the analysis lives in process memory, so with two
# workers an uploaded workbook is only visible to whichever one received it.
ENV PORT=8000 \
    GUNICORN_CMD_ARGS="--workers=1 --threads=4 --timeout=120" \
    DEMO_AUTOLOAD=1 \
    SESSION_COOKIE_SECURE=1

EXPOSE 8000

USER appuser

CMD exec gunicorn -b 0.0.0.0:${PORT} wsgi:app
