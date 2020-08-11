from django_elasticsearch_dsl import Document, fields
from django_elasticsearch_dsl.registries import registry

from core.mappings.models import Mapping


@registry.register_document
class MappingDocument(Document):
    class Index:
        name = 'mappings'
        settings = {'number_of_shards': 1, 'number_of_replicas': 0}

    class Django:
        model = Mapping
        fields = [
            'external_id', 'retired', 'is_latest_version', 'is_active'
        ]

    lastUpdate = fields.DateField(attr='updated_at')
    mapType = fields.TextField(attr='map_type')
    source = fields.TextField(attr='source')
    ownerType = fields.TextField(attr='owner_type')
    fromConcept = fields.ListField(fields.TextField())
    toConcept = fields.ListField(fields.TextField())
    concept = fields.ListField(fields.TextField())
    owner = fields.TextField(attr='owner_name')

    @staticmethod
    def prepare_fromConcept(instance):
        return [instance.from_concept_url, instance.from_concept_code, instance.from_concept_name]

    @staticmethod
    def prepare_toConcept(instance):
        return [instance.get_to_concept_code(), instance.get_to_concept_name()]

    def prepare_concept(self, instance):
        return self.prepare_fromConcept(instance) + self.prepare_toConcept(instance)
