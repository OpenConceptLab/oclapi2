import factory
from factory import Sequence, SubFactory

from core.common.constants import HEAD
from core.concepts.models import Concept, LocalizedText
from core.sources.tests.factories import SourceFactory


class LocalizedTextFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = LocalizedText

    name = Sequence("name{}".format)
    type = "FULLY_SPECIFIED"
    locale = "en"
    locale_preferred = False


class ConceptFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Concept

    mnemonic = Sequence("concept{}".format)
    name = Sequence("concept{}".format)
    version = HEAD
    parent = SubFactory(SourceFactory)
    concept_class = "Diagnosis"
    datatype = "None"
    is_latest_version = True

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
