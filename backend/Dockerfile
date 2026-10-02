# CloudGuard backend: FastAPI API and Celery workers share one image.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl is used by the container healthcheck; build-essential is only needed if a
# wheel is unavailable for this platform, so it is removed again afterwards.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt \
    && apt-get purge -y --auto-remove build-essential

# Warm Prophet's Stan backend at build time so the first forecast is not slowed
# by it, and fail the build early if the ML stack is broken in this image.
RUN python -c "import prophet, sklearn, pandas; print('ML stack OK:', prophet.__version__)"

COPY . .

# Run as a non-root user.
RUN useradd --create-home --uid 10001 cloudguard \
    && chown -R cloudguard:cloudguard /app
USER cloudguard

EXPOSE 8000

# This probe suits the API command. The worker and beat services run different
# commands from this same image and override it in docker-compose.yml, because
# neither of them serves HTTP.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

# --proxy-headers lets uvicorn take the client address from X-Forwarded-For, but
# only from peers listed in the FORWARDED_ALLOW_IPS environment variable
# (default 127.0.0.1). Set it to your platform proxy's range - see "Client IPs
# behind a proxy" in the README. No --forwarded-allow-ips flag here on purpose:
# it would override the variable. Never use "*": clients could spoof their IP.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
