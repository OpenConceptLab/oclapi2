#!/usr/bin/env bash
mongo "localhost:27017/ocl" ./export_sources_and_versions.js
mongoexport --db ocl --collection export.sources -o exported_sources.json
mongoexport --db ocl --collection export.sourceversions -o exported_sourceversions.json