#!/usr/bin/env bash

mongoexport --db ocl --collection orgs_organization --fields=_id,mnemonic,website,created_at,updated_at,is_active,uri,extras,location,public_access,company,name -o ../data/exported_orgs.json