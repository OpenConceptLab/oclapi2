#!/bin/bash
#Sets project build version
set -e

CONFIG_FILE="core/__init__.py"

SHA=${SOURCE_COMMIT:-'dev'}
SHA=${SHA:0:8}

echo "Setting build version to $SHA in $CONFIG_FILE"

sed -i "s/dev/$SHA/" $CONFIG_FILE

