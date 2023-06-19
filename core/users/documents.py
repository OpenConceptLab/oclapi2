import json

from django_opensearch_dsl import Document, fields
from django_opensearch_dsl.registries import registry

from core.common.utils import jsonify_safe, flatten_dict
from core.users.models import UserProfile


@registry.register_document
class UserProfileDocument(Document):
    class Index:
        name = 'user_profiles'
        settings = {'number_of_shards': 1, 'number_of_replicas': 0}

    last_update = fields.DateField(attr='updated_at')
    date_joined = fields.DateField(attr='created_at')
    username = fields.KeywordField(attr='username', normalizer='lowercase')
    location = fields.KeywordField(attr='location', normalizer='lowercase')
    company = fields.KeywordField(attr='company', normalizer='lowercase')
    name = fields.KeywordField(attr='name', normalizer='lowercase')
    extras = fields.ObjectField(dynamic=True)
    org = fields.ListField(fields.KeywordField())

    class Django:
        model = UserProfile
        fields = [
            'is_active',
            'is_superuser',
            'is_staff',
        ]

    @staticmethod
    def get_boostable_search_attrs():
        return {
            'username': {
                'boost': 5,
                'lower': True,
                'wildcard': True
            },
            'name': {
                'boost': 3,
                'lower': True,
                'wildcard': True
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
    def prepare_org(instance):
        return list(instance.organizations.values_list('mnemonic', flat=True))
