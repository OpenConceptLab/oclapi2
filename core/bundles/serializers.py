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
    lastUpdated = DateTimeField(read_only=True, default=datetime.now())


class BundleExtensionSerializer(CommonSerializer):
    url = URLField()
    valueBase65Binary = CharField(required=False)
    valueBoolean = BooleanField(required=False)
    # See https://www.hl7.org/fhir/extensibility.html for other possible fields
    valueInteger = IntegerField(required=False)


class BundleEntrySerializer(CommonSerializer):
    resource = JSONField()


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
    def convert_to_entry(cls, resources):
        entry = []
        for resource in resources:
            entry.append({'resource': resource})
        return entry

    def get_link(self, _):
        paginator = self.context.get('paginator')
        links = [dict(relation='self', url=paginator.get_current_page_url())]
        if paginator.has_previous():
            links.append(dict(relation='prev', url=paginator.get_previous_page_url()))
        if paginator.has_next():
            links.append(dict(relation='next', url=paginator.get_next_page_url()))
        return BundleLinkSerializer(links, many=True).data


# TODO: Adjust BundleSerializer to be FHIR compliant based on FhirBundleSerializer and remove FhirBundleSerializer
class BundleSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    resourceType = CharField(read_only=True, source='resource_type')
    type = ChoiceField(
        choices=['document', 'message', 'transaction', 'transaction-response', 'batch', 'batch-response', 'history',
                 'searchset', 'collection'], source='bundle_type')
    meta = BundleMetaSerializer(read_only=True, source='timestamp')
    total = IntegerField(read_only=True)
    entry = JSONField(read_only=True, source='entries')
    requested_url = CharField(read_only=True)
    repo_url = CharField(read_only=True)

    class Meta:
        fields = (
            'resourceType', 'type', 'meta', 'total', 'concepts', 'mappings', 'entry', 'requested_url', 'repo_url'
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
