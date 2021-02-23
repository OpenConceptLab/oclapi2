#!/bin/bash

set -e

CELERY_WORKER_NAME=${CELERY_WORKER_NAME:-""}
CELERY_WORKER_NAME_WITH_UUID=`cat celery-worker-$CELERY_WORKER_NAME.tmp`

celery inspect ping -A core.celery -d "celery@$CELERY_WORKER_NAME_WITH_UUID"
