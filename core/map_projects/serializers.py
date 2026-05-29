
from django.utils import timezone
from pydash import get
from rest_framework import serializers
from rest_framework.fields import CharField, DateTimeField, IntegerField, FileField

from core.common.constants import DEFAULT_ACCESS_TYPE, INCLUDE_SUMMARY, INCLUDE_LOGS
from core.common.utils import get_truthy_values
from core.map_projects.models import MapProject, AutomatchRun


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


class AutomatchRunListSerializer(serializers.ModelSerializer):
    id = IntegerField(read_only=True)
    url = CharField(read_only=True)
    map_project_id = IntegerField(read_only=True)
    parent_run_id = IntegerField(read_only=True)
    started_by = serializers.SerializerMethodField()

    class Meta:
        model = AutomatchRun
        fields = [
            'id', 'url', 'map_project_id', 'started_at', 'completed_at',
            'intended_rows', 'completed_rows', 'failed_rows',
            'completion_status', 'trigger_source', 'parent_run_id', 'started_by',
        ]

    @staticmethod
    def get_started_by(obj):
        # started_by is nullable (on_delete=SET_NULL), so guard the username lookup.
        return obj.started_by.username if obj.started_by_id else None


class AutomatchRunDetailSerializer(AutomatchRunListSerializer):
    class Meta:
        model = AutomatchRun
        fields = AutomatchRunListSerializer.Meta.fields + [
            'config_snapshot', 'client_user_agent', 'client_ip', 'created_at', 'updated_at',
        ]


class AutomatchRunCreateSerializer(serializers.ModelSerializer):
    id = IntegerField(read_only=True)
    url = CharField(read_only=True)
    intended_rows = IntegerField(min_value=0)
    trigger_source = serializers.ChoiceField(choices=AutomatchRun.TRIGGER_SOURCES)
    parent_run = serializers.PrimaryKeyRelatedField(
        queryset=AutomatchRun.objects.all(), required=False, allow_null=True)

    class Meta:
        model = AutomatchRun
        # A run always starts 'running' (the model default); completion is reported
        # later via PATCH, so completion_status is intentionally not settable here.
        fields = ['id', 'url', 'intended_rows', 'config_snapshot', 'trigger_source', 'parent_run']

    def validate(self, attrs):
        # A retry must link to a run within the SAME project — both a data-integrity
        # rule and an authorization guard (a caller must not chain a run onto another
        # project's run). See ocl_online#105 OQ3 (re-run semantics) / OQ2 (authz).
        parent_run = attrs.get('parent_run')
        map_project = self.context.get('map_project')
        if parent_run and map_project and parent_run.map_project_id != map_project.id:
            raise serializers.ValidationError(
                {'parent_run': 'parent_run must belong to the same map project.'})
        return attrs


class AutomatchRunUpdateSerializer(serializers.ModelSerializer):
    id = IntegerField(read_only=True)
    url = CharField(read_only=True)
    completion_status = serializers.ChoiceField(choices=AutomatchRun.COMPLETION_STATUSES, required=False)

    class Meta:
        model = AutomatchRun
        # Only lifecycle fields are mutable post-creation. intended_rows,
        # config_snapshot, trigger_source, parent_run and started_by are an
        # immutable run-start snapshot and are intentionally omitted (ocl_online#105 OQ3).
        fields = [
            'id', 'url', 'completed_rows', 'failed_rows', 'completion_status', 'completed_at',
        ]

    def update(self, instance, validated_data):
        # Stamp completed_at the first time a run reaches a terminal status, unless
        # the client set it explicitly. Already-completed runs keep their timestamp.
        new_status = validated_data.get('completion_status', instance.completion_status)
        if (new_status in AutomatchRun.TERMINAL_STATUSES
                and 'completed_at' not in validated_data
                and instance.completed_at is None):
            validated_data['completed_at'] = timezone.now()
        return super().update(instance, validated_data)
