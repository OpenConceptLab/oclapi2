#!/usr/bin/env bash
mongo "localhost:27017/ocl" ./export_collections_and_versions.js
mongoexport --db ocl --collection export.collections -o ../data/exported_collections.json
mongoexport --db ocl --collection export.collectionversions -o ../data/exported_collectionversions.json