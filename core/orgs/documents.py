import json

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
    name = fields.TextField(attr='name')
    _name = fields.KeywordField(attr='name', normalizer='lowercase')
    mnemonic = fields.TextField(attr='mnemonic')
    _mnemonic = fields.KeywordField(attr='mnemonic', normalizer='lowercase')
    extras = fields.ObjectField(dynamic=True)
    user = fields.ListField(fields.TextField())

    class Django:
        model = Organization
        fields = [
            'company',
            'location',
        ]

    @staticmethod
    def get_match_phrase_attrs():
        return ['name']

    @staticmethod
    def get_exact_match_attrs():
        return {
            'mnemonic': {
                'boost': 4,
            },
            'name': {
                'boost': 3.5,
            }
        }

    @staticmethod
    def get_wildcard_search_attrs():
        return {
            'mnemonic': {
                'boost': 1,
                'wildcard': True,
                'lower': True
            },
            'name': {
                'boost': 0.8,
                'wildcard': True,
                'lower': True
            }
        }

    @staticmethod
    def get_fuzzy_search_attrs():
        return {
            'name': {
                'boost': 0.8,
            }
        }

    @staticmethod
    def prepare_extras(instance):
        value = {}

        if instance.extras:
            value = jsonify_safe(instance.extras)
            if isinstance(value, dict):
                value = flatten_dict(value)

        if value:
            value = json.loads(json.dumps(value))
        return value or {}

    @staticmethod
    def prepare_user(instance):
        return list(instance.members.values_list('username', flat=True))
