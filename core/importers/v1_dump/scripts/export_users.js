db.export.users.drop();

cursor = db.auth_user.find();

while (cursor.hasNext()) {
    auth_user = cursor.next();
    profiles = db.users_userprofile.find({"user_id": auth_user._id}, {company: 1, location: 1, _id: 0, verified_email: 1, user_id: 1, hashed_password: 1, full_name: 1, preferred_locale: 1, organizations: 1, extras: 1, public_access: 1, verified_email: 1});
    if (profiles.hasNext()) {
        user_profile = profiles.next();
        db.export.users.insert(Object.assign({}, auth_user, user_profile));
    }
}

print(db.export.users.count() + " matching documents found");