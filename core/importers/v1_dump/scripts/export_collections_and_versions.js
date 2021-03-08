db.export.collections.drop();
db.export.collectionversions.drop();


org_ids = db.orgs_organization.find({mnemonic: {$in: ["EthiopiaNHDD", "MSF-OCB", "MOH-DM", "IAD", "integrated-impact", "SSAS", "DSME-Test", "GFPVAN", "im", "Kuunika", "DSME", "DSME-CDD", "MOH", "mTOMADY", "IRDO", "ibwighane", "mw-terminology-service", "mw-product-master", "ICI", "mw-terminology-service-development", "mw-product-master-ocl-instance", "mw-product-master-ocl", "malawi-diseases-diagnosis", "TestOrg", "DWB", "CMDF", "MUDHC", "MSF", "MU", "MUDH", "nproto", "MSFTW", "TWABC", "kuunika-registries", "UNIMED", "SHC", "MSFOCP", "SELF", "OpenSandbox", "sandbox", "ATH", "Reverton"]}}, {_id: 1}).map(doc => doc._id.str);
collection_ids = db.collections_collection.find({parent_id: {$in: org_ids}}, {_id: 1}).map(doc => doc._id.str);

db.export.collections.insertMany(db.collections_collection.find({parent_id: {$in: org_ids}}).map(doc => doc));
db.export.collectionversions.insertMany(db.collections_collectionversion.find({versioned_object_id: {$in: collection_ids}}).map(doc => doc));

print(db.export.collections.count() + " matching collection found");
print(db.export.collectionversions.count() + " matching collectionversion found");