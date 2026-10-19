#!/usr/bin/env bash
# Run a tools/*.py script from this machine against private Postgres and ES, through SSH tunnels on a bastion.
#
#   export BASTION_KEY=~/workspace/ocl/oclinfrastructure/bastion.pem
#   export BASTION_HOST=ec2-user@<bastion public ip>
#   export DB_REMOTE=<rds endpoint>            # DB_REMOTE_PORT defaults to 5432
#   export ES_REMOTE=es.staging.private        # ES_REMOTE_PORT defaults to 9200
#   export DB=postgres DB_USER=... DB_PASSWORD=...
#   tools/run_via_bastion.sh tools/fix_duplicate_latest_concepts.py --source-id 32 --csv plan.csv
#
# The script then sees DB_HOST/ES_HOST as localhost on the tunnel ports. Nothing is written unless the script's
# own --apply is passed.
set -euo pipefail

: "${BASTION_KEY:?}" "${BASTION_HOST:?}" "${DB_REMOTE:?}" "${ES_REMOTE:?}" "${DB_PASSWORD:?}"
DB_LOCAL_PORT="${DB_LOCAL_PORT:-15432}"
ES_LOCAL_PORT="${ES_LOCAL_PORT:-19200}"
SOCKET="$(mktemp -u /tmp/bastion-tunnel.XXXXXX)"

for port in "$DB_LOCAL_PORT" "$ES_LOCAL_PORT"; do
    if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "local port $port is already in use: a tunnel from an earlier run is probably still open." >&2
        echo "close it with: pkill -f 'ssh .*-L ${DB_LOCAL_PORT}:'   (or set DB_LOCAL_PORT / ES_LOCAL_PORT)" >&2
        exit 1
    fi
done

ssh -i "$BASTION_KEY" -o StrictHostKeyChecking=accept-new -o ExitOnForwardFailure=yes \
    -M -S "$SOCKET" -fN \
    -L "${DB_LOCAL_PORT}:${DB_REMOTE}:${DB_REMOTE_PORT:-5432}" \
    -L "${ES_LOCAL_PORT}:${ES_REMOTE}:${ES_REMOTE_PORT:-9200}" \
    "$BASTION_HOST"
trap 'ssh -S "$SOCKET" -O exit "$BASTION_HOST" >/dev/null 2>&1 || true' EXIT INT TERM

DB_HOST=localhost DB_PORT="$DB_LOCAL_PORT" ES_HOST=localhost ES_PORT="$ES_LOCAL_PORT" ES_SCHEME=http \
    "${PYTHON:-venv/bin/python}" -I "$@"
