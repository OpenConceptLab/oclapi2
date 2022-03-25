#! /bin/bash
set -e

./wait_for_it.sh ${ES_HOST}:${ES_PORT} -t 0

COVERAGE_FILE=/tmp/.coverage coverage run --parallel-mode --source='core' manage.py test --parallel=1 -v 3 --keepdb
COVERAGE_FILE=/tmp/.coverage coverage combine
COVERAGE_FILE=/tmp/.coverage coverage report -m --include=core/* --fail-under=88 --sort=cover
