# ===========================================================================
# Multi-stage Dockerfile for the LXD Management API
# ===========================================================================
# Build stage installs dependencies into a virtualenv; runtime stage copies
# only the venv + app code for a minimal image.
# ===========================================================================

# ---- Build stage ----------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build tooling.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev && \
    rm -rf /var/lib/apt/lists/*

# Create a virtualenv and install production deps.
COPY requirements.txt .
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ---- Runtime stage ---------------------------------------------------------
FROM python:3.12-slim

# Add labels for OCI/Docker image metadata.
LABEL maintainer="LXD Management API"
LABEL org.opencontainers.image.source="https://github.com/your-org/lxd-api"

WORKDIR /app

# Copy only the venv from the build stage.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy the application source.
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./

# Create the data directory for SQLite.
RUN mkdir -p /app/data

# Expose the API port (configurable via APP_PORT, default 8000).
EXPOSE 8000

# Run behind uvicorn with 4 workers for production.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "4", \
     "--log-level", "info"]
