#!/bin/bash

set -e

./wait_for_it.sh ${REDIS_HOST}:${REDIS_PORT} -t 0

celery -A core.celery flower --basic_auth=${FLOWER_USER}:${FLOWER_PASSWORD} --conf=flowerconfig.py
