FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir pytest pytest-cov ruff bandit hypothesis

WORKDIR /app
