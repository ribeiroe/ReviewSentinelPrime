# ─────────────────────────────────────────────────────────────────────────────
#  Review Sentinel Prime — Production Dockerfile
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim

# Set working directory inside the container
WORKDIR /app

# Install system dependencies (curl is used for the health-check probe)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency list first so Docker can cache this layer
COPY requirements.txt .

# Install Python dependencies plus gunicorn (production WSGI server)
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copy the rest of the application (data/ is excluded via .dockerignore)
COPY . .

# Create the data directories that the app expects at runtime.
# These are overridden at run-time by the volume mount, but the directories
# must exist in the image so the app starts without errors if no volume is used.
# They must be owned by the runtime user, or without a volume the non-root
# app cannot write API keys / saved searches.
RUN addgroup --system rsp && adduser --system --ingroup rsp --no-create-home rsp \
    && mkdir -p data/results \
    && chown -R rsp:rsp /app/data
USER rsp

# The internal port the app listens on
EXPOSE 5000

# Single worker prevents the APScheduler from starting twice
CMD ["gunicorn", "--workers=1", "--bind=0.0.0.0:5000", "--timeout=120", "--access-logfile=-", "app:app"]
