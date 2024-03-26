import json

from django_elasticsearch_dsl import Document, fields
from django_elasticsearch_dsl.registries import registry
from pydash import get

from core.common.utils import jsonify_safe, flatten_dict
from core.orgs.models import Organization
from core.url_registry.models import URLRegistry


@registry.register_document
class URLRegistryDocument(Document):
    class Index:
        name = 'url_registries'
        settings = {'number_of_shards': 1, 'number_of_replicas': 0}

    namespace = fields.TextField(attr='namespace')
    url = fields.TextField(attr='url')
    _url = fields.KeywordField(attr='url')
    name = fields.TextField(attr='name')
    _name = fields.KeywordField(attr='name', normalizer='lowercase')
    extras = fields.ObjectField(dynamic=True)
    last_update = fields.DateField(attr='updated_at')
    updated_by = fields.KeywordField(attr='updated_by.username')
    owner = fields.KeywordField(attr='owner.mnemonic', normalizer='lowercase')
    owner_type = fields.KeywordField(attr='owner_type')
    owner_url = fields.KeywordField(attr='owner_url')
    repo = fields.TextField()
    repo_owner_type = fields.KeywordField(attr='repo.parent_resource_type')
    repo_owner = fields.KeywordField(attr='repo.parent_resource', normalizer='lowercase')

    class Django:
        model = URLRegistry
        fields = ['is_active']

    @staticmethod
    def get_match_phrase_attrs():
        return ['_url', '_name', 'namespace', 'repo', 'repo_owner']

    @staticmethod
    def get_exact_match_attrs():
        return {
            'url': {
                'boost': 4,
            },
            'namespace': {
                'boost': 3.5,
            },
            'name': {
                'boost': 3,
            }
        }

    @staticmethod
    def get_wildcard_search_attrs():
        return {
            'url': {
                'boost': 1,
                'wildcard': True,
                'lower': True
            },
            'namespace': {
                'boost': 0.8,
                'wildcard': True,
                'lower': True
            },
            'name': {
                'boost': 0.6,
                'wildcard': True,
                'lower': True
            }
        }

    @staticmethod
    def get_fuzzy_search_attrs():
        return {
            'namespace': {
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
    def prepare_repo(instance):
        return get(instance, 'repo.mnemonic')