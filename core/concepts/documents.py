from django_elasticsearch_dsl import Document
from django_elasticsearch_dsl.registries import registry

from core.concepts.models import Concept


@registry.register_document
class ConceptDocument(Document):
    class Index:
        name = 'concepts'
        settings = {'number_of_shards': 1, 'number_of_replicas': 0}

    class Django:
        model = Concept
        fields = [
            'mnemonic',
            'concept_class',
            'datatype',
            'version',
        ]
