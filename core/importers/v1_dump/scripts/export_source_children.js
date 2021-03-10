db.export.concepts.drop();
db.export.conceptversions.drop();
db.export.mappings.drop();
db.export.mappingversions.drop();


org_ids = db.orgs_organization.find({mnemonic: {$in: ["EthiopiaNHDD", "MSF-OCB", "MOH-DM", "IAD", "integrated-impact", "SSAS", "DSME-Test", "GFPVAN", "im", "Kuunika", "DSME", "DSME-CDD", "MOH", "mTOMADY", "IRDO", "ibwighane", "mw-terminology-service", "mw-product-master", "ICI", "mw-terminology-service-development", "mw-product-master-ocl-instance", "mw-product-master-ocl", "malawi-diseases-diagnosis", "TestOrg", "DWB", "CMDF", "MUDHC", "MSF", "MU", "MUDH", "nproto", "MSFTW", "TWABC", "kuunika-registries", "UNIMED", "SHC", "MSFOCP", "SELF", "OpenSandbox", "sandbox", "ATH", "Reverton"]}}, {_id: 1}).map(doc => doc._id.str);
source_oids = db.sources_source.find({parent_id: {$in: org_ids}}, {_id: 1}).map(doc => doc._id);
source_ids = db.sources_source.find({parent_id: {$in: org_ids}}, {_id: 1}).map(doc => doc._id.str);

concept_ids = db.concepts_concept.find({parent_id: {$in: source_ids}}).map(doc => doc._id.str)
db.export.concepts.insertMany(db.concepts_concept.find({parent_id: {$in: source_ids}}).map(doc => doc));
db.export.conceptversions.insertMany(db.concepts_conceptversion.find({versioned_object_id: {$in: concept_ids}}).map(doc => doc));

mapping_ids = db.mappings_mapping.find({parent_id: {$in: source_oids}}).map(doc => doc._id.str)
db.export.mappings.insertMany(db.mappings_mapping.find({parent_id: {$in: source_oids}}).map(doc => doc));
db.export.mappingversions.insertMany(db.mappings_mappingversion.find({versioned_object_id: {$in: mapping_ids}}).map(doc => doc));

print(db.export.concepts.count() + " matching concepts found");
print(db.export.conceptversions.count() + " matching conceptversions found");
print(db.export.mappings.count() + " matching mappings found");
print(db.export.mappingversions.count() + " matching mappingversions found");