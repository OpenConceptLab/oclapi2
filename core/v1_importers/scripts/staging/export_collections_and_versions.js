db.export.collections.drop();
db.export.collectionversions.drop();
db.export.collection_ids.drop()
db.export.collectionversion_ids.drop()


org_ids = db.orgs_organization.find({mnemonic: {$in: ["EthiopiaNHDD", "MSFOCP", "Malawi-Demo"]}}, {_id: 1}).map(doc => doc._id.str);
collection_ids = db.collection_collection.find({ $or: [{parent_id: {$in: org_ids}}, {mnemonic: 'COVID-19-Starter-Set'}]}, {_id: 1}).map(doc => doc._id.str);

db.export.collections.insertMany(db.collection_collection.find({ $or: [{parent_id: {$in: org_ids}}, {mnemonic: 'COVID-19-Starter-Set'}]}).map(doc => doc));
db.export.collectionversions.insertMany(db.collection_collectionversion.find({versioned_object_id: {$in: collection_ids}, mnemonic: {$ne: 'HEAD'}}).map(doc => doc));
db.export.collection_ids.insertMany(db.collection_collection.find({}).map(doc => ({_id: doc._id, uri: doc.uri})))
db.export.collectionversion_ids.insertMany(db.collection_collectionversion.find({}).map(doc => ({_id: doc._id, uri: doc.uri})))

print(db.export.collections.count() + " matching collection found");
print(db.export.collectionversions.count() + " matching collectionversion found");