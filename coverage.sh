#! /bin/bash
LOG=0 coverage run --parallel-mode --source='core' manage.py test --parallel=4 -v 3 --keepdb
coverage combine
coverage report -m --include=core/* --fail-under=100
