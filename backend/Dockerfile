# syntax=docker/dockerfile:1
FROM python:3.12.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    DOCUMENT_STORAGE_ROOT=/var/lib/flowmind/documents

WORKDIR /app

RUN groupadd --system --gid 10001 flowmind \
    && useradd --system --uid 10001 --gid flowmind --home-dir /nonexistent --shell /usr/sbin/nologin flowmind \
    && install -d -o flowmind -g flowmind /var/lib/flowmind/documents

COPY requirements.txt constraints.txt ./
RUN python -m pip install --constraint constraints.txt --requirement requirements.txt

COPY --chown=flowmind:flowmind alembic.ini ./alembic.ini
COPY --chown=flowmind:flowmind alembic ./alembic
COPY --chown=flowmind:flowmind app ./app

USER flowmind

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
