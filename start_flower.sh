#!/bin/sh
celery -A core.celery flower --basic_auth=${FLOWER_USER}:${FLOWER_PASSWORD} --conf=flowerconfig.py
