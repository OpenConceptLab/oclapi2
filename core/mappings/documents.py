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
            'external_id', 'retired', 'is_latest_version', 'is_active', 'map_type',
        ]

    last_update = fields.DateField(attr='updated_at')
    source = fields.TextField(attr='source')
    owner_type = fields.TextField(attr='owner_type')
    from_concept = fields.ListField(fields.TextField())
    to_concept = fields.ListField(fields.TextField())
    concept = fields.ListField(fields.TextField())
    owner = fields.TextField(attr='owner_name')

    @staticmethod
    def prepare_from_concept(instance):
        return [instance.from_concept_url, instance.from_concept_code, instance.from_concept_name]

    @staticmethod
    def prepare_to_concept(instance):
        return [instance.get_to_concept_code(), instance.get_to_concept_name()]

    def prepare_concept(self, instance):
        return self.prepare_from_concept(instance) + self.prepare_to_concept(instance)
