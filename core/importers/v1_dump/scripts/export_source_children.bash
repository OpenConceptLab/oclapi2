#!/usr/bin/env bash
mongo "localhost:27017/ocl" ./export_sources_children.js
mongoexport --db ocl --collection export.concepts -o ../data/exported_concepts.json
mongoexport --db ocl --collection export.conceptversions -o ../data/exported_conceptversions.json
mongoexport --db ocl --collection export.mappings -o ../data/exported_mappings.json
mongoexport --db ocl --collection export.mappingversions -o ../data/exported_mappingversions.json