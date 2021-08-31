#!/bin/bash

set -e

CELERY_WORKER_NAME=${CELERY_WORKER_NAME:-""}
CELERY_WORKER_NAME_WITH_UUID=`cat celery-worker-$CELERY_WORKER_NAME.tmp`

# Providing broker with -b is more efficient than -A as the app does not have to be initialized on each ping
celery inspect ping -t 10 -d "celery@$CELERY_WORKER_NAME_WITH_UUID" -b redis://${REDIS_HOST:-localhost}:${REDIS_PORT:-6379}/0
