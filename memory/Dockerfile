FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 10001 memory \
    && useradd --uid 10001 --gid memory --create-home memory

WORKDIR /app
COPY pyproject.toml /app/
COPY src /app/src

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

USER memory

ENTRYPOINT ["memory-inspector"]
