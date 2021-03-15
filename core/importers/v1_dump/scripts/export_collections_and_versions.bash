#!/usr/bin/env bash
mongo "localhost:27017/ocl" ./export_collections_and_versions.js
mongoexport --db ocl --collection export.collections -o exported_collections.json
mongoexport --db ocl --collection export.collection_ids -o exported_collection_ids.json
mongoexport --db ocl --collection export.collectionversion_ids -o exported_collectionversion_ids.json
mongoexport --db ocl --collection export.collectionversions -o exported_collectionversions.json