from datetime import datetime

from pydash import get
from rest_framework import serializers
from rest_framework.fields import CharField, JSONField, IntegerField, DateTimeField, ChoiceField, URLField, \
    BooleanField, SerializerMethodField


class CommonSerializer(serializers.Serializer):

    def update(self, instance, validated_data):
        pass

    def create(self, validated_data):
        pass


class BundleLinkSerializer(CommonSerializer):
    relation = CharField(read_only=True)
    url = URLField(read_only=True)


class BundleMetaSerializer(CommonSerializer):
    lastUpdated = DateTimeField(read_only=True, default=datetime.now)


class BundleExtensionSerializer(CommonSerializer):
    url = URLField()
    valueBase65Binary = CharField(required=False)
    valueBoolean = BooleanField(required=False)
    # See https://www.hl7.org/fhir/extensibility.html for other possible fields
    valueInteger = IntegerField(required=False)


class BundleEntrySerializer(CommonSerializer):
    fullUrl = CharField(required=False)
    resource = JSONField()
    search = JSONField(required=False)


class FHIRBundleSerializer(CommonSerializer):
    # See https://www.hl7.org/fhir/bundle.html#tabs-json for all possible fields
    resourceType = SerializerMethodField(method_name='get_resource_type')
    meta = BundleMetaSerializer(read_only=True, required=False)
    type = ChoiceField(
        choices=['document', 'message', 'transaction', 'transaction-response', 'batch', 'batch-response', 'history',
                 'searchset', 'collection'])
    total = IntegerField(required=False)
    entry = BundleEntrySerializer(required=False, many=True)
    link = SerializerMethodField()
    extension = BundleExtensionSerializer(required=False)

    @classmethod
    def get_resource_type(cls, _obj):
        return 'Bundle'

    @classmethod
    def convert_to_entry(cls, resources, base_url):
        """
        Searchset entries: each resource a match with its fullUrl (base_url is the base and resource type), and a
        resource that couldn't be represented (an OperationOutcome) an outcome.
        """
        entry = []
        for resource in resources:
            if resource.get('resourceType') == 'OperationOutcome':
                entry.append({'resource': resource, 'search': {'mode': 'outcome'}})
            else:
                entry.append(
                    {'fullUrl': f"{base_url}/{resource['id']}", 'resource': resource, 'search': {'mode': 'match'}})
        return entry

    def get_link(self, _):
        """self, previous and next, for the page the paginator holds; only self without one (_count=0)"""
        paginator = self.context.get('paginator')
        if paginator is None:
            return BundleLinkSerializer(
                [{'relation': 'self', 'url': self.context['request'].build_absolute_uri()}], many=True).data
        links = [{'relation': 'self', 'url': paginator.get_current_page_url()}]
        if paginator.has_previous():
            links.append({'relation': 'previous', 'url': paginator.get_previous_page_url()})
        if paginator.has_next():
            links.append({'relation': 'next', 'url': paginator.get_next_page_url()})
        return BundleLinkSerializer(links, many=True).data


# TODO: Adjust BundleSerializer to be FHIR compliant based on FhirBundleSerializer and remove FhirBundleSerializer
class BundleSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    resourceType = CharField(read_only=True, source='resource_type')
    type = ChoiceField(
        choices=['document', 'message', 'transaction', 'transaction-response', 'batch', 'batch-response', 'history',
                 'searchset', 'collection'], source='bundle_type')
    meta = BundleMetaSerializer(read_only=True, source='timestamp')
    total = IntegerField(read_only=True)
    truncated = BooleanField(read_only=True)
    entry = JSONField(read_only=True, source='entries')
    requested_url = CharField(read_only=True)
    repo_version_url = CharField(read_only=True)

    class Meta:
        fields = (
            'resourceType', 'type', 'meta', 'total', 'truncated', 'concepts', 'mappings', 'entry',
            'requested_url', 'repo_version_url'
        )

    def __init__(self, *args, **kwargs):
        request = get(kwargs, 'context.request')
        params = get(request, 'query_params')

        self.query_params = params.dict() if params else {}
        is_hierarchy_response = self.query_params.get('view', '').lower() == 'hierarchy'

        try:
            if is_hierarchy_response:
                self.fields.pop('concepts')
                self.fields.pop('mappings')
                self.fields.pop('total')
        except:  # pylint: disable=bare-except
            pass

        super().__init__(*args, **kwargs)
