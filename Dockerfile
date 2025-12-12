FROM python:3.12-slim AS builder
WORKDIR /app

ENV TZ=UTC

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# Build tooling + Poetry backend
RUN pip install --no-cache-dir --upgrade pip build poetry-core

# Copy sources needed for wheel build
COPY pyproject.toml poetry.lock README.md /app/
COPY src /app/src

# Build a wheel (do NOT use --no-isolation unless you really need it)
RUN python -m build --wheel


FROM python:3.12-slim AS runtime
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install the built wheel
COPY --from=builder /app/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && \
    rm -rf /tmp/*.whl

# Create non-root user and runtime dirs
RUN useradd -m -u 10001 appuser \
    && mkdir -p /config /data \
    && chown -R appuser:appuser /config /data

USER appuser

VOLUME ["/config", "/data"]

ENTRYPOINT ["teledigest"]
CMD ["--config", "/config/teledigest.conf"]
