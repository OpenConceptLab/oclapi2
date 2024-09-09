from celery import states
from pydash import get
from rest_framework.fields import CharField, SerializerMethodField, JSONField
from rest_framework.serializers import ModelSerializer
from django.utils import timezone

from core.importers.importer import ImportTask
from core.tasks.models import Task


class TaskBriefSerializer(ModelSerializer):
    task = CharField(source='id')
    queue = CharField(source='queue_name')

    class Meta:
        model = Task
        fields = ('id', 'state', 'name', 'queue', 'username', 'task')

    def __init__(self, *args, **kwargs):  # pylint: disable=too-many-branches
        request = get(kwargs, 'context.request')
        params = get(request, 'query_params')
        self.view_kwargs = get(kwargs, 'context.view.kwargs', {})

        self.query_params = params.dict() if params else {}
        self.result_type = self.query_params.get('result', None) or 'summary'

        super().__init__(*args, **kwargs)

    def get_result(self, obj):
        if self.include_result:
            return obj.json_result
        return None

    def to_representation(self, instance):
        if instance.result_all:  # If the task completed, determine if there is an import task
            import_task = ImportTask.import_task_from_json(instance.result_all)
            if import_task:  # adjust results based on the import task
                if instance.state in states.READY_STATES and instance.state not in states.EXCEPTION_STATES:
                    instance.result = import_task.model_dump_json(include={'json', 'report', 'detailed_summary'})
                    instance.state = import_task.import_async_result.state
                    if instance.state is states.PENDING:
                        instance.state = states.STARTED
                    instance.finished_at = import_task.time_finished
                    if not instance.finished_at:
                        instance.updated_at = timezone.now()
                    instance.summary = {'total': instance.json_result.get('summary').get('total'), 'processed':
                                        instance.json_result.get('summary').get('processed'),
                                        'dependencies': instance.json_result.get('summary').get('dependencies')}

        ret = super().to_representation(instance)
        return ret


class TaskListSerializer(TaskBriefSerializer):
    class Meta:
        model = Task
        fields = TaskBriefSerializer.Meta.fields + (
            'created_at', 'started_at', 'finished_at', 'runtime', 'summary', 'children', 'message'
        )


class TaskDetailSerializer(TaskListSerializer):
    result = SerializerMethodField()
    report = JSONField(read_only=True, source='report_result')

    class Meta:
        model = Task
        fields = TaskListSerializer.Meta.fields + (
            'report', 'result', 'kwargs', 'error_message', 'traceback', 'retry'
        )

    def __init__(self, *args, **kwargs):  # pylint: disable=too-many-branches
        request = get(kwargs, 'context.request')
        params = get(request, 'query_params')
        self.query_params = params.dict() if params else {}
        self.result_type = self.query_params.get('result', None)

        super().__init__(*args, **kwargs)

    def to_representation(self, instance):
        data = super().to_representation(instance)

        if self.result_type not in ['json', 'all']:
            data.pop('result', None)

        return data

    def get_result(self, obj):
        if self.result_type == 'json':
            return obj.json_result
        if self.result_type == 'all':
            return obj.result_all
        return None


class TaskResultSerializer(TaskDetailSerializer):
    result = JSONField(read_only=True, source='result_all')
