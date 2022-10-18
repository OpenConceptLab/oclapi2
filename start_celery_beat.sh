#!/bin/bash

set -e

./wait_for_it.sh ${REDIS_HOST}:${REDIS_PORT} -t 0

celery beat -A core.celeryconf -l info
