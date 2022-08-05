from mozilla_django_oidc.auth import OIDCAuthenticationBackend


class OCLOIDCAuthenticationBackend(OIDCAuthenticationBackend):
    def create_user(self, claims):
        """Return object for a newly created user account."""
        # {
        #     'sub': '<str:uuid>',
        #     'email_verified': <boolean>,
        #     'realm_access': {
        #         'roles': ['offline_access', 'default-roles-ocl', 'uma_authorization']
        #     },
        #     'name': 'Inactive User',
        #     'preferred_username': 'inactive',
        #     'given_name': 'Inactive',
        #     'family_name': 'User',
        #     'email': 'inactive@user.com'
        # }
        from core.users.models import UserProfile
        return UserProfile.objects.create_user(
            claims.get('preferred_username'),
            email=claims.get('email'),
            **dict(
                first_name=claims.get('given_name'),
                last_name=claims.get('family_name'),
                verified=claims.get('email_verified')
            )
        )

    def update_user(self, user, claims):
        user.first_name = claims.get('given_name') or user.first_name
        user.last_name = claims.get('family_name') or user.last_name
        user.email = claims.get('email') or user.email
        user.save()
        return user

    def filter_users_by_claims(self, claims):
        from core.users.models import UserProfile

        username = claims.get('preferred_username')

        if not username:
            return UserProfile.objects.none()

        return UserProfile.objects.filter(username=username)
