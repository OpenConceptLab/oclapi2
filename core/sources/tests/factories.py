import factory
from factory import Sequence, SubFactory

from core.common.constants import ACCESS_TYPE_EDIT, HEAD
from core.orgs.tests.factories import OrganizationFactory
from core.sources.models import Source
from core.users.tests.factories import UserProfileFactory


class OrganizationSourceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Source

    mnemonic = Sequence("source{}".format)  # pylint: disable=consider-using-f-string
    name = Sequence("source{}".format)  # pylint: disable=consider-using-f-string
    source_type = "Dictionary"
    public_access = ACCESS_TYPE_EDIT
    default_locale = "en"
    supported_locales = ["en"]
    website = 'www.source.com'
    description = 'This is a test source'
    organization = SubFactory(OrganizationFactory)
    version = HEAD
    autoid_mapping_mnemonic = None


class UserSourceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Source

    mnemonic = Sequence("source{}".format)  # pylint: disable=consider-using-f-string
    name = Sequence("source{}".format)  # pylint: disable=consider-using-f-string
    source_type = "Dictionary"
    public_access = ACCESS_TYPE_EDIT
    default_locale = "en"
    supported_locales = ["en"]
    website = 'www.source.com'
    description = 'This is a test source'
    user = SubFactory(UserProfileFactory)
    version = HEAD
    autoid_mapping_mnemonic = None
