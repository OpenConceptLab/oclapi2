import factory
from factory import Sequence, SubFactory

from core.collections.models import Collection, CollectionReference
from core.common.constants import ACCESS_TYPE_EDIT, HEAD
from core.orgs.tests.factories import OrganizationFactory
from core.users.tests.factories import UserProfileFactory


class OrganizationCollectionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Collection

    mnemonic = Sequence("collection{}".format)  # pylint: disable=consider-using-f-string
    name = Sequence("collection{}".format)  # pylint: disable=consider-using-f-string
    collection_type = "Dictionary"
    public_access = ACCESS_TYPE_EDIT
    default_locale = "en"
    supported_locales = ["en"]
    website = 'www.collection.com'
    description = 'This is a test collection'
    organization = SubFactory(OrganizationFactory)
    version = HEAD


class UserCollectionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Collection

    mnemonic = Sequence("collection{}".format)  # pylint: disable=consider-using-f-string
    name = Sequence("collection{}".format)  # pylint: disable=consider-using-f-string
    collection_type = "Dictionary"
    public_access = ACCESS_TYPE_EDIT
    default_locale = "en"
    supported_locales = ["en"]
    website = 'www.collection.com'
    description = 'This is a test collection'
    user = SubFactory(UserProfileFactory)
    version = HEAD


class CollectionReferenceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CollectionReference
