from django_elasticsearch_dsl import Document, fields
from django_elasticsearch_dsl.registries import registry

from core.common.constants import HEAD
from core.concepts.models import Concept


@registry.register_document
class ConceptDocument(Document):
    class Index:
        name = 'concepts'
        settings = {'number_of_shards': 1, 'number_of_replicas': 0}

    id = fields.TextField(attr='mnemonic')
    name = fields.KeywordField(
        attr='display_name',
        normalizer="lowercase",
    )
    last_update = fields.DateField(attr='updated_at')
    locale = fields.ListField(fields.TextField())
    source = fields.TextField(attr='parent_resource')
    owner = fields.TextField(attr='owner_name')
    owner_type = fields.TextField(attr='owner_type')
    source_version = fields.ListField(fields.IntegerField())
    collection_version = fields.ListField(fields.IntegerField())
    collection = fields.ListField(fields.IntegerField())
    public_can_view = fields.BooleanField(attr='public_can_view')

    class Django:
        model = Concept
        fields = [
            'datatype',
            'version',
            'is_latest_version',
            'retired',
            'is_active',
            'concept_class'
        ]

    @staticmethod
    def prepare_locale(instance):
        return list(
            instance.names.filter(locale__isnull=False).distinct('locale').values_list('locale', flat=True)
        )

    @staticmethod
    def prepare_source_version(instance):
        return list(instance.sources.values_list('id', flat=True))

    @staticmethod
    def prepare_collection_version(instance):
        return list(instance.collection_set.values_list('id', flat=True))

    @staticmethod
    def prepare_collection(instance):
        from core.collections.models import Collection
        return list(
            Collection.objects.filter(
                version=HEAD,
                mnemonic__in=instance.collection_set.values_list('mnemonic', flat=True)
            ).distinct('id').values_list('id', flat=True)
        )
