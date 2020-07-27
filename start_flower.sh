#!/bin/sh
celery -A core.celery flower --conf=flowerconfig.py
