from django.test import TestCase

from core.orgs.constants import ORG_OBJECT_TYPE
from .models import Organization


class OrganizationTest(TestCase):
    def test_resource_type(self):
        self.assertEqual(Organization().resource_type(), ORG_OBJECT_TYPE)

    def test_org_id(self):
        self.assertEqual(Organization().org_id, None)
        self.assertEqual(Organization(id=1).org_id, 1)

    def test_members(self):
        org = Organization(id=123)
        self.assertEqual(org.members.count(), 0)
