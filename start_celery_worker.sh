#!/bin/bash

set -e

./wait_for_it.sh ${REDIS_HOST}:${REDIS_PORT} -t 0

CELERY_WORKER_NAME=${CELERY_WORKER_NAME:-""}
UUID=$(cat /proc/sys/kernel/random/uuid)

celery worker -A core.celery -n "${CELERY_WORKER_NAME}-${UUID}" -l INFO "$@"