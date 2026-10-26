#!/bin/sh
# How the server starts in its container (see the Dockerfile).
#
# On the first start this builds the tables and loads the reference data and
# the past calls; on every later start `sim.seed` sees the data is there and
# leaves it alone. Then the server runs, listening on every address of the
# container (0.0.0.0) so that it can be reached from outside it. `exec` makes
# it the container's main process, so `docker compose stop` reaches it and it
# shuts down cleanly: the workers finish the calls already queued.
set -e

python -m sim.seed
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log "$@"
