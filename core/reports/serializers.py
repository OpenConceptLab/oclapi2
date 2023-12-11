from django.conf import settings
from pydash import get
from rest_framework import serializers
from rest_framework.fields import JSONField, DateField, SerializerMethodField

VERBOSE_ATTRS = ['source_versions', 'collection_versions', 'collection_references', 'concepts', 'mappings']


class MonthlyUsageReportSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    start = DateField(read_only=True)
    end = DateField(read_only=True)
    current_month_start = DateField(read_only=True)
    current_month_end = DateField(read_only=True)
    users = JSONField(read_only=True)
    organizations = JSONField(read_only=True)
    sources = JSONField(read_only=True)
    collections = JSONField(read_only=True)
    source_versions = JSONField(read_only=True)
    collection_versions = JSONField(read_only=True)
    collection_references = JSONField(read_only=True)
    concepts = JSONField(read_only=True)
    mappings = JSONField(read_only=True)
    current_month = JSONField(read_only=True)
    env = SerializerMethodField()

    class Meta:
        fields = (
            'env', 'start', 'end', 'users', 'organizations', 'sources', 'collections', 'current_month',
            'current_month_start', 'current_month_end',
            *VERBOSE_ATTRS
        )

    def __init__(self, *args, **kwargs):
        is_verbose = get(kwargs, 'context.is_verbose')

        if not is_verbose:
            for attr in VERBOSE_ATTRS:
                self.fields.pop(attr, None)

        super().__init__(*args, **kwargs)

    @staticmethod
    def get_env(_):
        return settings.ENV
