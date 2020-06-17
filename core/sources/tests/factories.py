import factory
from factory import Sequence, SubFactory

from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source


class SourceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Source

    mnemonic = Sequence("source-{}".format)
    name = Sequence("source-{}".format)
    source_type = Sequence("source-type-{}".format)
    organization = SubFactory(OrganizationFactory)
    version = Sequence("version-{}".format)
