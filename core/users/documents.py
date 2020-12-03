from django_elasticsearch_dsl import Document, fields
from django_elasticsearch_dsl.registries import registry

from core.users.models import UserProfile


@registry.register_document
class UserProfileDocument(Document):
    class Index:
        name = 'user_profiles'
        settings = {'number_of_shards': 1, 'number_of_replicas': 0}

    date_joined = fields.DateField(attr='created_at')
    username = fields.KeywordField(attr='username', normalizer='lowercase')
    location = fields.KeywordField(attr='location', normalizer='lowercase')
    company = fields.KeywordField(attr='company', normalizer='lowercase')
    name = fields.KeywordField(attr='name', normalizer='lowercase')
    extras = fields.ObjectField()
    org = fields.ListField(fields.KeywordField())

    class Django:
        model = UserProfile
        fields = [
            'is_active'
        ]

    @staticmethod
    def prepare_extras(instance):
        return instance.extras or {}

    @staticmethod
    def prepare_org(instance):
        return list(instance.organizations.values_list('mnemonic', flat=True))
