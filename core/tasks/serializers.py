from pydash import get
from rest_framework.fields import CharField, SerializerMethodField, JSONField
from rest_framework.serializers import ModelSerializer

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
