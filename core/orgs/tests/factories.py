import factory
from factory import Sequence

from core.orgs.models import Organization


class OrganizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Organization

    name = Sequence("Org-{}".format)
    company = Sequence("Org-Company-{}".format)
    website = Sequence("org.{}.com".format)
    location = Sequence("location-{}".format)
    mnemonic = Sequence("org{}".format)
