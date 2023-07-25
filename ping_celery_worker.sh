#!/bin/bash

set -e

CELERY_WORKER_NAME=${CELERY_WORKER_NAME:-""}
CELERY_WORKER_NAME_WITH_UUID=`cat /temp/celery-worker-$CELERY_WORKER_NAME.tmp`

# Providing broker with -b is more efficient than -A as the app does not have to be initialized on each ping
celery -A core.celery inspect ping -t 60 -d "celery@$CELERY_WORKER_NAME_WITH_UUID"
