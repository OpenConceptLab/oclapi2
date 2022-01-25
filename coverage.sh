#! /bin/bash
set -e
COVERAGE_FILE=/tmp/.coverage coverage run --parallel-mode --source='core' manage.py test --parallel=4 -v 3 --keepdb
COVERAGE_FILE=/tmp/.coverage coverage combine
COVERAGE_FILE=/tmp/.coverage coverage report -m --include=core/* --fail-under=86 --sort=cover
