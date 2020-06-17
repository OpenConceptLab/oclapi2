import factory
from factory import Sequence, SubFactory

from core.concepts.models import Concept, LocalizedText
from core.sources.tests.factories import SourceFactory


class LocalizedTextFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = LocalizedText

    name = Sequence("name-{}".format)
    type = Sequence("locale-type-{}".format)
    locale = Sequence("locale-{}".format)


class ConceptFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Concept

    mnemonic = Sequence("concept-{}".format)
    name = Sequence("concept-{}".format)
    version = Sequence("version-{}".format)
    parent = SubFactory(SourceFactory)

    @factory.post_generation
    def sources(self, create, extracted):
        if not create:
            return

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
