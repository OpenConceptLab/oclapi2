from django.test import TestCase

from core.orgs.constants import ORG_OBJECT_TYPE
from .models import Organization


class OrganizationTest(TestCase):
    def test_resource_type(self):
        self.assertEqual(Organization().resource_type(), ORG_OBJECT_TYPE)
