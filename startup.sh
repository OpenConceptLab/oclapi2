#!/bin/bash

set -e

if [[ "$SKIP_MIGRATE_DB" != "true" ]]; then
    /bin/bash ./pre_startup.sh
else
  ./wait_for_it.sh ${DB_HOST}:${DB_PORT} -t 0

  if [[ -z "$ES_HOSTS" ]]; then
  ./wait_for_it.sh ${ES_HOST}:${ES_PORT} -t 0
  else
  set +e
  ES_HOSTS_LIST=(${ES_HOSTS//,/ })
  for ES in "${ES_HOSTS_LIST[@]}"; do
    ./wait_for_it.sh ${ES} -t 180
  set -e
  done
  fi

  ./wait_for_it.sh ${REDIS_HOST}:${REDIS_PORT} -t 0
fi


if [[ "$ENVIRONMENT" = "development" ]]; then
  echo "Starting up the development server"
  python manage.py runserver 0.0.0.0:${API_PORT:-8000}
else
  echo "Starting up the production server"
  gunicorn core.wsgi:application --bind 0.0.0.0:${API_PORT:-8000} --capture-output --workers 4 --timeout 600 --keep-alive 600 #match ALB
fi
