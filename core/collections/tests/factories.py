import factory
from factory import Sequence, SubFactory

from core.collections.models import Collection, CollectionReference
from core.common.constants import ACCESS_TYPE_EDIT, HEAD
from core.orgs.tests.factories import OrganizationFactory


class CollectionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Collection

    mnemonic = Sequence("collection{}".format)
    name = Sequence("collection{}".format)
    collection_type = "Dictionary"
    public_access = ACCESS_TYPE_EDIT
    default_locale = "en"
    supported_locales = ["en"]
    website = 'www.collection.com'
    description = 'This is a test collection'
    organization = SubFactory(OrganizationFactory)
    version = HEAD


class CollectionReferenceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CollectionReference
