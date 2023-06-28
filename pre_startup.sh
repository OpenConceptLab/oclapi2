#!/bin/bash

set -e

./wait_for_it.sh ${DB_HOST}:${DB_PORT} -t 0

if [[ -z "$ES_HOSTS" ]]; then
./wait_for_it.sh ${ES_HOST}:${ES_PORT} -t 0
else
set +e
ES_HOSTS_LIST=(${ES_HOSTS//,/ })
for ES in "${ES_HOSTS_LIST[@]}"; do
  ./wait_for_it.sh ${ES} -t 180
done
set -e
fi

./wait_for_it.sh ${REDIS_HOST}:${REDIS_PORT} -t 0

echo "Running DB migrations"
python manage.py migrate
echo "Importing base entities"
python manage.py loaddata core/fixtures/*

echo "Setting up superuser"
python manage.py setup_superuser

echo "Importing lookup values"
python manage.py import_lookup_values

echo "Populating text from extras.about"
python manage.py populate_text_from_extras_about
