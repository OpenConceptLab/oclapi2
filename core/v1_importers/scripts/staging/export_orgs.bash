#!/usr/bin/env bash

mongoexport --db ocl --collection orgs_organization -q='{"mnemonic": {$in: ["EthiopiaNHDD", "MSFOCP", "Malawi-Demo"]} }' --fields=_id,mnemonic,website,created_at,updated_at,is_active,uri,extras,location,public_access,company,name -o exported_orgs.json