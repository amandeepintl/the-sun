# The Sun - production image.
#
# Two stages: wheels are built in the first one so the runtime image carries no
# compilers or build headers. The application runs as an unprivileged user and the
# image contains no secrets - everything dynamic comes from the environment.
FROM python:3.14-slim AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --prefix=/install --no-warn-script-location -r requirements.txt


FROM python:3.14-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_ENV=production

# `tini` forwards signals so SIGTERM shuts the bot down gracefully; `curl` is used
# by the container health check.
RUN apt-get update \
    && apt-get install --no-install-recommends -y tini curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 thesun

COPY --from=build /install /usr/local

WORKDIR /app

# Only what the bot needs at runtime: the package, the migrations and the tests
# are excluded, and no `.env` is copied.
COPY --chown=thesun:thesun src/ ./src/
COPY --chown=thesun:thesun migrations/ ./migrations/
COPY --chown=thesun:thesun alembic.ini README.md ./
COPY --chown=thesun:thesun docker/entrypoint.sh /usr/local/bin/the-sun-entrypoint

# The package is imported from source through PYTHONPATH rather than installed:
# no build backend is needed in the runtime image, and the code that runs is
# exactly the code that was copied in.
ENV PYTHONPATH=/app/src

RUN chmod +x /usr/local/bin/the-sun-entrypoint

USER thesun

# Liveness only; readiness (which checks PostgreSQL and Redis) is served on the
# same port at /readyz when METRICS_ENABLED is true.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1:${HEALTH_PORT:-8080}/healthz || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/the-sun-entrypoint"]
CMD ["run"]
