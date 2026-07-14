FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 10001 playground \
    && useradd --uid 10001 --gid playground --create-home playground

WORKDIR /app
COPY pyproject.toml /app/
COPY src /app/src

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

USER playground

ENTRYPOINT ["agent-playground"]
