from django_elasticsearch_dsl import Document, fields
from django_elasticsearch_dsl.registries import registry

from core.users.models import UserProfile


@registry.register_document
class UserProfileDocument(Document):
    class Index:
        name = 'user_profiles'
        settings = {'number_of_shards': 1, 'number_of_replicas': 0}

    date_joined = fields.DateField(attr='created_at')

    class Django:
        model = UserProfile
        fields = [
            'username',
            'company',
            'location',
            'is_active'
        ]
