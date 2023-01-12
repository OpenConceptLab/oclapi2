#!/bin/bash

set -e

./wait_for_it.sh ${DB_HOST}:${DB_PORT} -t 0
./wait_for_it.sh ${ES_HOST}:${ES_PORT} -t 0
./wait_for_it.sh ${REDIS_HOST}:${REDIS_PORT} -t 0

if [[ "$SKIP_MIGRATE_DB" != "true" ]]; then
    /bin/bash ./pre_startup.sh
fi


if [[ "$ENVIRONMENT" = "development" ]]; then
  echo "Starting up the development server"
  python manage.py runserver 0.0.0.0:${API_PORT:-8000}
else
  echo "Collect static files"
  python manage.py collectstatic
  echo "Starting up the production server"
  gunicorn core.wsgi:application --bind 0.0.0.0:${API_PORT:-8000} --capture-output --workers 4 --timeout 60 --keep-alive 60 #match ALB
fi
