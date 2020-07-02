import factory
from factory import Sequence, SubFactory

from core.common.constants import ACCESS_TYPE_EDIT
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source


class SourceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Source

    mnemonic = Sequence("source{}".format)
    name = Sequence("source{}".format)
    source_type = "Dictionary"
    public_access = ACCESS_TYPE_EDIT
    default_locale = "en"
    supported_locales = ["en"]
    website = 'www.source.com'
    description = 'This is a test source'
    organization = SubFactory(OrganizationFactory)
    version = Sequence("version{}".format)
