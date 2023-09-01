#!/bin/bash

set -e

./wait_for_it.sh ${REDIS_HOST}:${REDIS_PORT} -t 0

UUID=$(cat /proc/sys/kernel/random/uuid | cut -c1-8)
CELERY_WORKER_NAME=${CELERY_WORKER_NAME:-""}
CELERY_WORKER_NAME_WITH_UUID="${CELERY_WORKER_NAME}-${UUID}"

echo "$CELERY_WORKER_NAME_WITH_UUID" > "/temp/celery-worker-$CELERY_WORKER_NAME.tmp"

celery -A core worker -n $CELERY_WORKER_NAME_WITH_UUID --loglevel=INFO "$@" -E