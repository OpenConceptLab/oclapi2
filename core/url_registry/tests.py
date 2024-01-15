from core.common.tests import OCLTestCase
from core.orgs.models import Organization
from core.url_registry.models import URLRegistry
from core.users.models import UserProfile


class URLRegistryTest(OCLTestCase):
    def test_owner(self):
        org = Organization()
        user = UserProfile()
        self.assertEqual(URLRegistry().owner, None)
        self.assertEqual(URLRegistry(organization=org).owner, org)
        self.assertEqual(URLRegistry(user=user).owner, user)

    def test_owner_type(self):
        org = Organization()
        user = UserProfile()
        self.assertEqual(URLRegistry().owner_type, None)
        self.assertEqual(URLRegistry(organization=org).owner_type, 'Organization')
        self.assertEqual(URLRegistry(user=user).owner, 'User')
