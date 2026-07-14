FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    SETUPTOOLS_SCM_PRETEND_VERSION_FOR_TELEFIRE=0.0.0 \
    SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0

RUN groupadd --gid 10001 telefire \
    && useradd --uid 10001 --gid telefire --create-home --home-dir /home/telefire telefire \
    && mkdir -p /telefire-data \
    && chown telefire:telefire /telefire-data

WORKDIR /app
COPY . /app

RUN chmod -R a+rX /app \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

USER telefire
