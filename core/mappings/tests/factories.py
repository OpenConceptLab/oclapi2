import factory
from factory import SubFactory

from core.concepts.tests.factories import ConceptFactory
from core.mappings.constants import SAME_AS
from core.mappings.models import Mapping
from core.sources.tests.factories import SourceFactory


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

    @factory.post_generation
    def versioned_object_id(self, create, _):
        if not create or self.versioned_object_id:
            return

        self.versioned_object = self
        self.save()
        sync_latest_version(self)
