import factory
from factory import Sequence, SubFactory

from core.common.constants import HEAD
from core.concepts.models import Concept, ConceptName, ConceptDescription
from core.sources.tests.factories import OrganizationSourceFactory


def sync_latest_version(self):
    latest_version = self.get_latest_version()
    if not latest_version:
        latest_version = self.clone()
        latest_version.save()
        self.is_latest_version = False
        self.save()
        latest_version.version = latest_version.id
        latest_version.save()
        latest_version.sources.add(latest_version.parent)
    if latest_version:
        if self.names.exists() and not latest_version.names.exists():
            latest_version.set_locales(self.names.all(), ConceptName)
        if self.descriptions.exists() and not latest_version.descriptions.exists():
            latest_version.set_locales(self.descriptions.all(), ConceptDescription)


class ConceptFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Concept

    mnemonic = Sequence("concept{}".format)  # pylint: disable=consider-using-f-string
    version = HEAD
    parent = SubFactory(OrganizationSourceFactory)
    concept_class = "Diagnosis"
    datatype = "None"

    @factory.post_generation
    def versioned_object_id(self, create, _):
        if not create or self.versioned_object_id:
            return

        self.versioned_object = self
        sync_latest_version(self)

    @factory.post_generation
    def sources(self, create, extracted):
        if not create:
            return

        self.sources.add(self.parent)

        if extracted:
            for source in extracted:
                self.sources.add(source)

    @factory.post_generation
    def names(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted:
            if isinstance(extracted, int):
                ConceptNameFactory.create_batch(size=extracted, concept=self, **kwargs)
            elif isinstance(extracted, (list, tuple, set)):
                for locale in extracted:
                    locale.concept = self
                    locale.save()

            sync_latest_version(self)

    @factory.post_generation
    def descriptions(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted:
            if isinstance(extracted, int):
                ConceptDescriptionFactory.create_batch(size=extracted, concept=self, **kwargs)
            elif isinstance(extracted, (list, tuple, set)):
                for locale in extracted:
                    locale.concept = self
                    locale.save()
            sync_latest_version(self)


class ConceptNameFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ConceptName

    name = Sequence("name{}".format)  # pylint: disable=consider-using-f-string
    type = "FULLY_SPECIFIED"
    locale = "en"
    locale_preferred = False


class ConceptDescriptionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ConceptDescription

    name = Sequence("name{}".format)  # pylint: disable=consider-using-f-string
    type = "Description"
    locale = "en"
    locale_preferred = False
