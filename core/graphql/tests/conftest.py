# Shared helpers for GraphQL tests (usable with Django's TestCase or pytest).
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token

from core.common.constants import SUPER_ADMIN_USER_ID
from core.users.tests.factories import UserProfileFactory


def bootstrap_super_user():
    """Ensure the SUPER_ADMIN user exists and return it."""
    user_model = get_user_model()
    super_user, _ = user_model.objects.get_or_create(
        id=SUPER_ADMIN_USER_ID,
        defaults={
            'username': 'superadmin',
            'email': 'superadmin@example.com',
            'password': 'unused',
            'created_by_id': SUPER_ADMIN_USER_ID,
            'updated_by_id': SUPER_ADMIN_USER_ID,
        },
    )
    return super_user


def create_user_with_token(username: str, super_user=None, password='testpass'):
    """Create a user/profile tied to SUPER_ADMIN and return (user, token)."""
    super_user = super_user or bootstrap_super_user()
    user = UserProfileFactory(
        username=username,
        password=password,
        created_by=super_user,
        updated_by=super_user,
    )
    token, _ = Token.objects.get_or_create(user=user)
    return user, token


def auth_header_for_token(token: Token) -> str:
    return f"Token {token.key}"
