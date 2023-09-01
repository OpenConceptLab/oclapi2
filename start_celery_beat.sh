#!/bin/bash

set -e

./wait_for_it.sh ${DB_HOST}:${DB_PORT} -t 0
./wait_for_it.sh ${REDIS_HOST}:${REDIS_PORT} -t 0
./wait_for_it.sh ${API_HOST}:${API_PORT} -t 0 # Wait for API to run any DB migrations before running scheduled tasks

celery -A core beat -l info -S django
