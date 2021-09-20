import factory
from factory import Sequence, SubFactory

from core.common.constants import HEAD
from core.concepts.models import Concept, LocalizedText
from core.sources.tests.factories import OrganizationSourceFactory


def sync_latest_version(self):
    latest_version = self.get_latest_version()
    has_names = self.names.exists()
    if not latest_version:
        latest_version = self.clone()
        latest_version.save()
        self.is_latest_version = False
        self.save()
        latest_version.version = latest_version.id
        latest_version.save()
        latest_version.sources.add(latest_version.parent)
    if latest_version and has_names and not latest_version.names.exists():
        latest_version.cloned_names = [name.clone() for name in self.names.all()]
        latest_version.set_locales()


class LocalizedTextFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = LocalizedText

    name = Sequence("name{}".format)  # pylint: disable=consider-using-f-string
    type = "FULLY_SPECIFIED"
    locale = "en"
    locale_preferred = False


class ConceptFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Concept

    mnemonic = Sequence("concept{}".format)  # pylint: disable=consider-using-f-string
    name = Sequence("concept{}".format)  # pylint: disable=consider-using-f-string
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
    def names(self, create, extracted):
        if not create:
            return

        if extracted:
            for name in extracted:
                self.names.add(name)
            sync_latest_version(self)

    @factory.post_generation
    def descriptions(self, create, extracted):
        if not create:
            return

        if extracted:
            for desc in extracted:
                self.descriptions.add(desc)

    @factory.post_generation
    def cloned_names(self, create, extracted):
        if not create:
            return

        if extracted:
            for name in extracted:
                self.cloned_names.add(name)

    @factory.post_generation
    def cloned_descriptions(self, create, extracted):
        if not create:
            return

        if extracted:
            for desc in extracted:
                self.cloned_descriptions.add(desc)
