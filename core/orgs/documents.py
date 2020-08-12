from django_elasticsearch_dsl import Document, fields
from django_elasticsearch_dsl.registries import registry

from core.orgs.models import Organization


@registry.register_document
class OrganizationDocument(Document):
    class Index:
        name = 'organizations'
        settings = {'number_of_shards': 1, 'number_of_replicas': 0}

    lastUpdate = fields.DateField(attr='updated_at')
    public_can_view = fields.BooleanField(attr='public_can_view')

    class Django:
        model = Organization
        fields = [
            'is_active',
            'name',
            'company',
            'location',
        ]
