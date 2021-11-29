import factory
from factory import Sequence

from core.orgs.models import Organization


class OrganizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Organization

    name = Sequence("Org-{}".format)  # pylint: disable=consider-using-f-string
    company = Sequence("Org-Company-{}".format)  # pylint: disable=consider-using-f-string
    website = Sequence("org.{}.com".format)  # pylint: disable=consider-using-f-string
    location = Sequence("location-{}".format)  # pylint: disable=consider-using-f-string
    mnemonic = Sequence("org{}".format)  # pylint: disable=consider-using-f-string
