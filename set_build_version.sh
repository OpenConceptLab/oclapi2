#!/bin/bash
#Sets project build version
set -e

CONFIG_FILE="core/__init__.py"

SHA=$(./release_version.sh sha "${SOURCE_COMMIT:-}")

echo "Setting build version to $SHA in $CONFIG_FILE"

sed -i "s/dev/$SHA/" $CONFIG_FILE

