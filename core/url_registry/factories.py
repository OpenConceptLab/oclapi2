import factory
from factory import Sequence, SubFactory

from core.orgs.tests.factories import OrganizationFactory
from core.url_registry.models import URLRegistry
from core.users.tests.factories import UserProfileFactory


class GlobalURLRegistryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = URLRegistry

    name = Sequence("GlobalRegistry-{}".format)  # pylint: disable=consider-using-f-string
    url = 'https://foo.bar.com'


class OrganizationURLRegistryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = URLRegistry

    name = Sequence("OrgRegistry-{}".format)  # pylint: disable=consider-using-f-string
    url = 'https://foo.bar.com'
    organization = SubFactory(OrganizationFactory)


class UserURLRegistryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = URLRegistry

    name = Sequence("UserRegistry-{}".format)  # pylint: disable=consider-using-f-string
    url = 'https://foo.bar.com'
    user = SubFactory(UserProfileFactory)
