#!/bin/bash

set -e

./wait_for_it.sh ${REDIS_HOST}:${REDIS_PORT} -t 0

celery worker -A core.celery -l INFO -n default --autoscale=15,3
