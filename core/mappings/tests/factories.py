import factory
from factory import SubFactory

from core.concepts.tests.factories import ConceptFactory
from core.mappings.constants import SAME_AS
from core.mappings.models import Mapping
from core.sources.tests.factories import SourceFactory


class MappingFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Mapping

    parent = SubFactory(SourceFactory)
    from_concept = SubFactory(ConceptFactory)
    to_concept = SubFactory(ConceptFactory)
    map_type = SAME_AS

    @factory.post_generation
    def sources(self, create, extracted):
        if not create:
            return

        self.sources.add(self.parent)

        if extracted:
            for source in extracted:
                self.sources.add(source)
