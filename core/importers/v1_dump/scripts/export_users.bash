#!/usr/bin/env bash

mongo "localhost:27017/ocl" ./export_user.js
mongoexport --db ocl --collection export.users -o ../data/exported_users.json