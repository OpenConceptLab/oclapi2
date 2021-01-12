#!/bin/bash

set -e

./wait_for_it.sh ${DB_HOST}:${DB_PORT} -t 0
./wait_for_it.sh ${ES_HOST}:${ES_PORT} -t 0
./wait_for_it.sh ${REDIS_HOST}:${REDIS_PORT} -t 0

echo "Running DB migrations"
python manage.py migrate

echo "Importing base entities"
python manage.py loaddata core/fixtures/*

echo "Setting up superuser"
python manage.py setup_superuser

echo "Importing lookup values"
python manage.py import_lookup_values

echo "Starting up the server"
python manage.py runserver 0.0.0.0:${API_PORT:-8000}
