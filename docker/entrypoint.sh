#!/usr/bin/env bash
# Container entrypoint.
#
# `migrate`, `check-config`, `doctor` and `invite` run as-is. `run` applies
# migrations first unless RUN_MIGRATIONS=false, so a fresh deployment comes up
# against a schema that matches the code. Nothing here is hardcoded: the database
# URL and every other setting come from the environment.
set -euo pipefail

command="${1:-run}"

if [ "$command" = "run" ] && [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    echo "[entrypoint] applying database migrations"
    python -m the_sun migrate
fi

if [ "${CHECK_CONFIG_ON_START:-true}" = "true" ]; then
    echo "[entrypoint] validating configuration"
    python -m the_sun check-config
fi

echo "[entrypoint] starting: the-sun $command"
exec python -m the_sun "$@"
