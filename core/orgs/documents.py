from django_elasticsearch_dsl import Document, fields
from django_elasticsearch_dsl.registries import registry

from core.common.utils import jsonify_safe, flatten_dict
from core.orgs.models import Organization


@registry.register_document
class OrganizationDocument(Document):
    class Index:
        name = 'organizations'
        settings = {'number_of_shards': 1, 'number_of_replicas': 0}

    last_update = fields.DateField(attr='updated_at')
    public_can_view = fields.BooleanField(attr='public_can_view')
    name = fields.KeywordField(attr='name', normalizer="lowercase")
    mnemonic = fields.KeywordField(attr='mnemonic', normalizer="lowercase")
    extras = fields.ObjectField(dynamic=True)
    user = fields.ListField(fields.KeywordField())

    class Django:
        model = Organization
        fields = [
            'is_active',
            'company',
            'location',
        ]

    @staticmethod
    def get_boostable_search_attrs():
        return dict(mnemonic=dict(boost=5), name=dict(boost=4))

    @staticmethod
    def prepare_extras(instance):
        value = {}

        if instance.extras:
            value = jsonify_safe(instance.extras)
            if isinstance(value, dict):
                value = flatten_dict(value)

        return value or {}

    @staticmethod
    def prepare_user(instance):
        return list(instance.members.values_list('username', flat=True))
