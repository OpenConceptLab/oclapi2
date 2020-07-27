#!/bin/sh
celery worker -A core.celery -l INFO -n default --autoscale=15,3
