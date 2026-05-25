
from pydash import get
from rest_framework import serializers
from rest_framework.fields import CharField, DateTimeField, IntegerField, FileField

from core.common.constants import DEFAULT_ACCESS_TYPE, INCLUDE_SUMMARY, INCLUDE_LOGS
from core.common.utils import get_truthy_values
from core.map_projects.models import MapProject


class MapProjectCreateUpdateSerializer(serializers.ModelSerializer):
    id = IntegerField(read_only=True)
    file = FileField(write_only=True, required=False)
    user_id = IntegerField(write_only=True, required=False)
    organization_id = IntegerField(write_only=True, required=False)
    input_file_name = CharField(required=False)

    class Meta:
        model = MapProject
        fields = [
            'id', 'name', 'input_file_name', 'matches', 'columns',
            'created_by', 'updated_by', 'created_at', 'updated_at', 'url', 'is_active',
            'public_access', 'file', 'user_id', 'organization_id', 'description',
            'target_repo_url', 'include_retired', 'score_configuration',
            'filters', 'candidates', 'algorithms', 'lookup_config', 'analysis', 'encoder_model',
            'prompt_template_key', 'prompt_output_locale', 'input_locales', 'use_lexical_variants',
        ]

    def validate_prompt_output_locale(self, value):
        if not value or value == 'auto':
            return value
        import re
        if not re.match(r'^[a-z]{2,3}(-[A-Z]{2})?$', value):
            raise serializers.ValidationError(
                'Invalid locale. Use "auto" or a BCP-47 code like "en" or "pt-BR".'
            )
        return value

    def validate_input_locales(self, value):
        if not value:
            return value or []
        import re
        for item in value:
            if not item:
                continue
            if not re.match(r'^[a-z]{2,3}(-[A-Z]{2})?$', item):
                raise serializers.ValidationError(
                    f'Invalid locale "{item}". Use BCP-47 codes like "en" or "pt-BR".'
                )
        return value

    def prepare_object(self, validated_data, instance=None, file=None):
        instance = instance or MapProject()
        instance.public_access = validated_data.get('public_access', instance.public_access or DEFAULT_ACCESS_TYPE)
        matches = validated_data.get('matches', False)
        if matches is not False:
            instance.matches = matches
        columns = validated_data.get('columns', False)
        if columns is not False:
            instance.columns = columns
        for attr in [
            'name', 'description', 'extras', 'target_repo_url', 'include_retired',
            'score_configuration', 'filters', 'candidates', 'algorithms', 'lookup_config', 'analysis',
            'encoder_model', 'prompt_template_key', 'prompt_output_locale', 'input_locales',
            'use_lexical_variants',
        ]:
            setattr(instance, attr, validated_data.get(attr, get(instance, attr)))
        if not instance.id:
            for attr in ['organization_id', 'user_id']:
                setattr(instance, attr, validated_data.get(attr, get(instance, attr)))
        if file:
            instance.input_file_name = file.name
        return instance

    def create(self, validated_data):
        file = validated_data.get('file', None)
        instance = self.prepare_object(validated_data)
        user = self.context['request'].user
        if file:
            instance.input_file_name = file.name
        errors = MapProject.persist_new(instance, user, input_file=file)
        self._errors.update(errors)
        return instance

    def update(self, instance, validated_data):
        data = MapProject.format_request_data(validated_data)
        file = data.get('file', None)
        instance = self.prepare_object(data, instance, file)
        user = self.context['request'].user
        if file:
            instance.input_file_name = file.name
        errors = MapProject.persist_changes(instance, user, input_file=file)
        self._errors.update(errors)
        return instance


class MapProjectSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = MapProject
        fields = ['id', 'summary', 'url']


class MapProjectListSerializer(serializers.ModelSerializer):
    created_by = CharField(source='created_by.username', read_only=True)
    updated_by = CharField(source='updated_by.username', read_only=True)
    created_at = DateTimeField(read_only=True)
    updated_at = DateTimeField(read_only=True)
    id = IntegerField(read_only=True)
    owner = CharField(source='parent.mnemonic', read_only=True)
    owner_type = CharField(source='parent.resource_type', read_only=True)
    owner_url = CharField(source='parent.uri', read_only=True)

    class Meta:
        model = MapProject
        fields = [
            'id', 'name', 'created_by', 'updated_by', 'created_at', 'updated_at',
            'url', 'is_active', 'owner_type', 'owner', 'owner_url'
        ]


class MapProjectConfigurationsSerializer(serializers.ModelSerializer):
    class Meta:
        model = MapProject
        fields = [
            'id', 'url', 'name'
        ] + MapProject.CONFIGURATION_FIELDS


class MapProjectSerializer(MapProjectConfigurationsSerializer):
    created_by = CharField(source='created_by.username', read_only=True)
    updated_by = CharField(source='updated_by.username', read_only=True)
    owner = CharField(source='parent.mnemonic', read_only=True)
    owner_type = CharField(source='parent.resource_type', read_only=True)
    owner_url = CharField(source='parent.uri', read_only=True)
    created_at = DateTimeField(read_only=True)
    updated_at = DateTimeField(read_only=True)
    id = IntegerField(read_only=True)

    class Meta:
        model = MapProject
        fields = MapProjectConfigurationsSerializer.Meta.fields + [
            'name', 'input_file_name',
            'created_by', 'updated_by', 'created_at', 'updated_at', 'is_active',
            'owner', 'owner_type', 'owner_url', 'public_access',
            'summary', 'logs', 'include_retired',
            'candidates', 'analysis',
        ]

    def __init__(self, *args, **kwargs):
        params = get(kwargs, 'context.request.query_params')

        self.query_params = {}
        if params:
            self.query_params = params if isinstance(params, dict) else params.dict()
        self.include_summary = self.query_params.get(INCLUDE_SUMMARY) in get_truthy_values()
        self.include_logs = self.query_params.get(INCLUDE_LOGS) in get_truthy_values()

        try:
            if not self.include_summary:
                self.fields.pop('summary', None)
                self.fields.pop('logs', None)
        except:  # pylint: disable=bare-except
            pass

        super().__init__(*args, **kwargs)


class MapProjectDetailSerializer(MapProjectSerializer):
    class Meta:
        model = MapProject
        fields = MapProjectSerializer.Meta.fields + ['file_url', 'matches', 'columns']


class MapProjectLogsSerializer(serializers.ModelSerializer):
    class Meta:
        model = MapProject
        fields = ['id', 'logs', 'url']
