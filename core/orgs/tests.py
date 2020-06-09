from django.test import TestCase

from core.orgs.constants import ORG_OBJECT_TYPE
from .models import Organization


class OrganizationTest(TestCase):
    def test_resource_type(self):
        self.assertEqual(Organization().resource_type(), ORG_OBJECT_TYPE)

    def test_org(self):
        self.assertEqual(Organization().org, '')
        self.assertEqual(Organization(mnemonic='blah').org, 'blah')

    def test_members(self):
        org = Organization(id=123)
        self.assertEqual(org.members.count(), 0)
