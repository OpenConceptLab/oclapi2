#!/usr/bin/env bash
mongo "localhost:27017/ocl" ./export_source_children.js
mongoexport --db ocl --collection export.concept_ids -o exported_concept_ids.json
mongoexport --db ocl --collection export.concepts -o exported_concepts.json
mongoexport --db ocl --collection export.conceptversions -o exported_conceptversions.json
mongoexport --db ocl --collection export.mappings -o exported_mappings.json
mongoexport --db ocl --collection export.mappingversions -o exported_mappingversions.json