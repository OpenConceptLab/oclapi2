db.export.tokens.drop();

cursor = db.auth_user.find()

while (cursor.hasNext()) {
    auth_user = cursor.next();
    tokens = db.authtoken_token.find({user_id: auth_user._id}, {_id: 1});
    if (tokens.hasNext()) {
        token = tokens.next();
        db.export.tokens.insert({username: auth_user.username, id: auth_user._id, token: token._id});
    }
}

print(db.export.tokens.count() + " matching documents found");
