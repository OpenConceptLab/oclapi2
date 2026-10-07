#!/bin/bash

set -e

CELERY_WORKER_NAME=${CELERY_WORKER_NAME:-""}
CELERY_WORKER_NAME_WITH_UUID=`cat /temp/celery-worker-$CELERY_WORKER_NAME.tmp`

# -A is needed since the broker URL is built in settings; NO_LM/NO_ENCODER keep that from loading the models
NO_LM=TRUE NO_ENCODER=TRUE celery -A core.celery inspect ping -t 10 -d "celery@$CELERY_WORKER_NAME_WITH_UUID"
