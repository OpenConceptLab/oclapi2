#!/usr/bin/env bash

mongo "localhost:27017/ocl" ./export_tokens.js
mongoexport --db ocl --collection export.tokens -o exported_tokens.json